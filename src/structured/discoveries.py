"""Explicit authored discoveries: knowledge, custody and rule effects are separate.

No prose matching: callers select a catalog ID and rule index. A human keeper
judges contextual suitability; the engine verifies declared prerequisites and
real check receipts. Acquiring an authored physical object is an explicit act,
never a side effect of reading, sharing or merely granting information.
"""

import copy

from sqlalchemy import select

from src.gameplay.investigators import investigator_entity
from src.gameplay.percentile import REQUIRED_RANKS
from src.storage.database import CheckRequest, EventOutbox

from .errors import StructuredError
from .ids import new_stable_id
from .registries import ensure_item_registry


def require_actor(state: dict, investigator_id: str) -> None:
    sheet = investigator_entity(state, investigator_id)
    roster = (state.get("investigators") or {}).get(investigator_id, sheet)
    if not isinstance(sheet, dict):
        raise StructuredError("object_not_found", "调查员不存在。")
    for entry in (sheet, roster):
        if (
            not isinstance(entry, dict)
            or entry.get("hp", 0) <= 0
            or set(entry.get("conditions") or []) & {"dead", "dying", "unconscious"}
        ):
            raise StructuredError("invalid_action", "该调查员当前无法执行发现或使用行动。")


def authored_rule(state: dict, clue_id: str, index: int) -> tuple[dict, dict]:
    catalog = state.get("clue_catalog") or {}
    entry = catalog.get(clue_id) if isinstance(catalog, dict) else None
    rules = entry.get("discovery_rules") if isinstance(entry, dict) else None
    if not isinstance(rules, list) or type(index) is not int or not 0 <= index < len(rules):
        raise StructuredError("object_not_found", "模组未声明该发现规则。")
    rule = rules[index]
    if not isinstance(rule, dict):
        raise StructuredError("invalid_action", "模组发现规则格式无效。")
    return entry, rule


def required_flags(rule: dict) -> dict:
    values = rule.get("requires_flags") or []
    if isinstance(values, list) and all(isinstance(v, str) for v in values):
        return dict.fromkeys(values, True)
    if isinstance(values, dict):
        return values
    raise StructuredError("invalid_action", "模组前置条件格式无效。")


def condition_met(rule: dict, flags: dict, key: str, expected) -> bool:
    if isinstance(rule.get("requires_flags"), list):
        return bool(flags.get(key))
    return key in flags and type(flags[key]) is type(expected) and flags[key] == expected


def verify_discovery(
    state: dict, clue_id: str, index: int, actor: str, check_id: str, ctx, *, use: bool = False
) -> tuple[dict, dict]:
    require_actor(state, actor)
    entry, rule = authored_rule(state, clue_id, index)
    if (rule.get("intent") == "use") != use:
        raise StructuredError("invalid_action", "使用型发现必须通过真实持有物品的使用命令结算。")
    scene_id = str((state.get("current_scene") or {}).get("id") or "")
    scenes = entry.get("related_scenes") or []
    if scenes and scene_id not in scenes:
        raise StructuredError("unknown_target", "此发现不属于当前场景。")
    required = required_flags(rule)
    if any(
        not condition_met(rule, state.get("flags") or {}, key, value)
        for key, value in required.items()
    ):
        raise StructuredError("invalid_action", "此发现的模组前置条件尚未满足。")
    if rule.get("requires_success") is True:
        row = (
            ctx.session.execute(
                select(CheckRequest).where(
                    CheckRequest.world_id == ctx.world_id,
                    CheckRequest.check_request_id == check_id,
                )
            ).scalar_one_or_none()
            if ctx.session is not None and check_id
            else None
        )
        conditions = row.conditions or {} if row is not None else {}
        target = conditions.get("target") or {}
        ranks = {
            "regular_success": 1,
            "hard_success": 2,
            "extreme_success": 3,
            "critical_success": 4,
        }
        result = row.result or {} if row is not None else {}
        live_receipt = (
            ctx.session.scalar(
                select(EventOutbox.id)
                .where(
                    EventOutbox.world_id == ctx.world_id,
                    EventOutbox.event_type == "check_resolved",
                    EventOutbox.payload["check_request_id"].as_string() == check_id,
                )
                .limit(1)
            )
            if row is not None
            else None
        )
        if (
            row is None
            or live_receipt is None
            or row.status != "resolved"
            or row.investigator_id != actor
            or (
                row.skill != rule.get("skill")
                or result.get("outcome") != "success"
                or conditions.get("scene_id") != scene_id
                or target.get("id") != clue_id
                or ranks.get(result.get("level"), 0)
                < REQUIRED_RANKS.get(rule.get("difficulty", "regular"), 99)
            )
        ):
            raise StructuredError(
                "invalid_action", "需要同调查员、技能、场景和线索目标的已成功检定。"
            )
    return entry, rule


def verify_item_effect(state: dict, item: dict, entry: dict, rule: dict, ctx) -> None:
    # Historical modules have no item-to-rule binding. Do not reconstruct it
    # from target keywords: an authorized human explicitly selects the authored
    # effect and records suitability in basis. Agents cannot make this override.
    if getattr(ctx.principal, "kind", "") != "keeper":
        raise StructuredError("keeper_required", "未声明物品绑定的模组效果须由人类主持核对用法。")
    required = required_flags(rule)
    catalog = state.get("clue_catalog") or {}
    inventory = ensure_item_registry(state)["items"]
    sources = set()
    for clue_id, definition in catalog.items():
        if not isinstance(definition, dict) or not definition.get("granted_item"):
            continue
        effects = definition.get("flag_effects") or {}
        if not any(key in required and required[key] == value for key, value in effects.items()):
            continue
        sources.add(clue_id)
        if not any(
            physical.get("source_clue_id") == clue_id
            and physical.get("quantity", 0) > 0
            and (physical.get("holder") or {}).get("kind") == "investigator"
            for physical in inventory.values()
        ):
            raise StructuredError(
                "object_not_held", "效果所需的模组实物不在调查员身上；剧情标记不能代替持有原件。"
            )
    if not sources or item.get("source_clue_id") not in sources:
        raise StructuredError(
            "invalid_action", "此实物没有该作者效果的取得依据，不能用任意物品结算效果。"
        )
    effects = entry.get("flag_effects") or {}
    if effects and all((state.get("flags") or {}).get(k) == v for k, v in effects.items()):
        raise StructuredError("invalid_action", "该作者效果已经落实，不能重复结算。")


def apply_discovery(
    state: dict,
    clue_id: str,
    entry: dict,
    actor: str,
    basis: str,
    ctx,
    *,
    acquire: bool,
    use: bool = False,
) -> str | None:
    item_label = entry.get("granted_item")
    if acquire and (not isinstance(item_label, str) or not item_label.strip()):
        raise StructuredError("invalid_action", "该线索没有作者声明的实物，不能创建背包物品。")
    item_id = None
    if acquire:
        registry = ensure_item_registry(state)["items"]
        ledger = state.setdefault("structured_acquisitions", {})
        if not isinstance(ledger, dict):
            raise StructuredError("invalid_action", "实物取得账本格式无效。")
        if clue_id in ledger:
            previous = registry.get(ledger[clue_id]["item_id"])
            if (
                previous is None
                or previous.get("quantity", 0) <= 0
                or previous.get("holder") != {"kind": "investigator", "id": actor}
            ):
                raise StructuredError(
                    "object_not_held", "此实物已取得或已转移；请使用现有物品转移，不能重复生成。"
                )
            item_id = previous["item_id"]
        else:
            # Old worlds may already hold the authored object. Adopt exactly one
            # exact-label match, not a substring or an NLP guess; ambiguity fails.
            matches = [
                item
                for item in registry.values()
                if item.get("label") == item_label and item.get("quantity", 0) > 0
            ]
            if matches:
                if len(matches) != 1 or matches[0].get("holder") != {
                    "kind": "investigator",
                    "id": actor,
                }:
                    raise StructuredError(
                        "object_not_held", "已有同名实物，不能确认唯一持有人；请先核对库存。"
                    )
                item = matches[0]
            else:
                item_id = new_stable_id("item")
                item = {
                    "item_id": item_id,
                    "label": item_label,
                    "quantity": 1,
                    "holder": {"kind": "investigator", "id": actor},
                    "stack_key": f"authored:{clue_id}",
                    "legacy_label": item_label,
                    "operations": [],
                }
                registry[item_id] = item
            item_id = item["item_id"]
            item["source_clue_id"] = clue_id
            ledger[clue_id] = {
                "item_id": item_id,
                "acquired_by": actor,
                "command_id": ctx.command_id,
                "basis": basis,
            }
    # Custody-sensitive author effects apply only on actual acquisition. A
    # reading grant of the document must not set documents_recovered.
    if not item_label or acquire:
        effects = entry.get("flag_effects") or {}
        if not isinstance(effects, dict) or any(
            type(v) not in {bool, int, str} for v in effects.values()
        ):
            raise StructuredError("invalid_action", "模组发现效果格式无效。")
        state.setdefault("flags", {}).update(copy.deepcopy(effects))
    state.setdefault("structured_discoveries", {})[clue_id] = {
        "investigator_id": actor,
        "command_id": ctx.command_id,
        "basis": basis,
        "acquired": acquire,
        "used": use,
    }
    return item_id


def advance_clue_clock(state: dict) -> None:
    """Legacy convention: a first catalog recording advances declared clarity."""
    clocks = state.get("case_clocks")
    definition = (state.get("case_clock_definitions") or {}).get("clue_clarity")
    if (
        not isinstance(clocks, dict)
        or not isinstance(definition, dict)
        or "clue_clarity" not in clocks
    ):
        return
    value, maximum = clocks["clue_clarity"], definition.get("max")
    if type(value) is not int or type(maximum) is not int or not 0 <= value <= maximum:
        raise StructuredError("invalid_action", "线索时钟记录无效，请先核对模组状态。")
    clocks["clue_clarity"] = min(value + 1, maximum)
