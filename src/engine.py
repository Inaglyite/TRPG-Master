"""TRPG 游戏引擎 —— 纯内核，不依赖终端/WebSocket。
通过回调函数输出事件，可接入任意界面层。
"""

import json
import sys
from dataclasses import dataclass, field
from typing import Callable, Any

from openai import OpenAI

from .config import (
    PROJECT_ROOT, API_KEY, BASE_URL, MODEL_FLASH, MODEL_PRO, MAX_TOOL_ROUNDS, AUTO_SAVE_SLOT,
)
from .persistence import load_system_prompt, save_game, load_game, restore_snapshot, has_save, list_saves
from .tools import (
    TOOLS, COMPLEX_FUNCTIONS, tool_category,
    needs_pro_model, execute_function, dice_summary,
)
from .llm import stream_llm as raw_stream_llm, tension, glm_quick_summary


@dataclass
class EngineCallbacks:
    """引擎输出事件回调。每个回调在特定时机触发。"""
    on_narrative: Callable[[str], None] = lambda text: None       # 流式文本块
    on_tension: Callable[[str, str], None] = lambda text, cat: None  # 沉浸式提示
    on_dice: Callable[[str], None] = lambda summary: None        # 骰子结果
    on_glm_summary: Callable[[str], None] = lambda text: None    # 快速摘要
    on_suggest: Callable[[dict], bool] = lambda info: False      # 检定确认，返回 True/False
    on_done: Callable[[], None] = lambda: None                   # 回合结束
    on_game_over: Callable[[str, str, str], None] = lambda t, ti, s: None  # 游戏结束
    on_error: Callable[[str], None] = lambda msg: None           # 错误


class GameEngine:
    """TRPG 游戏引擎内核"""

    def __init__(self):
        self.client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
        self.messages: list[dict] = []
        self.current_model = MODEL_FLASH
        self.cb = EngineCallbacks()
        # 记忆管理
        self._round_count = 0
        self._tier_last_injected = -99  # 首次必定注入
        self._last_turn_high_risk = False
        self._summary_token_estimate = 0
        # 摘要阈值：消息超过此数或 token 估算超过窗口 60% 时触发压缩
        self.SUMMARY_MSG_THRESHOLD = 30
        self.SUMMARY_TOKEN_THRESHOLD = 8000

    def reset(self):
        """开始新游戏——重置对话 + 世界状态"""
        import shutil
        from . import config as cfg

        # 重置世界状态到初始
        initial = cfg.MODULE_DIR / "world_state_initial.json"
        if initial.exists():
            shutil.copy(str(initial), str(cfg.STATE_FILE))

        # 如果初始 PC 没有名字（模组未预设具体调查员），从 characters/ 加载预设调查员
        self._ensure_pc_from_characters()

        self.messages = [{"role": "system", "content": load_system_prompt()}]
        mod_path = f"mod/{cfg.MODULE_NAME}/world_state.json"
        self.messages.append({
            "role": "user",
            "content": (
                f"（游戏开始。请调用 read_file 读取以下文件来初始化："
                "rules/rule_schema.json、rules/rule_config.json、"
                f"{mod_path}。"
                "然后调用 get_private_memory 了解当前信息边界。"
                "再调用 state_clues 和 state_npcs 确认已知线索和 NPC 揭示状态。"
                "最后描述开场场景并提供选项。）"
            )
        })
        self.current_model = MODEL_FLASH
        self._round_count = 0
        self._tier_last_injected = -99
        self._last_turn_high_risk = False
        self._summary_token_estimate = 0

    def _ensure_pc_from_characters(self):
        """若 world_state.json 的 pc 没有名字，从模组 characters/ 加载第一个预设调查员。
        角色卡文件结构（derived.HP / skills / attributes）映射到 world_state 的 pc 结构。
        """
        from . import config as cfg
        try:
            state = json.loads(cfg.STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return
        pc = state.get("pc", {})
        if pc.get("name"):  # 已有名字，无需填充
            return
        chars_dir = cfg.MODULE_DIR / "characters"
        if not chars_dir.exists():
            return
        char_files = sorted(chars_dir.glob("*.json"))
        if not char_files:
            return
        try:
            char = json.loads(char_files[0].read_text(encoding="utf-8"))
        except Exception:
            return
        derived = char.get("derived", {})
        pc.update({
            "name": char.get("name", ""),
            "occupation": char.get("occupation", ""),
            "hp": derived.get("HP", pc.get("hp", 10)),
            "max_hp": derived.get("max_HP", pc.get("max_hp", 10)),
            "san": derived.get("SAN", pc.get("san", 50)),
            "max_san": derived.get("max_SAN", pc.get("max_san", 50)),
            "attributes": char.get("attributes", pc.get("attributes", {})),
            "skills": char.get("skills", pc.get("skills", {})),
            "inventory": char.get("inventory", pc.get("inventory", [])),
        })
        state["pc"] = pc
        cfg.STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def has_save(self) -> bool:
        return has_save()

    def save(self, slot_id: str | None = None) -> str:
        """保存游戏。返回槽位 ID。"""
        return save_game(self.messages, slot_id)

    def list_saves(self) -> list[dict]:
        return list_saves()

    def load(self, slot_id: str | None = None) -> int | None:
        """读取存档并恢复世界状态快照。返回消息数量或 None。"""
        messages, snapshot = load_game(slot_id)
        if messages is None:
            return None
        # 恢复世界状态快照（防止线索污染）
        if snapshot:
            restore_snapshot(snapshot)
        # 保留当前 system prompt，恢复对话历史
        system_msg = self.messages[0] if self.messages else {"role": "system", "content": ""}
        self.messages = [system_msg] + messages[1:]
        # 重置记忆管理状态
        self._round_count = 0
        self._tier_last_injected = -99
        self._last_turn_high_risk = False
        self._summary_token_estimate = 0
        return len(messages) - 1

    # ---- 流式 LLM ----

    def _stream_llm(self, model: str) -> tuple[str, list]:
        """流式调用，文本通过 on_narrative 回调输出"""
        kwargs = dict(model=model, messages=self.messages, temperature=0.8,
                      max_tokens=4096, stream=True, tools=TOOLS, tool_choice="auto")
        try:
            stream = self.client.chat.completions.create(**kwargs)
        except Exception as e:
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
                if delta.content:
                    full_text += delta.content
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
                        if tc.id: acc["id"] += tc.id
                        if tc.function:
                            if tc.function.name: acc["function"]["name"] += tc.function.name
                            if tc.function.arguments: acc["function"]["arguments"] += tc.function.arguments

        # 因 token 上限被截断时提示（叙述/选项可能不完整）
        if finish_reason == "length" and not tool_calls_acc:
            self.cb.on_error("（叙述过长被截断，请重试或继续）")

        tool_calls_list = [tool_calls_acc[i] for i in sorted(tool_calls_acc.keys())]
        return full_text, tool_calls_list

    # ---- 工具执行 ----

    def _execute_tool(self, name: str, args: dict) -> str:
        """执行工具。suggest_check 通过回调交互。"""
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
        return execute_function(name, args)

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
        """检查是否需要压缩上下文"""
        msg_count = len(self.messages)
        if msg_count >= self.SUMMARY_MSG_THRESHOLD:
            return True
        if self._estimate_tokens() >= self.SUMMARY_TOKEN_THRESHOLD:
            return True
        return False

    def _summarize_history(self):
        """使用 GLM-4 Flash 将旧消息压缩为 ENGRAM 风格结构化摘要。
        保留 system prompt + 最近 10 条消息，中间部分替换为摘要。
        """
        from .llm import _get_glm

        glm = _get_glm()
        if glm is None:
            self.cb.on_error("（摘要模型不可用，跳过上下文压缩）")
            return

        # 保留：system prompt（索引0）+ 最近 N 条消息
        KEEP_RECENT = 10
        if len(self.messages) <= KEEP_RECENT + 5:
            return  # 太少，不压缩

        # 找到安全的切割点 —— 必须在完整的 user→assistant 周期边界
        cutoff = len(self.messages) - KEEP_RECENT
        # 向前调整到最近的 user 消息（确保不切断 tool 调用链）
        while cutoff > 1 and self.messages[cutoff].get("role") != "user":
            cutoff -= 1
        if cutoff <= 1:
            return  # 无法安全切割

        old_messages = self.messages[1:cutoff]  # 保留 system prompt
        recent_messages = self.messages[cutoff:]

        # 构建摘要请求
        old_text_parts = []
        for m in old_messages:
            role = m.get("role", "?")
            content = m.get("content", "") or ""
            if role == "tool":
                # 截断工具输出
                content = content[:300] + "..." if len(content) > 300 else content
            if content.strip():
                old_text_parts.append(f"[{role}]: {content}")

        old_text = "\n\n".join(old_text_parts)
        if len(old_text) > 8000:
            old_text = old_text[:8000] + "\n...(后续内容已截断)"

        summary_prompt = (
            "你是TRPG游戏记录员。请将以下游戏对话历史压缩为结构化JSON摘要。\n\n"
            "输出格式（严格遵守）：\n"
            '{"episodic":[{"turn":1,"event":"简述"}],"semantic":{"pc_knowledge":{},'
            '"revealed_clues":[],"world_state_changes":{}},"current_objective":"","last_scene":""}\n\n'
            "规则：\n"
            "1. episodic: 按时间顺序列出关键剧情事件，每条包含回合号和简述\n"
            "2. semantic.pc_knowledge: 以NPC名为key，记录PC已了解的信息（仅已揭示的，不推测秘密）\n"
            "3. semantic.revealed_clues: 已发现的关键线索列表\n"
            "4. semantic.world_state_changes: 场景/标志等状态变化\n"
            "5. current_objective: 当前主要目标（一句话）\n"
            "6. last_scene: 最后所在场景名称\n"
            "7. 只输出JSON，不要任何额外文本\n"
            "8. 技能检定和骰子结果必须保留\n"
            "9. 不要编造任何对话中不存在的信息\n\n"
            f"对话历史：\n{old_text}"
        )

        try:
            resp = glm.chat.completions.create(
                model="glm-4-flash-250414",
                messages=[
                    {"role": "system", "content": "你是TRPG游戏记录员。只输出JSON，不输出任何其他内容。"},
                    {"role": "user", "content": summary_prompt}
                ],
                temperature=0.3,
                max_tokens=1500,
            )
            summary_text = resp.choices[0].message.content.strip()
            # 清理可能的 markdown 代码块包装
            if summary_text.startswith("```"):
                summary_text = summary_text.split("```")[1]
                if summary_text.startswith("json"):
                    summary_text = summary_text[4:]
            summary_text = summary_text.strip()

            # 验证是有效 JSON
            summary_data = json.loads(summary_text)
            summary_str = json.dumps(summary_data, ensure_ascii=False, indent=2)
        except Exception as e:
            self.cb.on_error(f"（摘要生成失败: {e}）")
            return

        # 重建消息列表：system prompt + 摘要 + 最近消息
        system_msg = self.messages[0]
        summary_msg = {
            "role": "user",
            "content": (
                "（会话摘要——以下是此前冒险的关键记录。"
                "你仍然知道所有发生过的事，但信息已在下方压缩。"
                "技能检定结果、已发现线索、NPC互动记录均已保留。\n\n"
                f"{summary_str}\n\n"
                "——摘要结束。以下是最近的对话——）"
            )
        }
        self.messages = [system_msg, summary_msg] + recent_messages
        self._summary_token_estimate = self._estimate_tokens()
        self.cb.on_glm_summary("📋 上下文已压缩，保留关键信息。")

    # ---- TIER 规则滑动窗口 ----

    TIER_REMINDER = (
        "[核心约束 — 信息边界]\n"
        "1. 绝不主动提及任何 NPC 的 secret 字段内容。\n"
        "2. 叙事仅基于 visible_tags + 已揭示线索 + NPC revealed_entries。\n"
        "3. 不确定某信息是否可透露 → 保守处理，用模糊描述代替。\n"
        "4. 若需参考 NPC 幕后设定，先调用 get_npc_secret() 确认，再基于已揭示 tier 给出暗示。"
    )

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
        if user_content:
            # TIER 滑动窗口注入（在追加用户消息前检查）
            self._maybe_inject_tier()
            self.messages.append({"role": "user", "content": user_content})

        # 上下文压缩检查（在 LLM 调用前）
        if self._should_summarize():
            self._summarize_history()

        tool_round = 0
        narrative = ""
        self.current_model = MODEL_FLASH
        turn_had_check = False  # 本回合是否涉及检定/战斗/理智

        while tool_round <= MAX_TOOL_ROUNDS:
            text, tool_calls = self._stream_llm(self.current_model)

            if not text and not tool_calls:
                break

            if not tool_calls:
                narrative = text
                break

            # Pro 切换
            if self.current_model == MODEL_FLASH and needs_pro_model(tool_calls):
                self.current_model = MODEL_PRO
                cat = "dice"
                for tc in tool_calls:
                    n = tc["function"]["name"]
                    if n.startswith("sanity"): cat = "sanity"; break
                    elif n in ("apply_damage", "apply_heal"): cat = "combat"; break
                self.cb.on_tension(tension(cat), cat)
                if self.messages and self.messages[-1]["role"] == "assistant":
                    self.messages.pop()
                continue

            # 沉浸式提示
            complex_hit = any(tc["function"]["name"] in COMPLEX_FUNCTIONS for tc in tool_calls)
            if complex_hit:
                turn_had_check = True  # 标记高危回合
            if complex_hit and tool_round == 0:
                cat = "dice"
                for tc in tool_calls:
                    n = tc["function"]["name"]
                    if n.startswith("sanity"): cat = "sanity"; break
                    elif n in ("apply_damage", "apply_heal"): cat = "combat"; break
                self.cb.on_tension(tension(cat), cat)

            if text:
                narrative += text + "\n\n"

            assistant_msg: dict = {"role": "assistant", "content": text}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            self.messages.append(assistant_msg)

            # 执行工具
            tool_outputs = []
            for tc in tool_calls:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    args = {}
                output = self._execute_tool(name, args)
                self.messages.append({
                    "role": "tool", "tool_call_id": tc["id"], "content": output
                })

                if name in ("skill_check", "dice_roll", "dice_roll_advantage", "dice_roll_disadvantage"):
                    summary = dice_summary(output)
                    if summary:
                        self.cb.on_dice(summary)

                if name in COMPLEX_FUNCTIONS:
                    tool_outputs.append((name, output))

                # 检测游戏结束：模型提议，不直接结束——让玩家决定
                if name == "end_game":
                    try:
                        end_data = json.loads(output)
                        self.cb.on_game_over(
                            end_data.get("ending_type", "neutral"),
                            end_data.get("title", "故事结束"),
                            end_data.get("summary", "")
                        )
                        # 不 return，继续正常叙事循环。模型应该描述结局场景并提供
                        # 「确认离开」和「继续探索」两个选项给玩家选择。
                    except json.JSONDecodeError:
                        pass

            # GLM 快速摘要
            if tool_outputs:
                quick = glm_quick_summary(tool_outputs, text or narrative)
                if quick:
                    self.cb.on_glm_summary(quick)

            tool_round += 1

        if narrative.strip():
            self.messages.append({"role": "assistant", "content": narrative.strip()})
        else:
            self.cb.on_error("守秘人陷入了沉思……")

        # 每回合结束后自动存档到 slot_000
        self.save("slot_000")

        # 更新记忆管理状态
        self._last_turn_high_risk = turn_had_check
        self._round_count += 1

        self.cb.on_done()
