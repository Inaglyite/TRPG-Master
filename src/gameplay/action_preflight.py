"""Deterministic, public preflight for consequential scene transitions.

The module author may attach ``action_advisories`` to an origin scene.  A
matching advisory is evaluated entirely from the authoritative action plan and
the investigator's public sheet.  It never reads NPC secrets, private memory,
or clue text, so the warning shown before a move cannot become an accidental
walkthrough spoiler.

An advisory has one of two shapes, and they must never be mixed:

``blocking`` (default)
    A decision card.  ``npc_text``/``keeper_text``/``public_hint`` and the
    authored options are shown instead of moving, and the player decides.

``blocking: false``
    A transition beat.  There is no card and no decision — the player already
    chose to leave.  Only ``transition_text`` (the departure beat) is used and
    the action settles normally.  Card fields are ignored: replaying a held
    back "do you still want to go?" prompt after the player has chosen is the
    failure this split exists to prevent.
"""

from __future__ import annotations

import copy
import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Any

from src.gameplay.action_resolution import ActionResolution


@dataclass(frozen=True)
class ActionPreviewOption:
    """One player-visible response to an action preview."""

    id: str
    label: str
    description: str
    outcome: str
    action_text: str = ""


@dataclass(frozen=True)
class ActionPreview:
    """A public warning bound to one immutable action resolution."""

    advisory_id: str
    plan_digest: str
    request_id: str
    title: str
    narrative: str
    npc_id: str | None
    options: tuple[ActionPreviewOption, ...]
    default_option: str
    blocking: bool = True
    transition_text: str = ""

    def decision_payload(self) -> dict:
        import re

        gist = re.sub(r"【/?npc(?::[^】]*)?】", "", self.narrative).strip()
        return {
            "id": self.request_id,
            "kind": "action_preview",
            "presentation": "chat",
            "title": self.title,
            "description": gist[:500],
            "options": [
                {
                    "id": option.id,
                    "label": option.label,
                    "description": option.description,
                }
                for option in self.options
            ],
            "default_option": self.default_option,
        }

    def option(self, option_id: str | None) -> ActionPreviewOption:
        selected = next((option for option in self.options if option.id == option_id), None)
        if selected is not None:
            return selected
        return next(option for option in self.options if option.id == self.default_option)


def _bounded_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _flags_match(spec: dict, world: dict) -> bool:
    flags = world.get("flags", {})
    if not isinstance(flags, dict):
        return False
    required = spec.get("required_flags", {})
    forbidden = spec.get("forbidden_flags", {})
    if not isinstance(required, dict) or not isinstance(forbidden, dict):
        return False
    return all(flags.get(str(key)) == value for key, value in required.items()) and all(
        flags.get(str(key)) != value for key, value in forbidden.items()
    )


def _trait_text(pc: dict) -> str:
    values: list[str] = []
    backstory = pc.get("backstory", {})
    if isinstance(backstory, dict):
        for key in ("beliefs", "traits", "description"):
            values.append(str(backstory.get(key) or ""))
    profile = pc.get("psychological_profile", {})
    if isinstance(profile, dict):
        traits = profile.get("traits", [])
        if isinstance(traits, list):
            values.extend(
                str(item.get("name") or item.get("text") or "")
                if isinstance(item, dict)
                else str(item)
                for item in traits
            )
    return " ".join(values).casefold()


def _trigger_matches(trigger: Any, world: dict) -> bool:
    """Return whether at least one declared public-sheet risk is present.

    Multiple entries are deliberately ORed: an advisory may be relevant because
    *either* a skill is low *or* a character trait causes social friction.  An
    absent trigger (or ``{"always": true}``) declares an unconditional beat.
    """
    if trigger is None:
        return True
    if not isinstance(trigger, dict):
        return False
    if trigger.get("always") is True:
        return True

    pc = world.get("pc", {})
    if not isinstance(pc, dict):
        pc = {}
    checks: list[bool] = []

    skill_below = trigger.get("skill_below", {})
    if isinstance(skill_below, dict):
        skills = pc.get("skills", {})
        skills = skills if isinstance(skills, dict) else {}
        for skill, threshold in skill_below.items():
            limit = _numeric(threshold)
            current = _numeric(skills.get(str(skill)))
            if limit is not None:
                checks.append((current if current is not None else 0) < limit)

    attribute_below = trigger.get("attribute_below", {})
    if isinstance(attribute_below, dict):
        attributes = pc.get("attributes", {})
        attributes = attributes if isinstance(attributes, dict) else {}
        for attribute, threshold in attribute_below.items():
            limit = _numeric(threshold)
            current = _numeric(attributes.get(str(attribute)))
            if limit is not None:
                checks.append((current if current is not None else 0) < limit)

    occupations = trigger.get("occupation_contains_any", [])
    if isinstance(occupations, list):
        occupation = str(pc.get("occupation") or "").casefold()
        terms = [str(term).strip().casefold() for term in occupations if str(term).strip()]
        if terms:
            checks.append(any(term in occupation for term in terms))

    traits = trigger.get("traits_contain_any", [])
    if isinstance(traits, list):
        haystack = _trait_text(pc)
        terms = [str(term).strip().casefold() for term in traits if str(term).strip()]
        if terms:
            checks.append(any(term in haystack for term in terms))

    return any(checks) if checks else False


def _route_matches(spec: dict, action: ActionResolution) -> bool:
    destination = str(spec.get("destination_scene_id") or "")
    if destination and destination != action.destination_scene_id:
        return False

    transition_kinds = spec.get("transition_kinds", [])
    if isinstance(transition_kinds, list) and transition_kinds:
        if action.transition_kind not in {str(value) for value in transition_kinds}:
            return False

    route_ids = spec.get("route_ids", [])
    if isinstance(route_ids, list) and route_ids:
        if action.route_id not in {str(value) for value in route_ids}:
            return False

    return True


def _plan_digest(action: ActionResolution, advisory_id: str) -> str:
    payload = {
        "advisory_id": advisory_id,
        "player_input": action.player_input,
        "contract": action.public_contract(),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


def _preview_options(spec: dict, destination_name: str) -> tuple[ActionPreviewOption, ...]:
    continue_label = _bounded_text(spec.get("continue_label"), 80) or (
        f"仍然前往{destination_name}" if destination_name else "仍然执行这项行动"
    )
    cancel_label = _bounded_text(spec.get("cancel_label"), 80) or "暂时不去"
    options = [
        ActionPreviewOption(
            id="continue_action",
            label=continue_label,
            description=_bounded_text(spec.get("continue_description"), 160),
            outcome="continue",
        )
    ]

    prepares = spec.get("prepare_options", [])
    if isinstance(prepares, list):
        for index, item in enumerate(prepares[:3]):
            if not isinstance(item, dict):
                continue
            label = _bounded_text(item.get("label"), 80)
            action_text = _bounded_text(item.get("action_text"), 500)
            if not label or not action_text:
                continue
            configured_id = _bounded_text(item.get("id"), 48) or str(index + 1)
            safe_id = "".join(char for char in configured_id if char.isalnum() or char in "_-")
            options.append(
                ActionPreviewOption(
                    id=f"prepare_{safe_id or index + 1}",
                    label=label,
                    description=_bounded_text(item.get("description"), 160),
                    outcome="replace",
                    action_text=action_text,
                )
            )

    options.append(
        ActionPreviewOption(
            id="cancel_action",
            label=cancel_label,
            description=_bounded_text(spec.get("cancel_description"), 160),
            outcome="cancel",
        )
    )
    return tuple(options)


def match_action_preview(action: ActionResolution, world: dict) -> ActionPreview | None:
    """Return the first authored advisory matching a planned scene crossing."""
    if not action.is_arrival or not action.destination_scene_id:
        return None
    scenes = world.get("scene_catalog", {})
    if not isinstance(scenes, dict):
        return None
    origin = scenes.get(action.origin_scene_id)
    if not isinstance(origin, dict):
        return None
    advisories = origin.get("action_advisories", [])
    if not isinstance(advisories, list):
        return None

    for raw in advisories:
        if (
            not isinstance(raw, dict)
            or raw.get("enabled") is False
            or not _flags_match(raw, world)
            or not _route_matches(raw, action)
            or not _trigger_matches(raw.get("trigger_if"), world)
        ):
            continue

        advisory_id = _bounded_text(raw.get("id"), 80)
        if not advisory_id:
            continue
        current_scene = world.get("current_scene", {})
        runtime_present = (
            current_scene.get("npcs_present", [])
            if isinstance(current_scene, dict)
            and str(current_scene.get("id") or "") == action.origin_scene_id
            else []
        )
        present = {str(value) for value in runtime_present}
        npc_id = _bounded_text(raw.get("npc_id"), 80)
        blocking = bool(raw.get("blocking", True))
        # A declared NPC who is not actually in the runtime scene cannot perform
        # the beat; the keeper fallback carries the same fact in prose instead.
        npc_absent = bool(npc_id) and npc_id not in present
        plan_digest = _plan_digest(action, advisory_id)

        if not blocking:
            # 非阻塞提醒不是决策卡：只使用作者声明的过渡节拍。旧条目没有
            # transition_text，直接跳过——绝不把"劝留 + 再选一次"的卡片
            # 文案当成出发素材回灌给故事模型。
            beat = _bounded_text(
                raw.get("keeper_text") if npc_absent else raw.get("transition_text"),
                1600,
            )
            if not beat:
                continue
            return ActionPreview(
                advisory_id=advisory_id,
                plan_digest=plan_digest,
                request_id=f"action-transition-{plan_digest}-{secrets.token_hex(6)}",
                title=_bounded_text(raw.get("title"), 120),
                narrative=beat,
                npc_id=npc_id if not npc_absent else None,
                options=(),
                default_option="",
                blocking=False,
                transition_text=beat,
            )

        npc_text = _bounded_text(raw.get("npc_text"), 1600)
        keeper_text = _bounded_text(raw.get("keeper_text"), 1600)
        hint = _bounded_text(raw.get("public_hint"), 500)
        use_npc = bool(npc_id and npc_id in present and npc_text)
        spoken = f"【npc:{npc_id}】{npc_text}【/npc】" if use_npc else keeper_text
        narrative = "\n\n".join(part for part in (spoken, hint) if part)
        if not narrative:
            continue

        destination = scenes.get(action.destination_scene_id, {})
        destination_name = (
            _bounded_text(destination.get("name"), 100)
            if isinstance(destination, dict)
            else ""
        )
        options = _preview_options(raw, destination_name)
        return ActionPreview(
            advisory_id=advisory_id,
            plan_digest=plan_digest,
            request_id=(
                f"action-preview-{plan_digest}-{secrets.token_hex(6)}"
            ),
            title=_bounded_text(raw.get("title"), 120) or "在行动前再考虑一下",
            narrative=narrative,
            npc_id=npc_id if use_npc else None,
            options=options,
            # A timeout must never move the investigator or spend resources.
            default_option="cancel_action",
            blocking=True,
        )
    return None


def upgrade_legacy_advisories(state: dict, template: dict) -> list[str]:
    """Upgrade unmodified pre-fix non-blocking advisories to the author's beat.

    Before the card/beat split, a non-blocking advisory carried only decision
    card text, which the engine handed to the story model as departure
    material — so a player who chose to leave was shown the held-back "hear my
    view first" line and asked to choose again.

    An entry is replaced only when *all* of these hold:

    * the world entry is non-blocking and still has no ``transition_text``;
    * the module template carries the same advisory id with a ``transition_text``;
    * the template declares that exact world entry in the advisory's
      ``supersedes`` list — an author-declared copy of the shipped legacy
      payload.

    The last condition is the important one: ``supersedes`` is compared by full
    structural equality, so any hand edit at all (a rewritten ``npc_text``, a
    reworded ``keeper_text``, an extra key, a changed option) keeps the entry
    untouched.  Blocking cards, entries the template does not declare, and
    entries whose scene is missing are left alone too.  The replacement drops
    the ``supersedes`` bookkeeping, which keeps the world copy clean and keeps
    the step idempotent.  Only advisory text is rewritten: no progress,
    history, flags or scene state is touched.
    """

    upgraded: list[str] = []
    scenes = state.get("scene_catalog")
    template_scenes = template.get("scene_catalog")
    if not isinstance(scenes, dict) or not isinstance(template_scenes, dict):
        return upgraded

    for scene_id, scene in scenes.items():
        if not isinstance(scene, dict):
            continue
        advisories = scene.get("action_advisories")
        template_scene = template_scenes.get(scene_id)
        if not isinstance(advisories, list) or not isinstance(template_scene, dict):
            continue
        authored = {
            str(item.get("id") or ""): item
            for item in template_scene.get("action_advisories", [])
            if isinstance(item, dict)
        }
        for index, advisory in enumerate(advisories):
            if not isinstance(advisory, dict):
                continue
            if bool(advisory.get("blocking", True)):
                continue
            if str(advisory.get("transition_text") or "").strip():
                continue
            replacement = authored.get(str(advisory.get("id") or ""))
            if not isinstance(replacement, dict) or not str(
                replacement.get("transition_text") or ""
            ).strip():
                continue
            declared = replacement.get("supersedes")
            if not isinstance(declared, list) or not any(
                isinstance(payload, dict)
                and payload
                and advisory == payload
                for payload in declared
            ):
                continue
            migrated = {
                key: copy.deepcopy(value)
                for key, value in replacement.items()
                if key != "supersedes"
            }
            advisories[index] = migrated
            upgraded.append(f"{scene_id}/{advisory.get('id')}")
    return upgraded
