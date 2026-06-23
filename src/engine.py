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

    def reset(self):
        """开始新游戏"""
        self.messages = [{"role": "system", "content": load_system_prompt()}]
        self.messages.append({
            "role": "user",
            "content": (
                "（游戏开始。请调用 read_file 读取以下文件来初始化："
                "rules/rule_schema.json、rules/rule_config.json、"
                "mod/mansion_of_madness/world_state.json。"
                "然后调用 state_clues 确认已知线索。"
                "最后描述开场场景并提供选项。）"
            )
        })
        self.current_model = MODEL_FLASH

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

    # ---- 主回合 ----

    def handle_action(self, user_content: str | None = None):
        """执行一个完整回合"""
        if user_content:
            self.messages.append({"role": "user", "content": user_content})

        tool_round = 0
        narrative = ""
        self.current_model = MODEL_FLASH

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

                # 检测游戏结束
                if name == "end_game":
                    try:
                        end_data = json.loads(output)
                        self.cb.on_game_over(
                            end_data.get("ending_type", "neutral"),
                            end_data.get("title", "故事结束"),
                            end_data.get("summary", "")
                        )
                    except json.JSONDecodeError:
                        pass
                    return  # 不再继续叙事

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

        self.cb.on_done()
