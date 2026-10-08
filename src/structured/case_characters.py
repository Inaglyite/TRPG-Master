"""Owner-authorized case cards: preview/export and explicit non-overwriting save."""

import copy
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.gameplay.character_library import (
    CARD_FORMAT,
    CARD_FORMAT_VERSION,
    MAX_CARD_BYTES,
    CharacterLibraryError,
    entry_summary,
    validate_card,
)
from src.gameplay.investigators import investigator_controller_user_id, investigator_entity
from src.storage.database import (
    CharacterLibraryEntry,
    GameCommand,
    World,
    WorldInvestigator,
    WorldState,
    new_id,
    session_scope,
    utcnow,
)

from .bootstrap import LOCAL_OPERATOR_USER_ID, local_player_investigator_ids
from .combat_inventory import project_combat_inventory
from .errors import StructuredError
from .ids import canonical_digest
from .principal import resolve_player_principal


def _missing():
    return CharacterLibraryError("not_found", "找不到可访问的结案角色。", 404)


def _source(session, owner_id, world_id, investigator_id, case_id):
    world = session.get(World, world_id)
    row = session.scalar(
        select(WorldState).where(WorldState.world_id == world_id).with_for_update()
    )
    if world is None or row is None:
        raise _missing()
    state = copy.deepcopy(row.state)
    if owner_id:
        try:
            allowed = resolve_player_principal(session, world_id, owner_id).investigator_ids
        except StructuredError as exc:
            raise _missing() from exc
    else:
        if world.created_by not in {None, "", LOCAL_OPERATOR_USER_ID}:
            raise _missing()
        allowed = local_player_investigator_ids(session, world_id, state)
        claim = session.scalar(
            select(WorldInvestigator).where(
                WorldInvestigator.world_id == world_id,
                WorldInvestigator.character_key == investigator_id,
                WorldInvestigator.status == "claimed",
            )
        )
        controller = investigator_controller_user_id(state, investigator_id)
        if (
            claim and claim.controller_user_id not in {None, "", LOCAL_OPERATOR_USER_ID}
        ) or controller not in {None, "", LOCAL_OPERATOR_USER_ID}:
            raise _missing()
    if investigator_id not in allowed:
        raise _missing()
    if (world.metadata_json or {}).get("execution_profile") != "structured_v1":
        raise CharacterLibraryError(
            "profile_mismatch", "此入口用于结构化结案凭证；旧模式沿用原履历流程。", 409
        )
    if not state.get("game_over"):
        raise CharacterLibraryError("case_not_settled", "案件尚未结算，不能提前保存结案奖励。", 409)
    receipt = (state.get("case_settlements") or {}).get(case_id, {}).get(investigator_id)
    if not isinstance(receipt, dict) or receipt.get("case", {}).get("case_id") != case_id:
        raise _missing()
    project_combat_inventory(state)
    sheet = investigator_entity(state, investigator_id)
    if not isinstance(sheet, dict):
        raise _missing()
    if isinstance(receipt.get("character_snapshot"), dict):
        sheet = copy.deepcopy(receipt["character_snapshot"])
    else:
        # Older receipts have no immutable card snapshot. Only the latest
        # settlement can safely use the current ending's sheet as a fallback.
        latest = max(
            (
                r.get("case", {}).get("completed_at", "")
                for case in state.get("case_settlements", {}).values()
                for r in case.values()
                if isinstance(r, dict)
            ),
            default="",
        )
        if receipt.get("case", {}).get("completed_at", "") != latest:
            raise CharacterLibraryError(
                "historical_card_missing",
                "此旧案件缺少结案角色快照，请打开对应存档后保存，避免混入后来的角色状态。",
                409,
            )
    return row, sheet, receipt


def _card(sheet, receipt, name):
    fields = (
        "name",
        "occupation",
        "age",
        "era",
        "attributes",
        "skills",
        "inventory",
        "credit_rating",
        "backstory",
        "psychological_profile",
        "portrait",
        "violence_stance",
    )
    raw = {key: copy.deepcopy(sheet[key]) for key in fields if key in sheet}
    if name is not None:
        raw["name"] = name
    raw["career"] = copy.deepcopy(receipt["career"])
    raw["derived"] = {"LUCK": sheet.get("luck", (sheet.get("derived") or {}).get("LUCK", 50))}
    raw["trpg_case_record"] = {
        "format": "trpg-case-record",
        "version": 1,
        "case": copy.deepcopy(receipt["case"]),
        "final_state": {
            key: copy.deepcopy(sheet[key])
            for key in ("hp", "max_hp", "san", "max_san", "mp", "max_mp", "conditions")
            if key in sheet
        },
    }
    card, errors, warnings = validate_card(raw)
    if errors:
        raise CharacterLibraryError(
            "invalid_card",
            "结案角色资料不完整或不符合角色卡格式，请在角色管理中核对。",
            400,
            errors,
        )
    if len(json.dumps(card, ensure_ascii=False).encode("utf-8")) > MAX_CARD_BYTES:
        raise CharacterLibraryError(
            "card_too_large", "结案角色卡超过角色库大小上限，未保存或截断任何履历。", 400
        )
    warnings.append(
        "结案时生命、理智与伤势记录已附在卡中；新冒险仍按现有建卡推导规则初始化，不表示本场角色被治疗。"
    )
    return card, warnings


def _identity(owner_id, investigator_id, receipt):
    digest = canonical_digest(
        {"owner": owner_id, "investigator_id": investigator_id, "receipt": receipt}
    )
    return "chcase_" + digest[:40], "case-card-save-" + digest[:40]


def preview_case_character(
    database_url,
    owner_id,
    *,
    world_id,
    investigator_id,
    case_id,
    expected_revision,
    name=None,
    receipt_digest=None,
):
    with session_scope(database_url) as session:
        row, sheet, receipt = _source(session, owner_id, world_id, investigator_id, case_id)
        # Preview/export reads the current authorized receipt. Its revision is
        # the frozen version that a subsequent save must compare, so a user can
        # recover from stale websocket state without editing a request by hand.
        card, warnings = _card(sheet, receipt, name)
        entry_id, _ = _identity(owner_id, investigator_id, receipt)
        existing = session.get(CharacterLibraryEntry, entry_id)
        return {
            "card": card,
            "warnings": warnings,
            "revision": int(row.revision),
            "receipt_digest": canonical_digest(receipt),
            "saved_entry": entry_summary(existing)
            if existing and existing.owner_user_id == owner_id
            else None,
        }


def export_case_character(database_url, owner_id, **source):
    preview = preview_case_character(database_url, owner_id, **source)
    if source.get("receipt_digest") and source["receipt_digest"] != preview["receipt_digest"]:
        raise CharacterLibraryError("revision_conflict", "结案凭证已变化，请重新查看后导出。", 409)
    return {
        "format": CARD_FORMAT,
        "format_version": CARD_FORMAT_VERSION,
        "exported_at": utcnow().isoformat(),
        "card": preview["card"],
    }


def save_case_character(
    database_url,
    owner_id,
    *,
    world_id,
    investigator_id,
    case_id,
    expected_revision,
    name=None,
    receipt_digest=None,
):
    entry_id = ""
    try:
        with session_scope(database_url) as session:
            row, sheet, receipt = _source(session, owner_id, world_id, investigator_id, case_id)
            if receipt_digest and receipt_digest != canonical_digest(receipt):
                raise CharacterLibraryError(
                    "revision_conflict", "结案凭证已变化，请重新查看后保存。", 409
                )
            entry_id, command_id = _identity(owner_id, investigator_id, receipt)
            existing = session.get(CharacterLibraryEntry, entry_id)
            if existing:
                if existing.owner_user_id != owner_id:
                    raise _missing()
                return {
                    "entry": entry_summary(existing),
                    "warnings": ["此结案副本已经保存；重命名请在角色管理中进行。"],
                    "deduplicated": True,
                }
            saved = session.scalar(
                select(GameCommand).where(
                    GameCommand.world_id == world_id, GameCommand.command_id == command_id
                )
            )
            if saved:
                raise CharacterLibraryError(
                    "saved_copy_deleted",
                    "此前保存的副本已删除；如需重新建卡，可导出后在角色管理中导入。",
                    409,
                )
            if int(row.revision) != expected_revision:
                raise CharacterLibraryError(
                    "revision_conflict", "世界状态已变化，请重新查看结案角色。", 409
                )
            card, warnings = _card(sheet, receipt, name)
            entry = CharacterLibraryEntry(
                id=entry_id, owner_user_id=owner_id, name=card["name"], card_json=card
            )
            session.add(entry)
            session.add(
                GameCommand(
                    id=new_id("cmdrow"),
                    world_id=world_id,
                    command_id=command_id,
                    kind="case_card_save",
                    payload={"investigator_id": investigator_id, "case_id": case_id},
                    payload_digest=canonical_digest(receipt),
                    principal={"kind": "player", "user_id": owner_id or LOCAL_OPERATOR_USER_ID},
                    status="committed",
                    revision=int(row.revision),
                    result={"entry_id": entry_id},
                )
            )
            session.flush()
            return {"entry": entry_summary(entry), "warnings": warnings, "deduplicated": False}
    except IntegrityError:
        # Concurrent retries hit the deterministic primary key. Re-authorize and
        # read the winner rather than overwriting it or returning a second award.
        with session_scope(database_url) as session:
            _source(session, owner_id, world_id, investigator_id, case_id)
            existing = session.get(CharacterLibraryEntry, entry_id)
            if existing and existing.owner_user_id == owner_id:
                return {"entry": entry_summary(existing), "warnings": [], "deduplicated": True}
        raise
