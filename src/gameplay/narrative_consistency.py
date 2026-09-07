"""叙事与实际结算的一致性闸门（A02）。

故事模型先流式成文、引擎事后才能核对。本模块对定稿叙事做确定性检查，
发现越界（如把 4 小时演成数天）时发起一次有界重写；历史、回合记录与前端
权威段统一采用修正后的文本，避免错误叙事毒化后续上下文。检查与重写失败
一律 fail-open（保留原文并记录诊断），不得中断游戏。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from src.ai.model.llm_concurrency import llm_call_slot
from src.app.config import JUDGEMENT_MODEL, _enabled_env
from src.app.logger import game_event as log_game
from src.app.logger import model_call as log_model_call

# 以“天/夜”计的跨度词，覆盖中文数字（三天/两日/半个月等）。“第二天再说”这类
# 将来打算也会被命中，交给重写模型按提示甄别（打算/回忆/假设可保留），
# 误伤成本是一次多余但无害的校对调用。
_DAY_SPAN = re.compile(
    r"[一二两三四五六七八九十数几][天日](?![气氧])|数日|数天|多天|连日|昼夜|翌日|通宵|"
    r"整晚|一整夜|夜宿|到天亮|天亮才|守了一夜|过了[一二两三四五六七八九十数几半]?[天日夜]|"
    r"日落又日出|第二天[，。；]|次日[，。；]|半个月"
)
_HOUR_SPAN = re.compile(
    r"[一二两三四五六七八九十数几半]\s*个?\s*小时|数小时|半天|大半天|"
    r"一整个(?:上午|下午|晚上)|(?:上午|下午|晚上)一直到(?:上午|下午|晚上|夜里)"
)

_REWRITE_PROMPT = """你是叙事一致性校对。给出的守秘人叙事与本回合实际结算存在冲突。
只做消除冲突的最小修改：
- 不改变已结算的事件、骰点、物品、时钟与人物态度；不新增线索、物品、秘密或行动结果；
- 保留原文的【npc:id】…【/npc】台词标签、段落顺序与整体风格；
- 若被点名的表述是人物的打算、回忆或假设（并非本回合真实经过的时间），可保留原样；
- 直接输出修正后的叙事全文，不要解释，不要添加标题。"""


def consistency_violations(narrative: str, *, settled_minutes: int | None) -> list[str]:
    """Deterministic span checks against the time actually settled this turn."""
    violations = []
    if settled_minutes is None:
        return violations
    if settled_minutes < 1440 and _DAY_SPAN.search(narrative):
        violations.append(
            f"叙事出现以天/夜计的时间跨度，但本回合实际只结算了 {settled_minutes} 分钟"
        )
    if settled_minutes < 90 and _HOUR_SPAN.search(narrative):
        violations.append(
            f"叙事出现数小时级的时间跨度，但本回合实际只结算了 {settled_minutes} 分钟"
        )
    return violations


def _settled_minutes(outcome: dict) -> int | None:
    for event in outcome.get("events", []):
        if isinstance(event, dict) and event.get("type") == "time_advanced":
            try:
                return max(0, int(event.get("after", 0)) - int(event.get("before", 0)))
            except (TypeError, ValueError):
                return None
    return None


def apply_narrative_consistency(engine: Any, narrative: str) -> str:
    """Return the narrative, rewritten at most once when it outruns the settlement."""
    if not narrative.strip() or not _enabled_env("TRPG_NARRATIVE_CONSISTENCY", True):
        return narrative
    if not getattr(engine, "client", None):
        return narrative
    outcome = getattr(engine, "_adjudicated_outcome", None) or {}
    settled = _settled_minutes(outcome)
    violations = consistency_violations(narrative, settled_minutes=settled)
    if not violations:
        return narrative
    log_game("叙事一致性越界 | " + "；".join(violations)[:200])
    payload = {
        "settled_minutes": settled,
        "outcome_status": outcome.get("status", ""),
        "violations": violations,
        "narrative": narrative,
    }
    messages = [
        {"role": "system", "content": _REWRITE_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    model = getattr(engine, "narrative_model", None) or JUDGEMENT_MODEL
    started = time.monotonic()
    try:
        with llm_call_slot(model=model, world_id=str(getattr(engine.context, "world_id", ""))):
            response = engine.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,
                max_tokens=6000,
                extra_body={"thinking": {"type": "disabled"}},
            )
        engine.raise_if_turn_cancelled()
        if response.choices[0].finish_reason != "stop":
            raise ValueError("一致性重写未完整结束")
        rewritten = str(response.choices[0].message.content or "").strip()
    except Exception as exc:
        engine.raise_if_turn_cancelled()
        log_game(f"叙事一致性重写失败 | {type(exc).__name__}: {exc}"[:200])
        return narrative
    elapsed = time.monotonic() - started
    log_model_call(model, "narrative_consistency", elapsed, None, "chars", len(rewritten))
    if hasattr(engine, "_turn_diagnostics"):
        engine._turn_diagnostics.append(
            {
                "model": model,
                "role": "narrative_consistency",
                "status": "completed",
                "elapsed_ms": round(elapsed * 1000),
                "violations": len(violations),
            }
        )
    if not rewritten or len(rewritten) < len(narrative) * 0.3:
        log_game("叙事一致性重写结果过短，保留原文")
        return narrative
    remaining = consistency_violations(rewritten, settled_minutes=settled)
    if remaining:
        log_game("叙事一致性重写后仍有争议 | " + "；".join(remaining)[:200])
    return rewritten
