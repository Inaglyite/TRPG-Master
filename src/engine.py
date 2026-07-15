"""TRPG 游戏引擎 —— 纯内核，不依赖终端/WebSocket。
通过回调函数输出事件，可接入任意界面层。
"""

import copy
import json
import re
import time
from dataclasses import dataclass
from typing import Callable

from openai import OpenAI

from .config import (
    API_KEY, BASE_URL, JUDGEMENT_MODEL, MODEL_FLASH,
    NARRATIVE_MODEL,
    ENABLE_DYNAMIC_TOOLS, ENABLE_LOREBOOK, ENABLE_STREAM_USAGE,
    OPTIONAL_SKILL_HINTS,
    PROMPT_PROFILE, STORY_THINKING_MODE,
)
from .persistence import (
    has_save,
    list_saves,
    load_game,
    load_system_prompt,
    normalize_tool_message_history,
    restore_snapshot,
    save_game,
)
from .characters import apply_character_to_state, default_character_ref, settle_case as settle_character_case
from .action_checks import infer_action_check, infer_scene_transition
from .action_resolution import ActionResolution, plan_player_action
from .discovery import (
    DiscoveryMatch,
    match_discovery_rules,
    preferred_check_skill,
)
from .encounters import SceneEncounterResolution, resolve_scene_encounters
from .combat import preview_player_escalation
from .combat_agent import build_combat_overlay
from .handouts import matching_handouts
from .lorebook import (
    LoreSelection,
    estimate_text_tokens,
    load_lorebook,
    record_lore_usage,
    select_lore,
)
from .tools import (
    MODEL_TOOLS, dice_summary, execute_function, model_tools_for,
)
from .turn_reconciler import (
    narrative_body,
    reconcile_narrative_entities,
    reconcile_turn,
    turn_needs_model_audit,
)
from .runtime import RuntimeContext
from .agent_graph import build_turn_graph
from .model_settings import ModelSettings
from .model_session import ModelSession
from .turn_journal import TurnJournal
from .logger import error as log_error, summary_event as log_summary, \
    tier_inject as log_tier, game_event as log_game, model_call as log_model_call


_INTERNAL_NARRATIVE_PATTERNS = (
    re.compile(r"(?:让我|我)?先确认(?:一下)?当前(?:的)?信息边界[。.!！]?\s*"),
    re.compile(r"按玩家(?:的)?明确意图[^。！？\n]*[。！？]?\s*"),
    re.compile(
        r"需要(?:确认|记录|写入)[^。！？\n]*(?:world_state|世界状态)"
        r"[^。！？\n]*[。！？]?\s*",
        re.IGNORECASE,
    ),
    re.compile(
        r"当前\s*SAN\s*=\s*\d+\s*[？?][^。！？\n]*(?:应该|不对)"
        r"[^。！？\n]*[。！？]?\s*",
        re.IGNORECASE,
    ),
)

_OPENING_SYSTEM_CONTRACT = """# 新游戏公开开场模式

你只负责把最新一条引擎控制消息中的 module_opening 演成开场。该消息里的
module_opening、opening_public_facts、current_scene 和 npc_public_state 是本次回复的
全部权威调查事实。可以结合本轮 Lorebook 即兴补充环境、外貌、普通物件、动作和对白，
但未出现在权威字段中的补充只属于叙事点缀：不得把它记录为线索、证据、NPC 揭示、
检定目标或 flag，也不得借此提前公开幕后秘密。不要调用工具，不要确认指令。只能陈述
module_opening 明确标为“开局既成事实”的玩家处境；不得替调查员说出台词、展示证物、
接受承诺、得出结论或执行新的有意义行动。NPC 可以先开口，但必须在第一个真实选择点
停下，并按 module_opening 指定的格式输出行动选项，把主动权交还玩家。"""

_REWRITE_SYSTEM_CONTRACT = """# 已结算回合的叙事改写

你只负责改写已经结算的玩家可见叙事。输入中的骰点、工具结果、人物、地点、资源变化、
发现内容、时间顺序和行动结果已经固定，必须全部保持一致。不得增加、删除或改变事实，
不得重新判定，不得让调查员说出原文没有的台词、作出新行动或获得新线索。可以调整句式、
节奏、感官细节和对白表达，但普通气氛描写不能升级成证据。只输出改写后的叙事正文；
不要解释任务，不要输出行动选项、标题、分析、JSON 或 Markdown 代码块。"""


class TurnCancelledError(RuntimeError):
    """Raised when a disconnected client cancels an in-flight model turn."""


def _sanitize_visible_narrative(text: str) -> str:
    for pattern in _INTERNAL_NARRATIVE_PATTERNS:
        text = pattern.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text)


def _take_complete_sentences(text: str) -> tuple[str, str]:
    boundaries = list(re.finditer(r"[。！？!?\n]", text))
    if not boundaries:
        return "", text
    cutoff = boundaries[-1].end()
    return text[:cutoff], text[cutoff:]


def _stream_usage_dict(usage: object) -> dict:
    if usage is None:
        return {}
    if isinstance(usage, dict):
        raw = usage
    elif hasattr(usage, "model_dump"):
        raw = usage.model_dump()
    else:
        raw = {
            key: getattr(usage, key, None)
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "prompt_cache_hit_tokens",
                "prompt_cache_miss_tokens",
            )
        }
    return {
        key: value
        for key, value in raw.items()
        if key in {
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "prompt_cache_hit_tokens",
            "prompt_cache_miss_tokens",
        }
        and value is not None
    }


def _thinking_type_for_request(model: str, request_role: str) -> str | None:
    """Return an explicit DeepSeek thinking mode only when it is intentional."""
    if request_role != "story" or STORY_THINKING_MODE == "provider":
        return None
    if STORY_THINKING_MODE in {"disabled", "enabled"}:
        return STORY_THINKING_MODE
    if "deepseek.com" in BASE_URL.lower() and model == MODEL_FLASH:
        return "disabled"
    return None


@dataclass
class EngineCallbacks:
    """引擎输出事件回调。每个回调在特定时机触发。"""
    on_narrative: Callable[[str], None] = lambda text: None       # 流式文本块
    on_tension: Callable[[str, str], None] = lambda text, cat: None  # 沉浸式提示
    on_dice: Callable[[str, dict | None], None] = lambda summary, roll_data=None: None  # 骰子结果
    on_glm_summary: Callable[[str], None] = lambda text: None    # 快速摘要
    on_suggest: Callable[[dict], bool] = lambda info: False      # 检定确认，返回 True/False
    on_decision: Callable[[dict], str | None] = lambda info: info.get("default_option")  # 多选决定
    on_phase: Callable[[str, str], None] = lambda phase, label: None  # 稳定的等待阶段
    on_choices: Callable[[list[dict]], None] = lambda choices: None   # 结构化行动选项
    on_done: Callable[[], None] = lambda: None                   # 回合结束
    on_game_over: Callable[[str, str, str], None] = lambda t, ti, s: None  # 游戏结束
    on_handout: Callable[[dict], None] = lambda info: None       # 展示材料
    on_error: Callable[[str], None] = lambda msg: None           # 错误


class GameEngine:
    """TRPG 游戏引擎内核"""

    CONTROL_MESSAGE_PREFIX = "[引擎控制指令｜非玩家发言]"

    def _ensure_model_session(self) -> ModelSession:
        session = self.__dict__.get("_model_session")
        if session is None:
            session = ModelSession(
                messages=self.__dict__.pop("messages", []),
                diagnostics=self.__dict__.pop("_turn_diagnostics", []),
            )
            self.__dict__["_model_session"] = session
        return session

    @property
    def messages(self) -> list[dict]:
        return self._ensure_model_session().messages

    @messages.setter
    def messages(self, value: list[dict]) -> None:
        self._ensure_model_session().replace_messages(value)

    @property
    def _turn_diagnostics(self) -> list[dict]:
        return self._ensure_model_session().diagnostics

    @_turn_diagnostics.setter
    def _turn_diagnostics(self, value: list[dict]) -> None:
        self._ensure_model_session().diagnostics = value

    def __init__(self, context: RuntimeContext | None = None):
        self.context = context or RuntimeContext.local()
        self.client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
        self._model_session = ModelSession()
        self.narrative_model = NARRATIVE_MODEL
        self.judgement_model = JUDGEMENT_MODEL
        self.current_model = self.narrative_model
        self.cb = EngineCallbacks()
        # 记忆管理
        self._round_count = 0
        self._player_turn_count = 0
        self._last_summary_player_turn = 0
        self._tier_last_injected = -99  # 首次必定注入
        self._last_turn_high_risk = False
        self._summary_token_estimate = 0
        # 按需 skill 加载追踪：本会话已提示/加载过的 skill 路径，避免重复提示
        self._loaded_optional_skills: set[str] = set()
        self._preconfirmed_escalation: dict | None = None
        self._lorebook = None
        self.turn_journal = TurnJournal(
            self.context.world_dir,
            world_id=self.context.world_id,
            module_name=self.context.module_name,
        )
        self._active_turn_id: str | None = None
        self._turn_diagnostics: list[dict] = []
        self._turn_lore_diagnostics: dict = {}
        # 摘要策略：按玩家回合静默压缩，避免内部工具消息过多导致频繁打断沉浸。
        self.SUMMARY_PLAYER_TURN_INTERVAL = 50
        self.SUMMARY_KEEP_RECENT_MESSAGES = 24
        self._turn_graph = build_turn_graph()

    def prepare_session(self):
        """准备一条界面连接使用的空会话，不重置 world_state。"""
        self.narrative_model = getattr(
            self, "narrative_model", NARRATIVE_MODEL
        )
        self.judgement_model = getattr(
            self, "judgement_model", JUDGEMENT_MODEL
        )
        self.messages = [{"role": "system", "content": load_system_prompt(self.context)}]
        self._lorebook = None
        if ENABLE_LOREBOOK:
            try:
                self._lorebook = load_lorebook(self.context.lorebook_file)
            except (OSError, UnicodeError, ValueError) as exc:
                log_error(f"Lorebook 加载失败，已回退到原提示词: {exc}")
        self.current_model = self.narrative_model
        self._round_count = 0
        self._player_turn_count = 0
        self._last_summary_player_turn = 0
        self._tier_last_injected = -99
        self._last_turn_high_risk = False
        self._summary_token_estimate = 0
        self._loaded_optional_skills = set()
        self._preconfirmed_escalation = None
        self._turn_diagnostics = []
        self._turn_lore_diagnostics = {}

    def configure_models(self, narrative_model: object, judgement_model: object) -> dict:
        settings = ModelSettings.validated(narrative_model, judgement_model)
        self.narrative_model = settings.narrative_model
        self.judgement_model = settings.judgement_model
        self.current_model = self.narrative_model
        return {
            "narrative_model": self.narrative_model,
            "judgement_model": self.judgement_model,
        }

    def _ensure_turn_cancellation_state(self) -> None:
        # A few narrow unit tests construct GameEngine with __new__.
        self._ensure_model_session()

    def clear_turn_cancellation(self) -> None:
        self._ensure_turn_cancellation_state()
        self._model_session.clear_cancellation()

    def turn_cancellation_requested(self) -> bool:
        self._ensure_turn_cancellation_state()
        return self._model_session.cancellation_requested

    def raise_if_turn_cancelled(self) -> None:
        if self.turn_cancellation_requested():
            raise TurnCancelledError("客户端已离开，取消未完成的回合")

    def cancel_active_turn(self) -> None:
        """Cancel model streaming so a disconnected world releases its turn lock."""
        self._ensure_turn_cancellation_state()
        self._model_session.cancel()

    def _set_active_stream(self, stream: object | None) -> None:
        self._ensure_turn_cancellation_state()
        self._model_session.set_active_stream(stream)

    def _clear_active_stream(self, stream: object) -> None:
        self._ensure_turn_cancellation_state()
        self._model_session.clear_active_stream(stream)

    def _append_model_diagnostic(self, diagnostic: dict) -> None:
        self._ensure_model_session().append_diagnostic(diagnostic)

    def _retrieve_lore_context(self, player_action: str = "") -> LoreSelection | None:
        lorebook = getattr(self, "_lorebook", None)
        if lorebook is None:
            return None
        try:
            world = self.context.world_store.load()
            selection = select_lore(lorebook, world, self.messages, player_action)
            self._turn_lore_diagnostics = {
                **selection.diagnostics,
                "scan_depth": lorebook.data.scan_depth,
                "token_budget": lorebook.data.token_budget,
            }
            return selection
        except (OSError, TypeError, ValueError) as exc:
            self._turn_lore_diagnostics = {"error": str(exc)}
            log_error(f"Lorebook 检索失败，本轮跳过: {exc}")
            return None

    def _record_lore_usage(self, entry_ids: tuple[str, ...]) -> None:
        if getattr(self, "_lorebook", None) is None:
            return
        with self.context.world_store.transaction() as world:
            record_lore_usage(world, entry_ids)

    def switch_context(self, context: RuntimeContext) -> None:
        """切换到另一个世界实例并重建该世界对应的 system prompt。"""
        self.context = context
        self.turn_journal = TurnJournal(
            context.world_dir,
            world_id=context.world_id,
            module_name=context.module_name,
        )
        self._active_turn_id = None
        self.prepare_session()

    def adopt_message_history(self, messages: list[dict]) -> int:
        """Adopt committed public/session history without restoring world state again."""
        normalized = normalize_tool_message_history(copy.deepcopy(messages))
        system_msg = self.messages[0] if self.messages else {
            "role": "system",
            "content": load_system_prompt(self.context),
        }
        history = normalized[1:] if normalized and normalized[0].get("role") == "system" else normalized
        self.messages = [system_msg, *history]
        self._round_count = 0
        self._player_turn_count = 0
        self._last_summary_player_turn = 0
        self._tier_last_injected = -99
        self._last_turn_high_risk = False
        self._summary_token_estimate = 0
        return len(self.messages)

    @property
    def active_turn_id(self) -> str | None:
        return getattr(self, "_active_turn_id", None)

    def begin_turn_record(
        self,
        *,
        kind: str,
        player_input: str | None,
    ) -> str:
        active_turn_id = getattr(self, "_active_turn_id", None)
        if active_turn_id is not None:
            return active_turn_id
        self.clear_turn_cancellation()
        if not hasattr(self, "turn_journal"):
            return ""
        turn_id = self.turn_journal.begin(
            kind=kind,
            player_input=player_input,
        )
        self._active_turn_id = turn_id
        self._turn_diagnostics = []
        self._turn_lore_diagnostics = {}
        return turn_id

    def record_turn_event(self, payload: dict) -> None:
        journal = getattr(self, "turn_journal", None)
        if journal is not None:
            journal.append_event(getattr(self, "_active_turn_id", None), payload)

    def _complete_turn_record(
        self,
        *,
        narrative: str,
        choices: list[dict],
        executed_tools: list[dict],
        lore_entry_ids: list[str],
    ) -> dict | None:
        turn_id = getattr(self, "_active_turn_id", None)
        journal = getattr(self, "turn_journal", None)
        if turn_id is None or journal is None:
            return None
        record = journal.complete(
            turn_id,
            messages=self.messages,
            world_state=self.context.world_store.load(),
            narrative=narrative,
            choices=choices,
            executed_tools=executed_tools,
            lore_entry_ids=lore_entry_ids,
            diagnostics={
                "model_calls": list(self._turn_diagnostics),
                "lorebook": dict(self._turn_lore_diagnostics),
            },
        )
        self._active_turn_id = None
        return record

    def finish_turn_record(
        self,
        *,
        status: str,
        error: str = "",
    ) -> dict | None:
        turn_id = getattr(self, "_active_turn_id", None)
        journal = getattr(self, "turn_journal", None)
        if turn_id is None or journal is None:
            return None
        record = journal.finish_incomplete(
            turn_id,
            status=status,
            error=error,
        )
        self._active_turn_id = None
        return record

    def turn_recovery_status(self, requested_turn_id: str | None = None) -> dict:
        return self.turn_journal.recovery_status(requested_turn_id)

    def turn_diagnostics(self, turn_id: str | None = None) -> dict | None:
        return self.turn_journal.diagnostic_report(turn_id)

    def rewrite_turn(self, turn_id: str) -> dict:
        """Generate a prose-only variant for the latest committed turn."""
        if self.active_turn_id is not None:
            raise RuntimeError("当前已有游戏回合正在执行")
        record = self.turn_journal.read(turn_id)
        if record.get("status") != "completed":
            raise ValueError("只能重新叙述已经完整提交的回合")
        if self.turn_journal.latest_completed_id() != turn_id:
            raise ValueError("只能重新叙述当前世界的最后一个完整回合")

        expected_revision = int(record.get("world_revision", -1))
        if self.context.world_store.revision != expected_revision:
            raise ValueError("世界状态已经继续推进，不能改写旧回合")
        original_narrative = str(record.get("narrative") or "").strip()
        original_body = narrative_body(original_narrative)
        if not original_body:
            raise ValueError("该回合没有可改写的叙事正文")

        stored_messages, _snapshot = self.turn_journal.load_artifacts(turn_id)

        def clip(value: object, limit: int) -> str:
            text = str(value or "")
            return text if len(text) <= limit else text[: limit - 3] + "..."

        recent_context = []
        for message in stored_messages[:-1]:
            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue
            recent_context.append({
                "role": role,
                "content": clip(message.get("content"), 1200),
            })
        fixed_outcomes = [
            {
                "tool": item.get("name"),
                "result": clip(item.get("output"), 1400),
            }
            for item in record.get("executed_tools", [])
            if isinstance(item, dict) and item.get("name")
        ]
        rewrite_payload = {
            "player_input": record.get("player_input"),
            "recent_context": recent_context[-6:],
            "fixed_outcomes": fixed_outcomes,
            "original_narrative": clip(original_body, 18000),
            "fixed_choices_not_to_output": [
                item.get("label")
                for item in record.get("choices", [])
                if isinstance(item, dict) and item.get("label")
            ],
        }
        rewrite_messages = [
            {"role": "system", "content": _REWRITE_SYSTEM_CONTRACT},
            {
                "role": "user",
                "content": json.dumps(
                    rewrite_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        ]
        diagnostics_start = len(getattr(self, "_turn_diagnostics", []))
        self.cb.on_phase("rewriting", "守秘人正在重新组织叙述……")
        rewritten, tool_calls = self._stream_llm(
            self.narrative_model,
            enable_tools=False,
            prompt_profile="rewrite",
            temperature=0.85,
            messages_override=rewrite_messages,
        )
        rewritten = narrative_body(_sanitize_visible_narrative(rewritten)).strip()
        if tool_calls or not rewritten:
            raise RuntimeError("模型没有返回有效的叙事改写")
        if self.context.world_store.revision != expected_revision:
            raise ValueError("改写期间世界状态发生变化，结果已丢弃")

        previous_messages = self.messages
        updated_messages = copy.deepcopy(self.messages)
        replaced = False
        for message in reversed(updated_messages):
            if (
                message.get("role") == "assistant"
                and str(message.get("content") or "").strip() == original_narrative
            ):
                message["content"] = rewritten
                replaced = True
                break
        if not replaced:
            raise ValueError("当前会话历史与回合记录不一致，请先恢复最新存档")

        self.messages = updated_messages
        try:
            self.save("slot_000")
            variant = self.turn_journal.add_narrative_variant(
                turn_id,
                narrative=rewritten,
                messages=updated_messages,
                model=self.narrative_model,
                diagnostics=list(self._turn_diagnostics[diagnostics_start:]),
            )
        except Exception:
            self.messages = previous_messages
            try:
                self.save("slot_000")
            except Exception:
                pass
            raise
        return {
            "turn_id": turn_id,
            "variant_id": variant["variant_id"],
            "narrative": rewritten,
            "choices": copy.deepcopy(record.get("choices", [])),
            "world_revision": expected_revision,
        }

    def append_control_instruction(self, content: str) -> None:
        """追加程序控制消息，并与真实玩家发言明确隔离。"""
        self.messages.append({
            "role": "user",
            "content": (
                f"{self.CONTROL_MESSAGE_PREFIX}\n"
                "静默执行下列指令。不要确认、复述或说明执行过程；"
                "不要把这条消息的发出者称为守秘人或 GM。\n"
                f"{content}"
            ),
        })

    def _has_pending_control_instruction(self) -> bool:
        if not self.messages:
            return False
        latest = self.messages[-1]
        return (
            latest.get("role") == "user"
            and latest.get("content", "").startswith(self.CONTROL_MESSAGE_PREFIX)
        )

    def _has_pending_new_game_opening(self) -> bool:
        """Return whether the pending control turn has a structured public opening."""
        if not self._has_pending_control_instruction():
            return False
        if "开始新游戏" not in str(self.messages[-1].get("content") or ""):
            return False
        try:
            world = self.context.world_store.load()
        except Exception:
            return False
        return bool(str(world.get("module_opening") or "").strip())

    def _opening_system_prompt(self) -> str:
        """Build a short system prompt that contains no module-private material."""
        return (
            load_system_prompt(self.context, profile="opening")
            + "\n\n---\n\n"
            + _OPENING_SYSTEM_CONTRACT
        )

    def reset(self, character_ref: dict | None = None) -> dict | None:
        """开始新游戏——重置对话 + 世界状态"""
        log_game(f"新游戏 | world={self.context.world_id} | 模组={self.context.module_name}")
        self.context.reset_world()

        selected_character = self._apply_starting_character(character_ref)
        identity_instruction = ""
        if selected_character:
            identity = json.dumps({
                "name": selected_character.get("name", ""),
                "occupation": selected_character.get("occupation", ""),
            }, ensure_ascii=False)
            identity_instruction = f"本局玩家调查员身份已锁定为 {identity}；"

        self.prepare_session()
        self.append_control_instruction(
            "开始新游戏。引擎将在本消息后直接附上当前模组、调查员、场景、"
            "已知线索和信息边界的权威快照；不要再调用只读初始化工具，也不要"
            "调用 show_handout，展示素材由引擎自动分发。"
            f"{identity_instruction}"
            "玩家调查员姓名、职业、背景必须以该 world_state.json 的 pc 字段为唯一来源；"
            "不要使用 module.md、示例文本或旧存档里的默认调查员姓名来称呼玩家。"
            "除非开场确实发生持久状态变化，否则不要调用工具，直接用一次回复描述"
            "开场场景并提供选项。module_opening 是开场演出脚本而不是摘要素材；"
            "有该字段时必须完整呈现其公开前提、环境、NPC 主动行为和主动权交还，"
            "通常写成 6至8 个短段落，不要只复述已知线索。回复最后必须另起一个"
            "以“**你可以——**”开头的正文块，给出 3 个编号行动和第 4 个自由行动；"
            "只能把 module_opening 明确标为开局既成事实的玩家处境当作已经发生；不得"
            "替调查员说出台词、展示信件或证物、接受委托、作出判断，或执行任何新的"
            "有意义行动。NPC 可以主动说话，但必须在需要玩家回应的第一个选择点停下；"
            "只让模组开场明确指定的人物登场，不要因为其他 NPC 也属于同一地点就把他们"
            "一次全部拉入画面；即兴增加的气氛、动作和对白只能作为叙事点缀，不能记录为"
            "线索、证据、NPC 揭示、检定目标或 flag。"
        )
        return selected_character

    def _apply_starting_character(self, character_ref: dict | None) -> dict | None:
        """将选择的调查员复制进当前模组 world_state.pc。"""
        selected_ref = character_ref or default_character_ref(
            self.context.module_name, context=self.context
        )
        if not selected_ref:
            return

        selected_character: dict | None = None

        def apply(state: dict) -> None:
            nonlocal selected_character
            selected_character = apply_character_to_state(
                selected_ref,
                state,
                self.context.module_name,
                context=self.context,
            )
            inventory = state.setdefault("pc", {}).setdefault("inventory", [])
            for item in state.get("module_starting_inventory", []):
                if item not in inventory:
                    inventory.append(item)

        self.context.world_store.update(apply)
        if selected_character is None:
            raise ValueError("无法读取所选调查员，请返回角色选择界面后重试。")
        return selected_character

    def has_save(self) -> bool:
        return has_save(context=self.context)

    def save(self, slot_id: str | None = None) -> str:
        """保存游戏。返回槽位 ID。"""
        return save_game(self.messages, slot_id, context=self.context)

    def list_saves(self) -> list[dict]:
        return list_saves(context=self.context)

    def load(self, slot_id: str | None = None) -> int | None:
        """读取存档并恢复世界状态快照。返回消息数量或 None。"""
        expected_revision = self.context.world_store.revision
        messages, snapshot = load_game(slot_id, context=self.context)
        if messages is None:
            return None
        # 恢复世界状态快照（防止线索污染）
        if snapshot:
            restore_snapshot(
                snapshot,
                context=self.context,
                expected_revision=expected_revision,
            )
            self._emit_unseen_discovered_handouts()
        # 保留当前 system prompt，恢复对话历史
        system_msg = self.messages[0] if self.messages else {"role": "system", "content": ""}
        self.messages = [system_msg] + messages[1:]
        # 重置记忆管理状态
        self._round_count = 0
        self._player_turn_count = 0
        self._last_summary_player_turn = 0
        self._tier_last_injected = -99
        self._last_turn_high_risk = False
        self._summary_token_estimate = 0
        self._loaded_optional_skills = set()
        self._preconfirmed_escalation = None
        return len(messages) - 1

    def settle_case(self, ending_type: str, title: str, summary: str) -> dict:
        """将已确认结局写入长期角色履历。"""
        result: dict = {"ok": False, "error": "案件结算未执行"}

        def settle(world_state: dict) -> None:
            nonlocal result
            result = settle_character_case(
                world_state,
                ending_type=ending_type,
                title=title,
                summary=summary,
                module_name=self.context.module_name,
                context=self.context,
            )

        try:
            self.context.world_store.update(settle)
        except Exception as exc:
            return {"ok": False, "error": f"写入世界状态失败: {exc}"}
        if result.get("ok"):
            self.save("slot_000")
        return result

    # ---- 流式 LLM ----

    def _stream_llm(
        self,
        model: str,
        system_overlay: str | None = None,
        system_prompt_override: str | None = None,
        enable_tools: bool = True,
        prompt_profile: str | None = None,
        temperature: float = 0.8,
        buffer_if_tools: bool = False,
        messages_override: list[dict] | None = None,
        _retry_on_empty: bool = True,
    ) -> tuple[str, list]:
        """流式调用；控制回合可缓冲并丢弃工具调用前的元确认语。"""
        started_at = time.monotonic()
        first_token_at: float | None = None
        if messages_override is None:
            self.messages = normalize_tool_message_history(self.messages)
            messages = self.messages
        else:
            messages = normalize_tool_message_history(
                [dict(message) for message in messages_override]
            )
        if system_prompt_override or system_overlay:
            messages = [dict(message) for message in messages]
            if system_prompt_override:
                if messages and messages[0].get("role") == "system":
                    messages[0]["content"] = system_prompt_override
                else:
                    messages.insert(0, {
                        "role": "system",
                        "content": system_prompt_override,
                    })
            if (
                system_overlay
                and messages
                and messages[0].get("role") == "system"
            ):
                messages[0]["content"] = (
                    f"{messages[0].get('content', '')}\n\n---\n\n{system_overlay}"
                )
            else:
                if system_overlay:
                    messages.insert(0, {
                        "role": "system",
                        "content": system_overlay,
                    })
        request_role = "combat" if system_overlay else "story"
        request_tools = (
            model_tools_for(request_role)
            if enable_tools and ENABLE_DYNAMIC_TOOLS
            else MODEL_TOOLS if enable_tools else []
        )
        role_chars: dict[str, int] = {}
        role_tokens: dict[str, int] = {}
        for message in messages:
            role = str(message.get("role") or "unknown")
            content = str(message.get("content") or "")
            role_chars[role] = role_chars.get(role, 0) + len(content)
            role_tokens[role] = role_tokens.get(role, 0) + estimate_text_tokens(content)
        tool_schema_json = json.dumps(
            request_tools,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        system_chars = role_chars.get("system", 0)
        tool_schema_chars = len(tool_schema_json)
        context_sections = {
            "system": {
                "chars": system_chars,
                "estimated_tokens": role_tokens.get("system", 0),
            },
            "history": {
                "chars": sum(
                    value for role, value in role_chars.items() if role != "system"
                ),
                "estimated_tokens": sum(
                    value for role, value in role_tokens.items() if role != "system"
                ),
            },
            "tool_schema": {
                "chars": tool_schema_chars,
                "estimated_tokens": estimate_text_tokens(tool_schema_json),
            },
        }
        kwargs = dict(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=4096,
            stream=True,
        )
        if enable_tools:
            kwargs["tools"] = request_tools
            kwargs["tool_choice"] = "auto"
        if ENABLE_STREAM_USAGE:
            kwargs["stream_options"] = {"include_usage": True}
        thinking_type = _thinking_type_for_request(model, request_role)
        if thinking_type:
            kwargs["extra_body"] = {"thinking": {"type": thinking_type}}
        try:
            stream = self.client.chat.completions.create(**kwargs)
        except Exception as e:
            if self.turn_cancellation_requested():
                raise TurnCancelledError(
                    "客户端已离开，模型请求已取消"
                ) from e
            self._append_model_diagnostic({
                "model": model,
                "role": request_role,
                "status": "request_error",
                "elapsed_ms": int((time.monotonic() - started_at) * 1000),
                "first_token_ms": None,
                "finish_reason": "request_error",
                "tool_count": 0,
                "message_count": len(messages),
                "context_sections": context_sections,
                "usage": {},
                "prompt_profile": prompt_profile or PROMPT_PROFILE,
                "thinking_mode": thinking_type or "provider",
                "error_type": type(e).__name__,
            })
            if _retry_on_empty:
                log_error(f"API 建立流失败，正在重试: {e}")
                time.sleep(0.4)
                return self._stream_llm(
                    model,
                    system_overlay=system_overlay,
                    system_prompt_override=system_prompt_override,
                    enable_tools=enable_tools,
                    prompt_profile=prompt_profile,
                    temperature=temperature,
                    buffer_if_tools=buffer_if_tools,
                    messages_override=messages_override,
                    _retry_on_empty=False,
                )
            log_error(f"API: {e}")
            self.cb.on_error(f"API 错误: {e}")
            return "", []

        full_text = ""
        pending_visible = ""
        initial_sentence_released = False
        tool_calls_acc: dict[int, dict] = {}
        finish_reason = None
        usage_data: dict = {}

        self._set_active_stream(stream)
        try:
            self.raise_if_turn_cancelled()
            for chunk in stream:
                self.raise_if_turn_cancelled()
                chunk_usage = _stream_usage_dict(getattr(chunk, "usage", None))
                if chunk_usage:
                    usage_data = chunk_usage
                if chunk.choices:
                    choice = chunk.choices[0]
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
                    delta = choice.delta
                    if delta is None:
                        continue
                    if first_token_at is None and (delta.content or delta.tool_calls):
                        first_token_at = time.monotonic()
                    if delta.content:
                        full_text += delta.content
                        if not buffer_if_tools:
                            if initial_sentence_released:
                                visible = _sanitize_visible_narrative(delta.content)
                                if visible:
                                    self.cb.on_narrative(visible)
                            else:
                                pending_visible += delta.content
                                complete, _remainder = _take_complete_sentences(
                                    pending_visible
                                )
                                if complete:
                                    visible = _sanitize_visible_narrative(
                                        pending_visible
                                    )
                                    if visible:
                                        self.cb.on_narrative(visible)
                                    pending_visible = ""
                                    initial_sentence_released = True
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {
                                    "id": "", "type": "function",
                                    "function": {"name": "", "arguments": ""}
                                }
                            acc = tool_calls_acc[idx]
                            if tc.id:
                                acc["id"] += tc.id
                            if tc.function:
                                if tc.function.name:
                                    acc["function"]["name"] += tc.function.name
                                if tc.function.arguments:
                                    acc["function"]["arguments"] += tc.function.arguments
            self.raise_if_turn_cancelled()
        except TurnCancelledError:
            raise
        except Exception as e:
            if self.turn_cancellation_requested():
                raise TurnCancelledError(
                    "客户端已离开，模型流已取消"
                ) from e
            if _retry_on_empty and not full_text and not tool_calls_acc:
                log_error(f"API 空流中断，正在重试: {e}")
                time.sleep(0.4)
                return self._stream_llm(
                    model,
                    system_overlay=system_overlay,
                    system_prompt_override=system_prompt_override,
                    enable_tools=enable_tools,
                    prompt_profile=prompt_profile,
                    temperature=temperature,
                    buffer_if_tools=buffer_if_tools,
                    messages_override=messages_override,
                    _retry_on_empty=False,
                )
            finish_reason = "transport_error"
            log_error(f"API 流式响应中断: {e}")
            self.cb.on_error("模型连接中断，已保留本轮收到的内容。")
        finally:
            self._clear_active_stream(stream)

        full_text = _sanitize_visible_narrative(full_text)
        if not buffer_if_tools and pending_visible:
            visible = _sanitize_visible_narrative(pending_visible)
            if visible:
                self.cb.on_narrative(visible)

        # 因 token 上限被截断时提示（叙述/选项可能不完整）
        if finish_reason == "length" and not tool_calls_acc:
            self.cb.on_error("（叙述过长被截断，请重试或继续）")

        tool_calls_list = [tool_calls_acc[i] for i in sorted(tool_calls_acc.keys())]
        if buffer_if_tools:
            if tool_calls_list:
                # 控制回合的首轮文本只是工具调用前导语，不应进入 UI 或存档叙事。
                full_text = ""
            elif full_text:
                # 模型没有调用工具时仍保留其正文，避免异常情况下出现空白回合。
                self.cb.on_narrative(full_text)
        elapsed = time.monotonic() - started_at
        first_token = (
            first_token_at - started_at if first_token_at is not None else None
        )
        log_model_call(
            model,
            request_role,
            elapsed,
            first_token,
            finish_reason,
            len(tool_calls_list),
            usage=usage_data,
            system_chars=system_chars,
            tool_schema_chars=tool_schema_chars,
            prompt_profile=prompt_profile or PROMPT_PROFILE,
            thinking_mode=thinking_type or "provider",
        )
        self._append_model_diagnostic({
            "model": model,
            "role": request_role,
            "status": "completed" if finish_reason != "transport_error" else "transport_error",
            "elapsed_ms": int(elapsed * 1000),
            "first_token_ms": int(first_token * 1000) if first_token is not None else None,
            "finish_reason": finish_reason,
            "tool_count": len(tool_calls_list),
            "message_count": len(messages),
            "context_sections": context_sections,
            "usage": dict(usage_data),
            "prompt_profile": prompt_profile or PROMPT_PROFILE,
            "thinking_mode": thinking_type or "provider",
        })
        if not full_text and not tool_calls_list and _retry_on_empty:
            log_error("API 返回空响应，正在重试一次")
            time.sleep(0.4)
            return self._stream_llm(
                model,
                system_overlay=system_overlay,
                system_prompt_override=system_prompt_override,
                enable_tools=enable_tools,
                prompt_profile=prompt_profile,
                temperature=temperature,
                buffer_if_tools=buffer_if_tools,
                messages_override=messages_override,
                _retry_on_empty=False,
            )
        return full_text, tool_calls_list

    def _combat_state(self) -> dict:
        """Read the authoritative combat state for graph routing and prompt overlay."""
        try:
            world = self.context.world_store.load()
        except Exception:
            return {}
        combat = world.get("combat_state")
        return combat if isinstance(combat, dict) else {}

    def _combat_active(self) -> bool:
        return bool(self._combat_state().get("active"))

    def _preflight_player_escalation(self, content: str) -> str | None:
        """Confirm explicit violence before the first model token can narrate it."""
        try:
            world = self.context.world_store.load()
        except Exception:
            return content
        preview = preview_player_escalation(world, content)
        if preview is None:
            return content

        decision = preview["decision"]
        selected = self.cb.on_decision(decision)
        valid_options = {
            option.get("id")
            for option in decision.get("options", [])
            if isinstance(option, dict)
        }
        if selected not in valid_options:
            selected = decision.get("default_option")
        authorization = preview["authorization"]
        if selected != authorization["confirm_option"]:
            self.messages.append({
                "role": "user",
                "content": f"[玩家在行动发生前取消，场景状态不变] 原提议：{content}",
            })
            self.save("slot_000")
            return None

        self._preconfirmed_escalation = authorization
        return f"{content}\n{preview['prompt_suffix']}"

    def _preconfirmed_option(self, decision: dict) -> str | None:
        authorization = self._preconfirmed_escalation
        if not isinstance(authorization, dict):
            return None
        if decision.get("kind") != authorization.get("kind"):
            return None
        expected_target = authorization.get("target_id")
        if expected_target and decision.get("target_id") != expected_target:
            return None
        self._preconfirmed_escalation = None
        return authorization.get("confirm_option")

    def _combat_system_overlay(self) -> str:
        return build_combat_overlay(self._combat_state())

    def _resolve_action_check(
        self,
        content: str,
        preferred_skill: str | None = None,
    ) -> dict | None:
        """Resolve an explicit investigative action before narration starts."""
        try:
            world = self.context.world_store.load()
        except Exception:
            return None
        check = infer_action_check(content, world)
        if preferred_skill:
            from .action_checks import ActionCheck

            check = ActionCheck(
                skill=preferred_skill,
                reason="模组发现规则要求检定",
            )
        if check is None:
            return None

        output = self._execute_tool("skill_check", {"skill": check.skill})
        try:
            result = json.loads(output)
        except json.JSONDecodeError:
            log_error(f"行动预检无法解析 {check.skill} 结果: {output[:160]}")
            return None
        summary = dice_summary(output)
        if summary:
            self.cb.on_dice(summary, result)
        result["reason"] = check.reason
        return result

    def _match_discoveries(
        self,
        content: str,
    ) -> tuple[list[DiscoveryMatch], str | None]:
        try:
            world = self.context.world_store.load()
        except Exception:
            return [], None
        matches = match_discovery_rules(content, world)
        return matches, preferred_check_skill(matches, world)

    def _plan_player_action(self, content: str) -> ActionResolution:
        """Return the single authority boundary consumed by this turn."""
        world = self.context.world_store.load()
        return plan_player_action(content, world)

    def _emit_sanity_result(self, output: str) -> None:
        try:
            data = json.loads(output)
            roll = int(data["san_roll"])
            before = int(data["san_before"])
            loss = int(data["actual_loss"])
            success = bool(data["san_check_success"])
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            return
        self.cb.on_dice(
            f"理智检定 {roll} vs {before}，{'成功' if success else '失败'}，SAN -{loss}",
            {
                "spec": "d100",
                "sides": 100,
                "count": 1,
                "rolls": [roll],
                "total": roll,
                "sanity": True,
                "success": success,
                "loss": loss,
            },
        )

    def _resolve_luck_check(self, difficulty: str) -> dict | None:
        output = self._execute_tool("luck_check", {})
        try:
            result = json.loads(output)
            roll = int(result["d100_roll"])
            value = int(result["skill_value"])
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            return None
        divisor = {"regular": 1, "hard": 2, "extreme": 5}.get(difficulty, 1)
        threshold = max(1, value // divisor)
        result["luck_value"] = value
        result["skill"] = "luck"
        result["skill_name"] = "幸运"
        result["skill_value"] = threshold
        result["success"] = roll <= threshold
        if not result["success"] and result.get("level") != "fumble":
            result["level"] = "failure"
        result["difficulty"] = difficulty
        summary = dice_summary(json.dumps(result, ensure_ascii=False))
        if summary:
            self.cb.on_dice(summary, result)
        return result

    def _resolve_discoveries(
        self,
        matches: list[DiscoveryMatch],
        check_result: dict | None,
    ) -> list[dict]:
        """Commit module-authored discovery effects before story generation."""
        resolved: list[dict] = []
        for match in matches:
            rule = match.rule
            required_skill = str(rule.get("skill") or "")
            if rule.get("requires_success") and (
                not check_result
                or not check_result.get("success")
                or (
                    rule.get("check_type") != "luck"
                    and str(check_result.get("skill") or "") != required_skill
                )
            ):
                resolved.append({
                    "clue_id": match.clue_id,
                    "discovered": False,
                    "reason": "required_check_failed",
                })
                continue

            npc_reveals = rule.get("npc_reveals", [])
            if not isinstance(npc_reveals, list):
                npc_reveals = []
            severity = str(rule.get("sanity_severity") or "")
            if severity:
                from .llm import tension

                self.cb.on_tension(tension("sanity"), "sanity")
                output = self._execute_tool("sanity_event", {
                    "description": str(match.clue.get("text") or ""),
                    "severity": severity,
                    "clue_id": match.clue_id,
                    "npc_reveals": npc_reveals,
                })
                self._emit_sanity_result(output)
                try:
                    sanity = json.loads(output)
                except json.JSONDecodeError:
                    sanity = {}
                resolved.append({
                    "clue_id": match.clue_id,
                    "discovered": True,
                    "text": match.clue.get("text"),
                    "type": match.clue.get("type"),
                    "sanity": {
                        key: sanity.get(key)
                        for key in (
                            "san_before", "san_after", "san_roll",
                            "san_check_success", "actual_loss",
                        )
                    },
                    "npc_reveals": npc_reveals,
                })
                continue

            self._execute_tool("state_add_clue", {
                "text": "",
                "category": match.clue.get("category", "investigation"),
                "clue_id": match.clue_id,
            })
            for reveal in npc_reveals:
                if isinstance(reveal, dict):
                    self._execute_tool("npc_reveal", reveal)
            resolved.append({
                "clue_id": match.clue_id,
                "discovered": True,
                "text": match.clue.get("text"),
                "type": match.clue.get("type"),
                "npc_reveals": npc_reveals,
            })
        return resolved

    def _resolve_scene_transition(self, content: str) -> str | None:
        """Commit an unambiguous player move before generating its narration."""
        try:
            world = self.context.world_store.load()
        except Exception:
            return None
        scene_id = infer_scene_transition(content, world)
        if scene_id is None:
            return None
        encounter = resolve_scene_encounters(
            scene_id,
            world,
            luck_check=self._resolve_luck_check,
        )
        self._encounter_resolution = encounter
        output = self._execute_tool("state_set", {
            "path": "current_scene.id",
            "value": json.dumps(scene_id, ensure_ascii=False),
            "npcs_present": list(encounter.present_npc_ids),
        })
        for outcome in encounter.outcomes:
            if not outcome.cached:
                self._execute_tool("state_set", {
                    "path": (
                        f"encounter_history.{scene_id}.{outcome.encounter_id}"
                    ),
                    "value": json.dumps({
                        "present": outcome.present,
                        "availability": outcome.availability,
                        "check_result": outcome.check_result,
                    }, ensure_ascii=False),
                })
            npc_index = next((
                index
                for index, npc in enumerate(world.get("npcs", []))
                if isinstance(npc, dict) and str(npc.get("id") or "") == outcome.npc_id
            ), None)
            if npc_index is None:
                continue
            previous = str(world["npcs"][npc_index].get("current_location") or "")
            if outcome.present and previous != scene_id:
                self._execute_tool("state_set", {
                    "path": f"npcs.{npc_index}.current_location",
                    "value": json.dumps(scene_id),
                })
            elif not outcome.present and previous == scene_id:
                self._execute_tool("state_set", {
                    "path": f"npcs.{npc_index}.current_location",
                    "value": '"unknown"',
                })
        try:
            result = json.loads(output)
        except json.JSONDecodeError:
            return None
        return scene_id if result.get("ok") else None

    def _turn_prelude(
        self,
        scene_id: str | None,
        discovery_matches: list[DiscoveryMatch],
    ) -> str:
        """Build authored, player-visible setup that must precede rule effects."""
        parts: list[str] = []
        if scene_id:
            try:
                world = self.context.world_store.load()
                scene = (world.get("scene_catalog", {}) or {}).get(scene_id, {})
            except Exception:
                scene = {}
            name = str(scene.get("name") or "").strip()
            description = str(scene.get("description") or "").strip()
            if name:
                arrival = f"你前往{name}。"
                if description:
                    arrival += description
                parts.append(arrival)

        for match in discovery_matches:
            approach = str(match.rule.get("approach_text") or "").strip()
            if approach and approach not in parts:
                parts.append(approach)
        return "\n\n".join(parts)

    def _authoritative_turn_context(
        self,
        check_result: dict | None = None,
        resolved_discoveries: list[dict] | None = None,
    ) -> str:
        """Build a compact, private snapshot that anchors one story turn."""
        try:
            world = self.context.world_store.load()
        except Exception:
            return ""
        pc = world.get("pc", {})
        scene = world.get("current_scene", {})
        present_npc_ids = set(scene.get("npcs_present", []))
        present_npcs = []
        for npc in world.get("npcs", []):
            if not isinstance(npc, dict) or npc.get("id") not in present_npc_ids:
                continue
            revealed = npc.get("revealed") or {}
            present_npcs.append({
                "id": npc.get("id"),
                "name": npc.get("name"),
                "visible_tags": npc.get("visible_tags", []),
                "revealed_level": revealed.get("level", 0),
                "revealed_entries": revealed.get("entries", []),
                "keeper_private": {
                    "disposition": npc.get("disposition"),
                },
            })
        known_clues = []
        clue_groups = world.get("clues_found", {})
        if isinstance(clue_groups, dict):
            for category, clues in clue_groups.items():
                if not isinstance(clues, list):
                    continue
                for clue in clues[-8:]:
                    if isinstance(clue, dict):
                        known_clues.append({
                            "id": clue.get("catalog_id") or clue.get("id"),
                            "category": category,
                            "text": str(clue.get("text") or "")[:220],
                        })
        scene_clues = []
        clue_catalog = world.get("clue_catalog", {})
        if isinstance(clue_catalog, dict):
            for clue_id, clue in clue_catalog.items():
                if not isinstance(clue, dict):
                    continue
                related_scenes = clue.get("related_scenes", [])
                if (
                    clue.get("source") != scene.get("id")
                    and scene.get("id") not in related_scenes
                ):
                    continue
                scene_clues.append({
                    "id": clue.get("id") or clue_id,
                    "text": str(clue.get("text") or "")[:300],
                    "category": clue.get("category"),
                    "type": clue.get("type"),
                    "discovery_notes": str(
                        clue.get("discovery_notes") or ""
                    )[:300],
                })
        module_rules = world.get("module_rules") or {}
        latest_content = (
            str(self.messages[-1].get("content") or "")
            if self.messages
            else ""
        )
        opening = (
            str(world.get("module_opening") or "")[:1600]
            if latest_content.startswith(self.CONTROL_MESSAGE_PREFIX)
            and "开始新游戏" in latest_content
            else ""
        )
        resolved_discoveries = resolved_discoveries or []
        action_resolution = getattr(self, "_action_resolution", None)
        encounter_resolution = getattr(self, "_encounter_resolution", None)
        arrival_only = bool(action_resolution and action_resolution.is_arrival)
        opening_public_facts = (
            [str(clue.get("text") or "")[:400] for clue in known_clues]
            if opening
            else []
        )
        newly_confirmed_facts = [
            str(discovery.get("text") or "")[:400]
            for discovery in resolved_discoveries
            if isinstance(discovery, dict)
            and discovery.get("discovered")
            and discovery.get("text")
        ]
        payload = {
            "module": world.get("module_meta") or {"id": world.get("module")},
            "pc": {
                "name": pc.get("name"),
                "occupation": pc.get("occupation"),
                "hp": pc.get("hp"),
                "max_hp": pc.get("max_hp"),
                "san": pc.get("san"),
                "max_san": pc.get("max_san"),
                "inventory": pc.get("inventory", []),
                "conditions": pc.get("conditions", []),
            },
            "current_scene": {
                "id": scene.get("id"),
                "name": scene.get("name"),
                "description": str(scene.get("description") or "")[:900],
                "exits": scene.get("exits", []),
                "npcs_present": scene.get("npcs_present", []),
                "npc_public_state": present_npcs,
            },
            "flags": world.get("flags", {}),
            "case_clocks": world.get("case_clocks", {}),
            "recent_known_clues": known_clues[-20:],
            "available_scene_clues": scene_clues[:20],
            "sanity_triggers": module_rules.get("sanity_triggers", []),
            "keeper_memory": {
                "goals_and_plans": str(
                    (world.get("private_memory") or {}).get("goals_and_plans") or ""
                )[:700],
                "inference_notes": str(
                    (world.get("private_memory") or {}).get("inference_notes") or ""
                )[:700],
            },
            "resolved_check": check_result,
            "resolved_discoveries": resolved_discoveries,
            "resolved_encounter": (
                {
                    "scene_id": encounter_resolution.scene_id,
                    "present_npc_ids": list(encounter_resolution.present_npc_ids),
                    "outcomes": [
                        {
                            "npc_id": outcome.npc_id,
                            "present": outcome.present,
                            "availability": outcome.availability,
                        }
                        for outcome in encounter_resolution.outcomes
                    ],
                }
                if isinstance(encounter_resolution, SceneEncounterResolution)
                else None
            ),
            "narrative_fact_scope": {
                "closed_world_for_this_action": bool(
                    opening or arrival_only
                    or check_result is not None or resolved_discoveries
                ),
                "mode": "module_opening" if opening else "normal_turn",
                "opening_public_facts": opening_public_facts,
                "uncatalogued_opening_details": (
                    "flavor_only_never_persist_as_clue_or_state"
                    if opening
                    else ""
                ),
                "newly_confirmed_facts": newly_confirmed_facts,
                "unlisted_observations": "本行动没有额外可验证发现",
                "action_resolution": (
                    action_resolution.public_contract() if action_resolution else None
                ),
                "arrival_only": arrival_only,
            },
            "module_opening": opening,
        }
        return (
            "[引擎权威状态｜仅供守秘人，不得复述]\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            + "\n约束：随身物品仅以上述 inventory 为准；未列出的相机、通信设备、证物均不存在。"
            "时代技术必须服从 module.era。只有 resolved_check 中的骰点是本行动真实检定；"
            "若其为空，禁止自行编造检定名、技能值、骰点、SAN 点数或成败。"
            "resolved_discoveries 是本行动开始叙述前已完成的权威结算；必须按其结果叙述，"
            "不得为其中的线索、SAN、flag 或 NPC 揭示再次调用工具。"
            "narrative_fact_scope.closed_world_for_this_action 为 true 时，newly_confirmed_facts"
            "是本行动新发现的完整事实边界，不是扩写提纲；只能改写表达，不能增加可检验细节。"
            "narrative_fact_scope.arrival_only 为 true 时，本轮只叙述抵达、环境与接洽在场人物；"
            "玩家所述出行目的不是已经完成的调查动作。不得打开容器、展示或检查尸体、阅读文件，"
            "也不得触发 SAN、线索、NPC 秘密或相关 flag；应把明确调查动作留作抵达后的下一选择。"
            "narrative_fact_scope.mode 为 module_opening 时，module_opening、opening_public_facts、"
            "current_scene.description 与 npc_public_state 是完整的开场权威调查事实；允许即兴"
            "扩写非核心细节，但未被这些字段或 clue_catalog 支持的内容永远只是叙事点缀，"
            "不得转写为线索、证据、NPC 揭示、检定目标或 flag，也不得提前公开幕后秘密。"
            "玩家检查了未列出的部位或对象时，只能说明没有额外值得注意的发现；"
            "不得据此创造新选项、新物品或后续检定目标。available_scene_clues 只是守秘人候选，"
            "未出现在 resolved_discoveries 中就仍未发现。"
            "npc_public_state.keeper_private 仅供守秘人判断语气，不代表玩家已知；"
            "只有 visible_tags、revealed_entries 或本轮结算明确释放的内容可以直接说出。"
            "叙事中确实完成的场景、物品、线索、NPC 揭示和结局变化必须调用工具落账。"
            "同一回合互不依赖的工具必须在一条 assistant 消息中批量调用；"
            "线索、NPC 揭示和标志不依赖 SAN 骰点时，不得等 SAN 返回后再开新工具轮。"
            "恐怖发现若对应 available_scene_clues，必须把稳定 clue_id 和同事件的 NPC"
            "揭示直接放进 sanity_event；该事务会提交线索、flag_effects 与 NPC 信息。"
            "若本轮需要任何工具，先完成全部工具调用，工具返回后再一次性输出正文；"
            "不要在工具调用前叙述事件，也不要重述本轮已经输出过的段落。"
        )

    def _reconcile_turn(
        self,
        player_action: str,
        narrative: str,
        executed_tools: list[dict] | None = None,
    ) -> dict:
        return reconcile_turn(
            self,
            player_action=player_action,
            narrative=narrative,
            executed_tools=executed_tools,
        )

    def _reconcile_narrative_entities(self, narrative: str) -> list[str]:
        return reconcile_narrative_entities(self, narrative)

    def _turn_needs_model_audit(
        self,
        executed_tools: list[dict] | None,
        *,
        player_action: str = "",
        narrative: str | None = None,
    ) -> bool:
        return turn_needs_model_audit(
            executed_tools,
            player_action=player_action,
            narrative=narrative,
        )

    def _resume_pending_combat_decision(self) -> None:
        """Re-open a persisted combat decision before asking either agent to continue."""
        pending = self._combat_state().get("pending_decision")
        if not isinstance(pending, dict) or not pending.get("id"):
            return
        decision = {key: value for key, value in pending.items() if key != "action"}
        selected = self.cb.on_decision(decision)
        valid_options = {
            option.get("id")
            for option in decision.get("options", [])
            if isinstance(option, dict)
        }
        if selected not in valid_options:
            selected = decision.get("default_option")
        result = execute_function(
            "combat_decide",
            {
                "decision_id": decision.get("id", ""),
                "option_id": selected or "",
            },
            context=self.context,
        )
        self.messages.append({
            "role": "user",
            "content": f"[恢复的战斗决定已结算] {result}",
        })

    # ---- 工具执行 ----

    def _execute_model_tool(
        self,
        name: str,
        args: dict,
        *,
        player_action: str = "",
    ) -> str:
        """Validate model-proposed authored effects before executing them.

        The story model may classify a free-form action, but an authored clue
        with discovery rules remains gated by the current scene and the
        player's actual action.  This keeps model judgement useful without
        letting it bypass module progression contracts.
        """
        action_resolution = getattr(self, "_action_resolution", None)
        arrival_only = bool(action_resolution and action_resolution.is_arrival)
        if arrival_only and name in {"state_add_clue", "sanity_event"}:
            return json.dumps({
                "ok": False,
                "error": "arrival_turn_effect_not_authorized",
                "instruction": (
                    "跨场景抵达回合不能提交线索或理智事件；"
                    "先叙述抵达与接洽，把实际调查留给玩家下一次明确行动。"
                ),
            }, ensure_ascii=False)
        if arrival_only and name == "state_set":
            path = str(args.get("path") or "")
            try:
                world = self.context.world_store.load()
            except Exception:
                world = {}
            protected_flags = {
                f"flags.{key}"
                for clue in (world.get("clue_catalog", {}) or {}).values()
                if isinstance(clue, dict)
                for key in (clue.get("flag_effects", {}) or {})
            }
            if path in protected_flags:
                return json.dumps({
                    "ok": False,
                    "error": "arrival_turn_flag_not_authorized",
                    "path": path,
                    "instruction": "该标志由目录线索发现流程提交，抵达场景不能提前修改。",
                }, ensure_ascii=False)
        if name not in {"state_add_clue", "sanity_event"}:
            return self._execute_tool(name, args)
        try:
            world = self.context.world_store.load()
        except Exception:
            return self._execute_tool(name, args)

        catalog = world.get("clue_catalog", {})
        if not isinstance(catalog, dict):
            return self._execute_tool(name, args)
        clue_id = str(args.get("clue_id") or "")
        asset_id = str(args.get("asset_id") or "")
        if not clue_id and asset_id:
            clue_id = next((
                str(candidate_id)
                for candidate_id, clue in catalog.items()
                if isinstance(clue, dict)
                and str((clue.get("asset") or {}).get("id") or "") == asset_id
            ), "")
        clue = catalog.get(clue_id) if clue_id else None
        rules = clue.get("discovery_rules", []) if isinstance(clue, dict) else []
        if not isinstance(rules, list) or not rules:
            return self._execute_tool(name, args)

        known_ids = {
            str(known.get("catalog_id") or known.get("id") or "")
            for clues in world.get("clues_found", {}).values()
            if isinstance(clues, list)
            for known in clues
            if isinstance(known, dict)
        }
        action_matches = {
            match.clue_id
            for match in match_discovery_rules(player_action, world)
        }
        if clue_id not in known_ids:
            reason = (
                "catalog_clue_not_resolved"
                if clue_id in action_matches
                else "catalog_clue_not_authorized"
            )
            return json.dumps({
                "ok": False,
                "error": reason,
                "clue_id": clue_id,
                "instruction": (
                    "该目录线索尚未由引擎发现流程结算；"
                    "若只是NPC口述的新信息，可改为不带 clue_id/asset_id 的普通线索。"
                ),
            }, ensure_ascii=False)
        return self._execute_tool(name, args)

    def _execute_tool(self, name: str, args: dict) -> str:
        """执行工具。确认类工具通过回调与玩家交互。"""
        if name == "state_set" and args.get("path") == "current_scene.id":
            try:
                scene_id = json.loads(args.get("value", "\"\""))
            except (TypeError, json.JSONDecodeError):
                scene_id = args.get("value", "")
            try:
                world = self.context.world_store.load()
                scene = world.get("scene_catalog", {}).get(scene_id)
            except Exception:
                world = {}
                scene = None
            if isinstance(scene, dict):
                requested_npcs = args.get("npcs_present")
                actual_npcs = (
                    [str(npc_id) for npc_id in requested_npcs]
                    if isinstance(requested_npcs, list)
                    else [
                        str(npc.get("id"))
                        for npc in world.get("npcs", [])
                        if isinstance(npc, dict)
                        and npc.get("id")
                        and str(npc.get("current_location") or "") == str(scene_id)
                    ]
                )
                args = {
                    "path": "current_scene",
                    "value": json.dumps(
                        {
                            **{
                                key: value
                                for key, value in scene.items()
                                if key not in {"document", "npcs_present"}
                            },
                            "npcs_present": actual_npcs,
                        },
                        ensure_ascii=False,
                    ),
                }

        if name == "sanity_event":
            before = self.context.world_store.load()
            before_clues = {
                str(clue.get("catalog_id") or clue.get("id") or "")
                for clues in before.get("clues_found", {}).values()
                if isinstance(clues, list)
                for clue in clues
                if isinstance(clue, dict)
            }
            before_flags = dict(before.get("flags", {}))
            result = execute_function(name, args, context=self.context)
            clue_id = str(args.get("clue_id") or "")
            catalog = before.get("clue_catalog", {})
            if clue_id and isinstance(catalog, dict) and clue_id in catalog:
                clue = catalog[clue_id]
                if isinstance(clue, dict):
                    self._execute_tool("state_add_clue", {
                        "text": "",
                        "category": clue.get("category", "investigation"),
                        "clue_id": clue_id,
                    })
            committed_npcs = []
            for reveal in args.get("npc_reveals", [])[:8]:
                if not isinstance(reveal, dict):
                    continue
                npc_id = str(reveal.get("npc_id") or "")
                entry_text = str(reveal.get("entry_text") or "").strip()
                try:
                    tier = int(reveal.get("tier") or 1)
                except (TypeError, ValueError):
                    continue
                if not npc_id or not entry_text or tier not in {1, 2, 3}:
                    continue
                reveal_result = self._execute_tool("npc_reveal", {
                    "npc_id": npc_id,
                    "tier": tier,
                    "entry_text": entry_text,
                })
                try:
                    reveal_data = json.loads(reveal_result)
                except json.JSONDecodeError:
                    continue
                if reveal_data.get("ok"):
                    committed_npcs.append(npc_id)
            self._handle_tool_handouts(name, args, result)
            after = self.context.world_store.load()
            after_clues = {
                str(clue.get("catalog_id") or clue.get("id") or "")
                for clues in after.get("clues_found", {}).values()
                if isinstance(clues, list)
                for clue in clues
                if isinstance(clue, dict)
            }
            changed_flags = {
                key: value
                for key, value in after.get("flags", {}).items()
                if before_flags.get(key) != value
            }
            try:
                data = json.loads(result)
            except json.JSONDecodeError:
                return result
            data["auto_committed"] = {
                "clue_ids": sorted(after_clues - before_clues),
                "flags": changed_flags,
                "npc_ids": sorted(set(committed_npcs)),
            }
            if (
                data["auto_committed"]["clue_ids"]
                or changed_flags
                or committed_npcs
            ):
                data["instruction"] = (
                    "以上线索、标志与 NPC 揭示已由引擎提交，不要重复调用状态工具。"
                )
            return json.dumps(data, ensure_ascii=False)

        if name == "suggest_check":
            info = {
                "skill": args.get("skill", "?"),
                "attribute": args.get("attribute", "?"),
                "dc": args.get("dc", 15),
                "dc_label": args.get("dc_label", "中等"),
                "description": args.get("description", ""),
            }
            confirmed = self.cb.on_suggest(info)
            if confirmed:
                return json.dumps({"confirmed": True, "skill": info["skill"],
                                   "attribute": info["attribute"], "dc": info["dc"]})
            else:
                return json.dumps({"confirmed": False, "reason": "玩家选择不冒险"})

        if name == "show_handout":
            result = execute_function(name, args, context=self.context)
            try:
                info = json.loads(result)
                if info.get("found"):
                    if not info.get("already_seen") and info.get("asset_data_uri"):
                        self.cb.on_handout(info)
            except Exception:
                pass
            return result

        if name == "state_add_clue":
            result = execute_function(name, args, context=self.context)
            self._handle_tool_handouts(name, args, result)
            return result

        if name in {"combat_start", "combat_action"}:
            result = execute_function(name, args, context=self.context)
            try:
                info = json.loads(result)
            except json.JSONDecodeError:
                return result
            if not info.get("requires_decision"):
                return result

            decision = info.get("decision") or {}
            selected = self._preconfirmed_option(decision)
            if selected is None:
                selected = self.cb.on_decision(decision)
            valid_options = {
                option.get("id")
                for option in decision.get("options", [])
                if isinstance(option, dict)
            }
            if selected not in valid_options:
                selected = decision.get("default_option")
            return execute_function(
                "combat_decide",
                {
                    "decision_id": decision.get("id", ""),
                    "option_id": selected or "",
                },
                context=self.context,
            )

        result = execute_function(name, args, context=self.context)
        self._handle_tool_handouts(name, args, result)
        return result

    # ---- 按需 Skill 加载提示 ----

    # 玩家消息关键词 → 对应按需 skill（引擎侧主动检测，不依赖模型判断）
    _KEYWORD_SKILL_MAP = {
        "skills/keeper/keeper_items.skill": [
            "鸣枪", "开枪", "射击", "扣动扳机", "子弹", "装弹", "换弹",
            "喝下", "服用", "点燃", "烧掉", "使用钥匙", "打开手电筒",
            "急救包", "消耗道具", "使用物品",
        ],
        "skills/keeper/keeper_combat.skill": [
            "开枪", "射击", "攻击", "挥拳", "拔枪", "持枪", "用枪", "枪指", "瞄准",
            "威胁", "拔刀", "砍", "刺", "砸",
            "战斗", "搏斗", "斗殴", "反击", "闪避", "伤害", "受伤", "倒地",
            "武器", "手枪", "左轮", "刀", "棍", "枪", "弹药",
        ],
        "skills/keeper/keeper_psychology.skill": [
            "疯狂", "崩溃", "失控", "幻觉", "尖叫", "发疯", "恐惧症", "躁狂",
        ],
        "skills/keeper/keeper_magic.skill": [
            "魔法", "咒语", "施法", "仪式", "召唤", "神话典籍", "诅咒", "克苏鲁神话",
        ],
    }

    def _load_optional_skill(self, skill_path: str):
        """按需把 skill 文件内容直接注入上下文——不再提示模型 read_file 多跑一整轮。
        读不到就静默跳过，不阻塞回合。"""
        if skill_path in self._loaded_optional_skills:
            return
        self._loaded_optional_skills.add(skill_path)
        try:
            content = (self.context.project_root / skill_path).read_text(encoding="utf-8")
        except Exception:
            return  # 文件读不到就算了，别打断回合
        self.append_control_instruction(
            f"以下 Skill 规则已经由引擎加载，请在本回合应用：{skill_path}\n\n{content}"
        )

    def _maybe_hint_optional_skill(self, tool_name: str):
        """工具调用后,若该工具对应一个按需 skill 且本会话尚未加载,直接注入其内容。"""
        skill_path = OPTIONAL_SKILL_HINTS.get(tool_name)
        if skill_path:
            self._load_optional_skill(skill_path)

    def _detect_content_skill_hint(self, content: str):
        """检测玩家消息内容,若包含战斗/魔法/疯狂等关键词且对应 skill 未加载,直接注入。
        这是"第三重保险"——不依赖模型判断,引擎直接检测。"""
        for skill_path, keywords in self._KEYWORD_SKILL_MAP.items():
            if skill_path in self._loaded_optional_skills:
                continue
            if any(kw in content for kw in keywords):
                self._load_optional_skill(skill_path)

    # ---- 记忆管理 ----

    def _estimate_tokens(self) -> int:
        """粗略估算消息列表的 token 数。中文约 1.5 字符/token，英文约 4 字符/token。"""
        total = 0
        for m in self.messages:
            content = m.get("content", "") or ""
            # 混合文本粗略估算
            total += len(content) // 2  # 取中值 ~2 chars/token
            if "tool_calls" in m:
                total += len(json.dumps(m["tool_calls"])) // 2
        self._summary_token_estimate = total
        return total

    def _should_summarize(self) -> bool:
        """是否到达玩家回合压缩周期。"""
        return (
            self._player_turn_count > 0
            and self._player_turn_count - self._last_summary_player_turn >= self.SUMMARY_PLAYER_TURN_INTERVAL
        )

    def _maybe_summarize_after_turn(self):
        """在本轮叙事和 done 事件之后静默压缩，尽量让玩家无感。"""
        if not self._should_summarize():
            return
        current_turn = self._player_turn_count
        changed = self._summarize_history(silent=True)
        self._last_summary_player_turn = current_turn
        if changed:
            self.save("slot_000")

    def _summarize_history(self, silent: bool = False) -> bool:
        """压缩旧消息。优先 GLM-4 Flash（免费快速），失败则 DeepSeek Pro（可靠），
        都失败才降级为简单截断。

        关键设计：
        - 不因消息数少就跳过——只要 token 超标且有可压缩的旧消息就尝试。
          （system prompt 本身很大时，即使对话少，token 仍可能超标，
           此时仍应压缩对话部分以腾出空间。）
        - JSON 解析失败时降级接受纯文本摘要，而非丢弃整段输出。
        """
        from .llm import _get_glm

        KEEP_RECENT = self.SUMMARY_KEEP_RECENT_MESSAGES
        # 只在"旧消息太少不值得压缩"时跳过：系统消息+开场prompt之后几乎没有对话
        cutoff = len(self.messages) - KEEP_RECENT
        while cutoff > 1 and self.messages[cutoff].get("role") != "user":
            cutoff -= 1
        if cutoff <= 1:
            return False  # 没有足够的旧消息可压缩

        old_messages = self.messages[1:cutoff]
        recent_messages = self.messages[cutoff:]
        system_msg = self.messages[0]
        if len(old_messages) < 3:
            return False  # 旧消息太少，压缩意义不大

        # 构建旧消息文本（复用于各级摘要）
        old_text = self._build_summary_input(old_messages)

        # 第一级：GLM-4 Flash（免费）
        glm = _get_glm()
        if glm is not None:
            summary = self._try_model_summary(glm, "glm-4-flash-250414", old_text)
            if summary is not None:
                self._apply_summary(system_msg, summary, recent_messages, "GLM-4 Flash", silent=silent)
                return True

        # 第二级：DeepSeek Pro（付费但可靠）
        if not silent:
            self.cb.on_tension("正在用 DeepSeek Pro 压缩上下文……", "pro")
        summary = self._try_model_summary(
            self.client, self.judgement_model, old_text
        )
        if summary is not None:
            self._apply_summary(system_msg, summary, recent_messages, "DeepSeek Pro", silent=silent)
            return True

        # 第三级：简单截断（最后手段）
        dropped = len(old_messages)
        note = {
            "role": "user",
            "content": (
                f"（上下文压缩——摘要模型均不可用，已丢弃最早的 {dropped} 条消息。"
                "当前世界状态保存在 world_state.json 中，"
                "请调用 state_clues() 和 state_npcs() 查询线索和 NPC 揭示状态，然后继续。）"
            )
        }
        self.messages = [system_msg, note] + recent_messages
        self._summary_token_estimate = self._estimate_tokens()
        if not silent:
            self.cb.on_glm_summary(f"📋 截断 {dropped} 条旧消息（摘要模型不可用）。")
        return True

    def _apply_summary(self, system_msg, summary, recent_messages, model_name, silent: bool = False):
        """将摘要应用到消息列表。"""
        summary_msg = {
            "role": "user",
            "content": (
                "（会话摘要——此前冒险的关键记录已压缩如下。"
                "技能检定、已发现线索、NPC互动记录均已保留。\n\n"
                f"{summary}\n\n"
                "——摘要结束。以下是最近的对话——）"
            )
        }
        self.messages = [system_msg, summary_msg] + recent_messages
        self._summary_token_estimate = self._estimate_tokens()
        log_summary(model_name, "成功")
        if not silent:
            self.cb.on_glm_summary(f"📋 上下文已压缩（{model_name}）。")

    def _build_summary_input(self, old_messages: list) -> str:
        """从旧消息构建摘要输入文本。截断 tool 输出，保留 user/assistant 核心内容。"""
        parts = []
        for m in old_messages:
            role = m.get("role", "?")
            content = m.get("content", "") or ""
            if not content.strip():
                continue
            if role == "tool":
                content = content[:200] + "..." if len(content) > 200 else content
            elif role in ("user", "assistant"):
                content = content[:500] + "..." if len(content) > 500 else content
            else:
                continue
            parts.append(f"[{role}]: {content}")

        old_text = "\n".join(parts)
        MAX_INPUT = 6000
        if len(old_text) > MAX_INPUT:
            half = MAX_INPUT // 2
            old_text = old_text[:half] + "\n...(中间内容省略)...\n" + old_text[-half:]
        return old_text

    def _try_model_summary(self, client, model: str, old_text: str) -> str | None:
        """用指定模型生成摘要。返回摘要文本或 None。

        尝试2次（API偶发网络错误时重试）。JSON 解析失败时降级接受纯文本摘要，
        不再因格式问题丢弃整段输出。
        """
        prompt = (
            "你是TRPG记录员。将以下对话历史压缩为结构化摘要。\n"
            "要求: 按时间顺序列出关键事件(episodic)、PC已知信息(pc_knowledge)、"
            "已发现线索(revealed_clues)、当前目标(current_objective)、"
            "最后场景(last_scene)。保留技能检定和骰子结果，不编造信息。\n"
            "优先输出JSON格式，但内容完整性比格式正确更重要。\n\n"
            f"{old_text}"
        )

        for attempt in range(2):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "你是TRPG记录员。尽量输出JSON，但务必保证内容完整。"},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3,
                    max_tokens=3000,
                )
                raw = resp.choices[0].message.content.strip()
            except Exception:
                if attempt == 0:
                    continue  # 重试一次
                return None

            # 优先解析为 JSON
            parsed = self._parse_summary_json(raw)
            if parsed is not None:
                return parsed

            # JSON 解析失败：降级接受纯文本摘要（只要有实质内容）
            if len(raw) > 50 and attempt == 1:
                # 第二次也失败，用纯文本兜底
                return f"（纯文本摘要）\n{raw}"

            if attempt == 0:
                continue  # 第一次失败，重试

        return None

    def _parse_summary_json(self, raw: str) -> str | None:
        """从模型输出中提取并验证 JSON。支持多种格式，尽力容错。"""
        import re
        if not raw:
            return None
        # 尝试1: 直接解析
        try:
            json.loads(raw)
            return raw
        except (json.JSONDecodeError, ValueError):
            pass
        # 尝试2: 提取 markdown 代码块中的 JSON
        m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw, re.DOTALL)
        if m:
            try:
                json.loads(m.group(1))
                return m.group(1)
            except (json.JSONDecodeError, ValueError):
                pass
        # 尝试3: 找到第一个 { 和最后一个 }
        start = raw.find('{')
        end = raw.rfind('}')
        if start >= 0 and end > start:
            try:
                candidate = raw[start:end+1]
                json.loads(candidate)
                return candidate
            except (json.JSONDecodeError, ValueError):
                pass
        # 尝试4: 修复常见问题（尾部逗号、单引号、缺右括号）
        if start >= 0 and end > start:
            try:
                candidate = raw[start:end+1]
                candidate = re.sub(r',\s*}', '}', candidate)
                candidate = re.sub(r',\s*]', ']', candidate)
                # 补全缺失的右括号
                depth = 0
                for ch in candidate:
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                if depth > 0:
                    candidate += '}' * depth
                json.loads(candidate)
                return candidate
            except (json.JSONDecodeError, ValueError):
                pass
        # 尝试5: JSON 被截断——尝试补全（长对话输出接近 max_tokens 时常见）
        if start >= 0:
            candidate = raw[start:]
            candidate = re.sub(r',\s*$', '', candidate)
            # 统计未闭合的括号
            depth_brace = 0
            depth_bracket = 0
            in_string = False
            esc = False
            for ch in candidate:
                if esc:
                    esc = False
                    continue
                if ch == '\\':
                    esc = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == '{':
                    depth_brace += 1
                elif ch == '}':
                    depth_brace -= 1
                elif ch == '[':
                    depth_bracket += 1
                elif ch == ']':
                    depth_bracket -= 1
            candidate += ']' * max(depth_bracket, 0)
            candidate += '}' * max(depth_brace, 0)
            try:
                json.loads(candidate)
                return candidate
            except (json.JSONDecodeError, ValueError):
                pass
        return None

    # ---- TIER 规则滑动窗口 ----

    TIER_REMINDER = (
        "[核心约束 — 信息边界 + 规则加载]\n"
        "1. 绝不主动提及任何 NPC 的 secret 字段内容。\n"
        "2. 叙事仅基于 visible_tags + 已揭示线索 + NPC revealed_entries。\n"
        "3. 不确定某信息是否可透露 → 保守处理，用模糊描述代替。\n"
        "4. 战斗、疯狂与魔法 Skill 由引擎按玩家行动自动注入，不要为加载规则额外调用 read_file。"
    )

    def _handle_tool_handouts(self, name: str, args: dict, output: str) -> None:
        """Translate authoritative tool results into declarative handout events."""
        try:
            data = json.loads(output)
        except (TypeError, json.JSONDecodeError):
            data = {}

        if name == "state_add_clue" and data.get("ok"):
            clue = data.get("clue") or {}
            asset = clue.get("asset") or {}
            clue_id = str(clue.get("catalog_id") or clue.get("id") or "")
            if asset.get("id") and asset.get("file"):
                self._auto_handout(
                    "clue",
                    str(clue.get("id") or asset["id"]),
                    asset_id=asset["id"],
                )
            self._dispatch_handouts(
                "clue_discovered",
                entity_id=clue_id,
                text=str(clue.get("text") or args.get("text") or ""),
            )
            return

        if name == "npc_reveal" and data.get("ok"):
            if data.get("revealed_level") == 1:
                self._dispatch_handouts(
                    "npc_revealed",
                    entity_id=str(data.get("npc_id") or args.get("npc_id") or ""),
                    text=str((data.get("new_entry") or {}).get("text") or ""),
                )
            return

        if name == "state_set" and args.get("path") == "current_scene":
            try:
                scene = json.loads(args.get("value", "{}"))
            except (TypeError, json.JSONDecodeError):
                scene = {}
            if isinstance(scene, dict) and scene.get("id"):
                self._dispatch_handouts(
                    "scene_entered",
                    entity_id=str(scene["id"]),
                    text=json.dumps(scene, ensure_ascii=False),
                )
                for npc_id in scene.get("npcs_present", []):
                    self._dispatch_handouts(
                        "npc_revealed",
                        entity_id=str(npc_id),
                        text=json.dumps(scene, ensure_ascii=False),
                    )
            return

    def _dispatch_handouts(
        self,
        event: str,
        *,
        entity_id: str = "",
        text: str = "",
    ) -> None:
        state = self.context.world_store.load()
        for match in matching_handouts(
            state,
            event,
            entity_id=entity_id,
            text=text,
        ):
            self._auto_handout(
                match["entity_type"],
                match["entity_id"],
                asset_id=match["asset_id"],
            )

    def _dispatch_narrative_handouts(self, narrative: str) -> None:
        """Fallback for entities physically present in the current scene."""
        body = narrative_body(narrative)
        if not body:
            return
        try:
            state = self.context.world_store.load()
        except Exception:
            return

        current_scene = state.get("current_scene", {})
        present_npc_ids = {
            str(npc_id)
            for npc_id in current_scene.get("npcs_present", [])
        } if isinstance(current_scene, dict) else set()
        for npc in state.get("npcs", []):
            if (
                not isinstance(npc, dict)
                or str(npc.get("id") or "") not in present_npc_ids
            ):
                continue
            npc_id = str(npc.get("id") or "")
            name = str(npc.get("name") or "")
            aliases = {name}
            aliases.update(part for part in name.replace("・", "·").split("·") if len(part) >= 2)
            if npc_id and any(alias and alias in body for alias in aliases):
                self._dispatch_handouts("npc_revealed", entity_id=npc_id, text=body)

        if isinstance(current_scene, dict):
            scene_id = str(current_scene.get("id") or "")
            name = str(current_scene.get("name") or "")
            if scene_id and name and name in body:
                self._dispatch_handouts(
                    "scene_entered",
                    entity_id=scene_id,
                    text=body,
                )

    def _emit_unseen_discovered_handouts(self) -> None:
        """Recover handouts attached to known clues but missed by an older session."""
        state = self.context.world_store.load()
        seen = state.get("seen_handouts", {})
        seen_assets = state.get("seen_handout_assets", {})
        seen_clues = seen.get("clues", []) if isinstance(seen, dict) else []
        seen_clue_assets = (
            seen_assets.get("clues", []) if isinstance(seen_assets, dict) else []
        )
        clues_found = state.get("clues_found", {})
        if not isinstance(clues_found, dict):
            return
        for clues in clues_found.values():
            if not isinstance(clues, list):
                continue
            for clue in clues:
                if not isinstance(clue, dict):
                    continue
                asset = clue.get("asset") or {}
                asset_id = asset.get("id")
                if (
                    not asset_id
                    or not asset.get("file")
                    or asset_id in seen_clue_assets
                    or asset_id in seen_clues
                ):
                    continue
                self._auto_handout(
                    "clue",
                    str(clue.get("id") or asset_id),
                    asset_id=asset_id,
                )

    def _auto_handout(
        self,
        entity_type: str,
        entity_id: str,
        *,
        asset_id: str | None = None,
    ):
        """自动推送展示材料（独立于 LLM 调用，确保首次遇到必触发）。"""
        args = {"entity_type": entity_type, "entity_id": entity_id}
        if asset_id:
            args["asset_id"] = asset_id
        result = execute_function(
            "show_handout",
            args,
            context=self.context,
        )
        try:
            info = json.loads(result)
            if (
                info.get("found")
                and not info.get("already_seen")
                and info.get("asset_data_uri")
            ):
                self.cb.on_handout(info)
        except Exception:
            pass

    def _inject_tier_reminder(self):
        """在最新 user 消息前注入 TIER 规则提醒（防止上下文稀释导致泄密）"""
        # 找到最后一条 user 消息
        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i]["role"] == "user":
                content = self.messages[i]["content"]
                # 避免重复注入
                if "[核心约束" not in content:
                    self.messages[i]["content"] = self.TIER_REMINDER + "\n\n" + content
                break
        log_tier(self._round_count)
        self._tier_last_injected = self._round_count

    def _maybe_inject_tier(self):
        """滑动窗口检查：距离上次注入 ≥5 轮 且 上轮为高危场景时注入。
        前 3 轮不注入（规则新鲜），之后每 10 轮至少注入一次防稀释。"""
        if self._round_count <= 2:
            return  # 前几轮规则还新鲜，不注入
        rounds_since = self._round_count - self._tier_last_injected
        if rounds_since >= 5 and self._last_turn_high_risk:
            self._inject_tier_reminder()
        elif rounds_since >= 10:
            # 即使没有高危场景，每 10 轮也注入一次防止规则稀释
            self._inject_tier_reminder()

    # ---- 主回合 ----

    def handle_action(self, user_content: str | None = None):
        """执行一个完整回合"""
        if (
            getattr(self, "_active_turn_id", None) is None
            and hasattr(self, "turn_journal")
        ):
            self.begin_turn_record(
                kind="action" if user_content else "control",
                player_input=user_content,
            )
        try:
            self._resume_pending_combat_decision()
            if user_content:
                user_content = self._preflight_player_escalation(user_content)
                if user_content is None:
                    self.finish_turn_record(
                        status="cancelled",
                        error="玩家在行动发生前取消",
                    )
                    self.cb.on_done()
                    return
            self._turn_graph.invoke(
                {"engine": self, "user_content": user_content},
                config={"recursion_limit": 50},
            )
        except TurnCancelledError as exc:
            self.finish_turn_record(
                status="cancelled",
                error=str(exc),
            )
            return
        except Exception as exc:
            self.finish_turn_record(
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        finally:
            self._preconfirmed_escalation = None
