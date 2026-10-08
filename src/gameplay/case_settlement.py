"""Transaction-local, multi-investigator case rewards; no profile/file writes."""

from __future__ import annotations

import copy

from .characters import _normalize_career, _reputation_delta
from .investigators import investigator_entity


def settle_roster_case(
    state: dict,
    *,
    world_id: str,
    ending: dict,
    completed_at: str,
) -> dict[str, dict]:
    """Apply one world's ending once, on the caller's transaction-local copy.

    Career entries and the settlement ledger belong to world state, so restore
    and branch retain their existing snapshot semantics. Persisting/exporting a
    user's library card is a separate owner-authorized operation. Existing
    contacts are preserved; this function does not copy globally revealed NPCs
    into every player's private contact list.
    """
    if not world_id or not completed_at:
        raise ValueError("结算必须有世界标识与完成时间。")
    ending_type = ending.get("type")
    if ending_type not in {"good", "secret", "neutral", "bad"}:
        raise ValueError("未知结局类型。")
    case_id = f"{world_id}:{ending.get('id') or 'manual'}"
    ledger = state.get("case_settlements", {})
    if not isinstance(ledger, dict):
        raise ValueError("案件结算账本损坏。")
    if case_id in ledger:
        if not isinstance(ledger[case_id], dict):
            raise ValueError("案件结算凭证损坏。")
        return copy.deepcopy(ledger[case_id])

    roster = state.get("investigators")
    if roster is not None and not isinstance(roster, dict):
        raise ValueError("调查员名册损坏。")
    ids = sorted(roster) if isinstance(roster, dict) and roster else ["pc"]
    staged = {}
    for investigator_id in ids:
        sheet = investigator_entity(state, investigator_id)
        if not isinstance(sheet, dict):
            raise ValueError(f"缺少调查员角色卡：{investigator_id}")
        career = _normalize_career(sheet.get("career"))
        history = career["case_history"]
        previous = next(
            (
                entry
                for entry in history
                if isinstance(entry, dict) and entry.get("case_id") == case_id
            ),
            None,
        )
        session = sheet.get("character_session") or {}
        if not isinstance(session, dict):
            raise ValueError(f"调查员起始状态损坏：{investigator_id}")
        module = str(state.get("module") or "")
        entry = previous or {
            "case_id": case_id,
            "world_id": world_id,
            "module": module,
            "ending_id": ending.get("id"),
            "ending_type": ending_type,
            "title": str(ending.get("title") or ""),
            "summary": str(ending.get("summary") or ""),
            "hp_delta": int(sheet.get("hp", 0))
            - int(
                session.get(
                    "starting_hp",
                    sheet.get("max_hp", sheet.get("hp", 0)),
                )
            ),
            "san_delta": int(sheet.get("san", 0))
            - int(
                session.get(
                    "starting_san",
                    sheet.get("max_san", sheet.get("san", 0)),
                )
            ),
            "reputation_delta": _reputation_delta(ending_type),
            "completed_at": completed_at,
        }
        if previous is None:
            history.append(entry)
            career["reputation"] += entry["reputation_delta"]
        if module and module not in career["completed_modules"]:
            career["completed_modules"].append(module)
        staged[investigator_id] = {
            "investigator_id": investigator_id,
            "character_id": str(sheet.get("character_id") or investigator_id),
            "case": copy.deepcopy(entry),
            "career": career,
            "character_snapshot": {
                key: copy.deepcopy(sheet[key])
                for key in (
                    "name",
                    "occupation",
                    "age",
                    "era",
                    "attributes",
                    "derived",
                    "skills",
                    "inventory",
                    "credit_rating",
                    "backstory",
                    "psychological_profile",
                    "portrait",
                    "violence_stance",
                    "luck",
                    "hp",
                    "max_hp",
                    "san",
                    "max_san",
                    "mp",
                    "max_mp",
                    "conditions",
                )
                if key in sheet
            },
        }

    # Stage every participant before modifying any sheet. A malformed later
    # sheet must not leave earlier investigators with half-awarded rewards.
    for investigator_id, receipt in staged.items():
        sheet = investigator_entity(state, investigator_id)
        sheet["career"] = copy.deepcopy(receipt["career"])
        if isinstance(roster, dict) and investigator_id in roster:
            roster[investigator_id]["career"] = copy.deepcopy(receipt["career"])
    state.setdefault("case_settlements", {})[case_id] = copy.deepcopy(staged)
    return copy.deepcopy(staged)
