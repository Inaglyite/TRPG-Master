"""TRPG 游戏引擎 —— 纯内核，不依赖终端/WebSocket。
通过回调函数输出事件，可接入任意界面层。
"""

import json
import time
from dataclasses import dataclass
from typing import Callable

from openai import OpenAI

from .config import (
    API_KEY, BASE_URL, MODEL_FLASH, MODEL_PRO, FORCE_PRO,
    OPTIONAL_SKILL_HINTS,
)
from .persistence import load_system_prompt, save_game, load_game, restore_snapshot, has_save, list_saves
from .characters import apply_character_to_state, default_character_ref, settle_case as settle_character_case
from .combat import preview_player_escalation
from .combat_agent import build_combat_overlay
from .tools import (
    TOOLS, execute_function,
)
from .runtime import RuntimeContext
from .agent_graph import build_turn_graph
from .logger import error as log_error, summary_event as log_summary, \
    tier_inject as log_tier, game_event as log_game, model_call as log_model_call


@dataclass
class EngineCallbacks:
    """引擎输出事件回调。每个回调在特定时机触发。"""
    on_narrative: Callable[[str], None] = lambda text: None       # 流式文本块
    on_tension: Callable[[str, str], None] = lambda text, cat: None  # 沉浸式提示
    on_dice: Callable[[str, dict | None], None] = lambda summary, roll_data=None: None  # 骰子结果
    on_glm_summary: Callable[[str], None] = lambda text: None    # 快速摘要
    on_suggest: Callable[[dict], bool] = lambda info: False      # 检定确认，返回 True/False
    on_decision: Callable[[dict], str | None] = lambda info: info.get("default_option")  # 多选决定
    on_done: Callable[[], None] = lambda: None                   # 回合结束
    on_game_over: Callable[[str, str, str], None] = lambda t, ti, s: None  # 游戏结束
    on_handout: Callable[[dict], None] = lambda info: None       # 展示材料
    on_error: Callable[[str], None] = lambda msg: None           # 错误


class GameEngine:
    """TRPG 游戏引擎内核"""

    CONTROL_MESSAGE_PREFIX = "[引擎控制指令｜非玩家发言]"

    def __init__(self, context: RuntimeContext | None = None):
        self.context = context or RuntimeContext.local()
        self.client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
        self.messages: list[dict] = []
        self.current_model = MODEL_FLASH
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
        # 摘要策略：按玩家回合静默压缩，避免内部工具消息过多导致频繁打断沉浸。
        self.SUMMARY_PLAYER_TURN_INTERVAL = 50
        self.SUMMARY_KEEP_RECENT_MESSAGES = 24
        self._turn_graph = build_turn_graph()

    def prepare_session(self):
        """准备一条界面连接使用的空会话，不重置 world_state。"""
        self.messages = [{"role": "system", "content": load_system_prompt(self.context)}]
        self.current_model = MODEL_PRO if FORCE_PRO else MODEL_FLASH
        self._round_count = 0
        self._player_turn_count = 0
        self._last_summary_player_turn = 0
        self._tier_last_injected = -99
        self._last_turn_high_risk = False
        self._summary_token_estimate = 0
        self._loaded_optional_skills = set()
        self._preconfirmed_escalation = None

    def switch_context(self, context: RuntimeContext) -> None:
        """切换到另一个世界实例并重建该世界对应的 system prompt。"""
        self.context = context
        self.prepare_session()

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

    def reset(self, character_ref: dict | None = None):
        """开始新游戏——重置对话 + 世界状态"""
        log_game(f"新游戏 | world={self.context.world_id} | 模组={self.context.module_name}")
        self.context.reset_world()

        self._apply_starting_character(character_ref)

        self.prepare_session()
        self.append_control_instruction(
            "开始新游戏。请调用 read_file 读取以下文件来初始化："
            "rules/rule_schema.json、rules/rule_config.json、"
            "world://state。"
            "然后调用 get_private_memory 了解当前信息边界。"
            "再调用 state_clues 和 state_npcs 确认已知线索和 NPC 揭示状态。"
            "玩家调查员姓名、职业、背景必须以该 world_state.json 的 pc 字段为唯一来源；"
            "不要使用 module.md、示例文本或旧存档里的默认调查员姓名来称呼玩家。"
            "工具调用全部完成后，第一段可见文本直接描述开场场景并提供选项。"
        )

    def _apply_starting_character(self, character_ref: dict | None):
        """将选择的调查员复制进当前模组 world_state.pc。"""
        selected_ref = character_ref or default_character_ref(
            self.context.module_name, context=self.context
        )
        if not selected_ref:
            return

        def apply(state: dict) -> None:
            apply_character_to_state(
                selected_ref,
                state,
                self.context.module_name,
                context=self.context,
            )

        self.context.world_store.update(apply)

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
        buffer_if_tools: bool = False,
    ) -> tuple[str, list]:
        """流式调用；控制回合可缓冲并丢弃工具调用前的元确认语。"""
        started_at = time.monotonic()
        first_token_at: float | None = None
        messages = self.messages
        if system_overlay and messages:
            messages = [dict(message) for message in messages]
            if messages[0].get("role") == "system":
                messages[0]["content"] = f"{messages[0].get('content', '')}\n\n---\n\n{system_overlay}"
            else:
                messages.insert(0, {"role": "system", "content": system_overlay})
        kwargs = dict(model=model, messages=messages, temperature=0.8,
                      max_tokens=4096, stream=True, tools=TOOLS, tool_choice="auto")
        try:
            stream = self.client.chat.completions.create(**kwargs)
        except Exception as e:
            log_error(f"API: {e}")
            self.cb.on_error(f"API 错误: {e}")
            return "", []

        full_text = ""
        tool_calls_acc: dict[int, dict] = {}
        finish_reason = None

        for chunk in stream:
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
                        self.cb.on_narrative(delta.content)
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
        log_model_call(
            model,
            "combat" if system_overlay else "story",
            time.monotonic() - started_at,
            first_token_at - started_at if first_token_at is not None else None,
            finish_reason,
            len(tool_calls_list),
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
            self.cb.on_done()
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

    def _execute_tool(self, name: str, args: dict) -> str:
        """执行工具。确认类工具通过回调与玩家交互。"""
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
                if info.get("found") and info.get("asset_data_uri"):
                    self.cb.on_handout(info)
            except Exception:
                pass
            return result

        if name == "state_add_clue":
            result = execute_function(name, args, context=self.context)
            try:
                info = json.loads(result)
                clue = info.get("clue") or {}
                asset = clue.get("asset") or {}
                if info.get("ok") and asset.get("id") and asset.get("file"):
                    self._auto_handout("clue", asset["id"])
            except Exception:
                pass
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

        return execute_function(name, args, context=self.context)

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
        summary = self._try_model_summary(self.client, MODEL_PRO, old_text)
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
        "4. 涉及战斗/疯狂叙事/魔法时，务必先 read_file 加载对应 skill（参考 trpg_master.skill 路由表）。"
    )

    def _auto_handout(self, entity_type: str, entity_id: str):
        """自动推送展示材料（独立于 LLM 调用，确保首次遇到必触发）。"""
        result = execute_function(
            "show_handout",
            {"entity_type": entity_type, "entity_id": entity_id},
            context=self.context,
        )
        try:
            info = json.loads(result)
            if info.get("found") and info.get("asset_data_uri"):
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
        self._resume_pending_combat_decision()
        if user_content:
            user_content = self._preflight_player_escalation(user_content)
            if user_content is None:
                return
        try:
            self._turn_graph.invoke(
                {"engine": self, "user_content": user_content},
                config={"recursion_limit": 50},
            )
        finally:
            self._preconfirmed_escalation = None
