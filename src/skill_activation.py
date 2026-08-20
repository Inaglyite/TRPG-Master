"""GameEngine 侧的 Skill 激活编排（H3）。

规则数据与谓词判断在 ``skill_manifest`` / ``skill_resolver`` / ``skill_pins``；
本模块只做引擎装配（catalog/pin 缓存、注入、模型 loader），避免 engine.py
继续增长触碰架构行数 ratchet。所有函数接收 engine duck（``context``、
``messages``、``_loaded_optional_skills`` 等属性）。
"""

from __future__ import annotations

import json
from typing import Any

from . import context_shadow as _context_shadow
from .context_summary import CONTROL_MESSAGE_PREFIX
from .logger import error as log_error
from .logger import game_event as log_game
from .skill_manifest import (
    CatalogError,
    catalog_for,
    read_skill_content,
    skill_content_digest,
    skill_content_within_budget,
)
from .skill_pins import (
    PinUnavailable,
    ensure_world_pins,
    pinned_catalog,
    probe_world_pins,
    read_world_pins,
)
from .skill_resolver import keyword_misses, resolve_activations
from .tool_request_authority import current_execution_snapshot

_SKILL_SURFACE_MARKER_PREFIX = "[skill-pin"


def _skill_surface_marker(skill_id: str, digest: str) -> str:
    """Stable, model-visible marker for one deterministic Skill control message.

    ``_loaded_optional_skills`` is deliberately *not* the source of truth for
    this.  A context checkpoint can replace an old control message during the
    same turn, while the in-memory set survives.  The marker lets us ask the
    only question that matters for a provider request: is this exact frozen
    Skill still present on the current model surface?
    """

    return f"{_SKILL_SURFACE_MARKER_PREFIX} id={skill_id} digest={digest}]"


def _has_deterministic_skill_on_surface(engine: Any, skill_id: str, digest: str) -> bool:
    """Whether the current model surface still contains this engine control.

    The marker is paired with the exact in-memory message object registered at
    injection time.  A player can never suppress a rule merely by echoing a
    control-looking string (or a public digest) in an ordinary user message.
    Reloaded/copied history deliberately loses that ephemeral registration and
    receives a fresh engine control on its next deterministic refresh.
    """

    marker = _skill_surface_marker(skill_id, digest)
    trusted_messages = getattr(engine, "__dict__", {}).get("_skill_control_messages", {})
    trusted_message = trusted_messages.get(marker) if isinstance(trusted_messages, dict) else None
    if not isinstance(trusted_message, dict):
        return False
    for message in getattr(engine, "messages", ()):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = str(message.get("content") or "")
        if (
            message is trusted_message
            and content.startswith(CONTROL_MESSAGE_PREFIX)
            and marker in content
        ):
            return True
    return False


def _has_loaded_skill_on_surface(engine: Any, skill_id: str, digest: str, content: str) -> bool:
    """Whether a prior full ``load_skill`` result remains model-visible.

    A compacted/pruned result does not contain the complete frozen content and
    therefore cannot satisfy this predicate.  The next request must receive a
    full result again in that case rather than trusting an in-memory lifetime
    set.
    """

    for message in getattr(engine, "messages", ()):
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        try:
            payload = json.loads(str(message.get("content") or ""))
        except (TypeError, json.JSONDecodeError):
            continue
        if (
            isinstance(payload, dict)
            and payload.get("ok") is True
            and payload.get("skill_id") == skill_id
            and payload.get("digest") == digest
            and payload.get("content") == content
        ):
            return True
    return False


def skill_catalog(engine: Any):
    """当前世界的 Skill catalog（官方 + 模组），加载失败返回 None 并诊断。"""
    if engine._skill_catalog_cache is None:
        try:
            engine._skill_catalog_cache = catalog_for(engine.context)
        except CatalogError as exc:
            log_error(f"Skill catalog 加载失败: {exc}")
            engine._skill_catalog_cache = False
    return engine._skill_catalog_cache or None


def skill_pins(engine: Any):
    """当前世界的 Skill pin 集；不可用返回 None（非 DB 世界/遗留环境）。

    catalog 损坏但世界已有 pin 时仍返回 pin（pin 自足，不依赖当前 catalog）。
    已存在世界的 pin 失效抛 PinUnavailable（调用方 fail-closed 处理）。
    """
    if engine._skill_pins_cache is None:
        catalog = skill_catalog(engine)
        if catalog is not None:
            engine._skill_pins_cache = ensure_world_pins(engine.context, catalog) or False
        else:
            # 当前 catalog 不可用：已有 pin 的世界由冻结快照自足治理。
            engine._skill_pins_cache = read_world_pins(engine.context) or False
    return engine._skill_pins_cache or None


def effective_skill_catalog(engine: Any):
    """该世界行为治理用的有效 catalog：有 pin 用冻结快照，否则当前 catalog。"""
    pins = skill_pins(engine)
    if pins is not None:
        return pinned_catalog(pins)
    return skill_catalog(engine)


def _ruleset_name(engine: Any) -> str:
    try:
        data = json.loads(
            (engine.context.project_root / "rules" / "rule_config.json").read_text(encoding="utf-8")
        )
        return str(data.get("game_system") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def _module_capabilities(engine: Any) -> set[str]:
    record = getattr(engine.context, "module_record", None)
    capabilities = getattr(record, "capabilities", None) or ()
    return {str(capability) for capability in capabilities}


def inject_skill(engine: Any, entry: Any) -> bool:
    """注入一个确定性激活的 Skill，内容以世界 pin 为唯一权威。"""
    try:
        pins = skill_pins(engine)
    except PinUnavailable as exc:
        # 已存在世界 pin 失效：拒绝注入，绝不回退可能漂移的磁盘内容。
        log_error(f"Skill pin 失效，跳过注入: {entry.id} | {exc}")
        return False
    if pins is not None:
        pin = pins.get(entry.id)
        if pin is None:
            log_error(f"Skill 不在世界 pin 中，拒绝热补: {entry.id}")
            return False
        content, digest = pin.content, pin.digest
    else:
        # A real DB world with no pins is not a legacy runtime.  Its catalog
        # may already have failed to load, and accepting a caller-provided
        # entry here would recreate the forbidden live-disk authority path.
        if probe_world_pins(engine.context).state == "empty":
            log_error(f"世界尚未冻结 Skill，拒绝磁盘注入: {entry.id}")
            return False
        content = read_skill_content(
            engine.context.project_root,
            entry,
            module_dir=getattr(engine.context, "module_dir", None),
        )
        if content is None:
            log_error(f"可选 Skill 加载失败: {entry.id}")
            return False
        digest = skill_content_digest(content)
        log_error(f"Skill pin 不可用，回退磁盘内容注入: {entry.id}")
    if not skill_content_within_budget(content, entry):
        # This is defense in depth for non-DB/legacy callers.  DB worlds are
        # rejected while reading their pin; neither path may append an
        # author-controlled automatic Skill beyond its frozen budget.
        log_error(f"Skill 超出 max_context_tokens，拒绝自动注入: {entry.id}")
        return False
    if entry.residency == "deterministic":
        _record_turn_deterministic_skill(engine, entry.id, digest)
    # This is intentionally surface-based, not lifetime-based.  H2 may remove
    # an old control instruction during a preflight/overflow compaction and
    # retry the same turn; re-inject the exact pin before that retry, but never
    # duplicate it while it remains on the provider-visible message surface.
    if _has_deterministic_skill_on_surface(engine, entry.id, digest):
        engine._loaded_optional_skills.add(entry.id)
        return False
    engine._loaded_optional_skills.add(entry.id)
    engine.append_control_instruction(
        f"{_skill_surface_marker(entry.id, digest)}\n"
        f"以下 Skill 规则已经由引擎加载，请在本回合应用：{entry.id}\n\n{content}",
        skill_source={
            "skill_id": entry.id,
            "digest": digest,
            "version": entry.version,
        },
    )
    # Keep an in-memory identity reference rather than trusting text content
    # alone.  It intentionally does not survive a save/reload, where a fresh
    # deterministic refresh is the safe behavior.
    latest = engine.messages[-1] if getattr(engine, "messages", None) else None
    if isinstance(latest, dict):
        controls = engine.__dict__.setdefault("_skill_control_messages", {})
        if isinstance(controls, dict):
            controls[_skill_surface_marker(entry.id, digest)] = latest
    return True


def resolve_for_engine(
    engine: Any,
    *,
    tool_name: str | None = None,
    force_skill_ids: tuple[str, ...] = (),
) -> list:
    try:
        catalog = effective_skill_catalog(engine)
    except PinUnavailable as exc:
        # fail-closed：pin 失效时本回合不激活任何 Skill（宁可降级，不可漂移）。
        log_error(f"Skill pin 失效，本回合不激活可选 Skill: {exc}")
        return []
    if catalog is None:
        return []
    try:
        world = engine.context.world_store.load()
    except Exception:
        world = {}
    return resolve_activations(
        catalog,
        world=world if isinstance(world, dict) else {},
        action_resolution=getattr(engine, "_action_resolution", None),
        tool_name=tool_name,
        ruleset=_ruleset_name(engine),
        module_capabilities=_module_capabilities(engine),
        force_ids=force_skill_ids,
    )


def hint_tool(engine: Any, tool_name: str) -> None:
    """工具派发热潮：由 resolver 的 tools 谓词确定性决定注入。"""
    for entry in resolve_for_engine(engine, tool_name=tool_name):
        inject_skill(engine, entry)


def sync_world_skills(engine: Any, content: str = "") -> None:
    """回合开始：按权威状态确定性激活；关键词只做漏加载诊断。"""
    activated = resolve_for_engine(engine)
    activated_ids = {entry.id for entry in activated}
    for entry in activated:
        inject_skill(engine, entry)
    try:
        catalog = effective_skill_catalog(engine)
    except PinUnavailable:
        return
    if catalog is None or not content:
        return
    for entry in keyword_misses(catalog, content, activated_ids | engine._loaded_optional_skills):
        log_game(f"Skill 漏加载诊断 | skill={entry.id} | 关键词命中但未被确定性激活")


def refresh_deterministic_skills(engine: Any) -> int:
    """Restore deterministic Skill controls missing from the current surface.

    This is intentionally narrower than :func:`sync_world_skills`: capacity
    compaction and overflow retries must not run keyword diagnostics or infer
    new gameplay state.  They only re-apply rules whose resolver predicates
    were already true for the current authoritative world.

    Lightweight streamer test doubles and non-game callers do not have the
    engine caches required for this operation; they are explicitly a no-op.
    """

    required = (
        "context",
        "_loaded_optional_skills",
        "_skill_catalog_cache",
        "_skill_pins_cache",
        "messages",
    )
    if not all(hasattr(engine, attribute) for attribute in required):
        return 0
    restored = 0
    # A tool-only predicate may no longer be derivable from world state after
    # its handler has returned.  Keep its *already deterministic* activation
    # only for this active turn, then re-resolve it from the frozen catalog if
    # H2 replaced the original control surface before the retry.
    tracked = _current_turn_deterministic_skill_ids(engine)
    for entry in resolve_for_engine(engine, force_skill_ids=tracked):
        restored += int(inject_skill(engine, entry))
    return restored


def _record_turn_deterministic_skill(engine: Any, skill_id: str, digest: str) -> None:
    """Remember a tool-triggered activation only until the current turn ends."""

    turn_id = str(getattr(engine, "_active_turn_id", "") or "")
    if not turn_id:
        # Session/preflight calls without a durable turn may still inject a
        # state-triggered rule, but they must not leak a transient tool trigger
        # into an unrelated future turn.
        return
    state = getattr(engine, "__dict__", {}).get("_turn_deterministic_skills")
    if not isinstance(state, dict) or state.get("turn_id") != turn_id:
        state = {"turn_id": turn_id, "skills": {}}
        engine.__dict__["_turn_deterministic_skills"] = state
    skills = state.get("skills")
    if isinstance(skills, dict):
        skills[skill_id] = digest


def _current_turn_deterministic_skill_ids(engine: Any) -> tuple[str, ...]:
    """Read the private activation continuation only for the live turn id."""

    turn_id = str(getattr(engine, "_active_turn_id", "") or "")
    state = getattr(engine, "__dict__", {}).get("_turn_deterministic_skills")
    if not turn_id or not isinstance(state, dict) or state.get("turn_id") != turn_id:
        return ()
    skills = state.get("skills")
    if not isinstance(skills, dict):
        return ()
    # Digests are checked again by normal pinned injection; retain only valid
    # nonempty ids here so a corrupted in-memory diagnostic object cannot add
    # arbitrary catalog entries.
    return tuple(sorted(skill_id for skill_id, digest in skills.items() if skill_id and digest))


def loadable_skill_allowlist(engine: Any) -> tuple[tuple[str, str], ...]:
    """本请求可 load_skill 的冻结集合：(skill_id, digest) 对，按 pin 顺序。

    on_demand/model_invocable 判定用冻结的 pin 元数据；pin 不可用（含
    PinUnavailable）时返回空——模型 loader 路径的 fail-closed：没有冻结
    集合就什么都加载不了。
    """
    try:
        pins = skill_pins(engine)
    except PinUnavailable:
        return ()
    if pins is None:
        return ()
    return tuple(
        (pin.skill_id, pin.digest)
        for pin in sorted(pins.values(), key=lambda item: item.order)
        if pin.entry.residency == "on_demand" and pin.entry.model_invocable
    )


def note_injection_provenance(engine: Any, message: dict, skill_source: dict[str, Any]) -> None:
    """把 skill 溯源登记到 H2 context shadow（fail-open）。"""
    _context_shadow.note_skill_injection(
        engine,
        message=message,
        skill_id=str(skill_source.get("skill_id") or ""),
        digest=str(skill_source.get("digest") or ""),
        version=str(skill_source.get("version") or ""),
    )


def note_load_skill_result(engine: Any, message: dict, output: str) -> None:
    """load_skill 工具结果的溯源：工具结果消息即 Skill 内容注入点。"""
    try:
        payload = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict):
        return
    digest = payload.get("digest")
    content = payload.get("content")
    if (
        not payload.get("ok")
        or not payload.get("skill_id")
        or not isinstance(digest, str)
        or len(digest) != len("sha256:") + 64
        or not digest.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in digest[len("sha256:") :])
        or not isinstance(content, str)
    ):
        return
    note_injection_provenance(engine, message, payload)


def execute_load_skill(engine: Any, skill_id: str) -> str:
    """模型按需加载非关键 Skill；只放行世界 pin 冻结元数据里的 on_demand 条目。

    H1 执行期证据：必须处于已签发模型请求的执行窗口（current_execution_snapshot）
    内，且 (skill_id, pin.digest) 精确属于该请求冻结的 skill_allowlist；
    无快照或 digest 不匹配一律拒绝。
    """
    deny: dict[str, Any] = {"ok": False, "error": "skill_not_loadable"}
    request_snapshot = current_execution_snapshot(engine)
    if request_snapshot is None:
        log_error(f"load_skill 缺少已签发的执行期快照，拒绝: {skill_id}")
        return json.dumps(deny, ensure_ascii=False)
    frozen = dict(request_snapshot.skill_allowlist)
    frozen_digest = frozen.get(skill_id)
    try:
        catalog = effective_skill_catalog(engine)
    except PinUnavailable:
        return json.dumps(deny, ensure_ascii=False)
    if catalog is None:
        return json.dumps(deny, ensure_ascii=False)
    entry = catalog.by_id.get(skill_id)
    if entry is None or entry.residency != "on_demand" or not entry.model_invocable:
        deny["instruction"] = (
            "该 Skill 不存在、已常驻上下文或由引擎自动注入；直接基于当前上下文继续。"
        )
        return json.dumps(deny, ensure_ascii=False)
    try:
        pins = skill_pins(engine)
    except PinUnavailable:
        return json.dumps(deny, ensure_ascii=False)
    if pins is None or skill_id not in pins:
        return json.dumps(deny, ensure_ascii=False)
    pin = pins[skill_id]
    if frozen_digest is None or pin.digest != frozen_digest:
        # 不在本请求冻结集合内，或 pin digest 与冻结 digest 不一致。
        log_error(f"load_skill 冻结集合校验失败，拒绝: {skill_id}")
        return json.dumps(deny, ensure_ascii=False)
    if _has_loaded_skill_on_surface(engine, skill_id, pin.digest, pin.content):
        # The complete pinned content is still visible in a prior tool result
        # on this *current* surface.  Keep the response compact, but include
        # the digest so it can never produce an empty provenance record.
        return json.dumps(
            {
                "ok": True,
                "already_loaded": True,
                "skill_id": skill_id,
                "version": pin.version,
                "digest": pin.digest,
                "instruction": "该 Skill 已加载，直接应用此前内容即可。",
            },
            ensure_ascii=False,
        )
    from .lorebook import estimate_text_tokens

    if estimate_text_tokens(pin.content) > pin.entry.max_context_tokens:
        # 冻结元数据声明的上下文预算被内容超出：拒绝加载而不是静默超限。
        log_error(
            f"Skill 超出冻结 max_context_tokens，拒绝加载: {skill_id} "
            f"(budget={pin.entry.max_context_tokens})"
        )
        return json.dumps(
            {"ok": False, "error": "skill_over_budget", "skill_id": skill_id},
            ensure_ascii=False,
        )
    engine._loaded_optional_skills.add(skill_id)
    return json.dumps(
        {
            "ok": True,
            "skill_id": skill_id,
            "version": pin.version,
            "digest": pin.digest,
            "content": pin.content,
        },
        ensure_ascii=False,
    )
