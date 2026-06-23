"""LLM 客户端：流式/非流式调用 + GLM-4 Flash 快速摘要 + 沉浸式等待文本"""

import json
import random

from openai import OpenAI

from .config import GLM_API_KEY, GLM_BASE_URL, GLM_MODEL

# ---------------------------------------------------------------------------
# 沉浸式等待文本
# ---------------------------------------------------------------------------

TENSION_LINES = {
    "dice": [
        "命运之骰在黑暗中翻滚……",
        "这是个艰难的行动，不知道能不能成功……",
        "来，让我们看看命运站在哪一边……",
        "你在心中默默祈祷……",
        "成败在此一举——",
        "心跳加速，手心渗出细密的汗珠……",
        "空气仿佛凝固了……",
        "你深吸一口气，放手一搏……",
    ],
    "pro": [
        "这个判定比较复杂，需要仔细斟酌一下……",
        "让我想想，这个事情没有那么简单……",
        "局势微妙，容我仔细推敲……",
    ],
    "combat": [
        "肾上腺素在血管中奔涌……",
        "生死存亡，就在电光石火之间——",
        "战斗的本能接管了你的身体……",
    ],
    "sanity": [
        "一股莫名的寒意爬上你的脊背……",
        "你的理智正在经受考验……",
        "空气中似乎有什么东西在低语……",
    ],
}


def tension(category: str = "dice") -> str:
    """返回一条随机沉浸式等待文本"""
    lines = TENSION_LINES.get(category, TENSION_LINES["dice"])
    return random.choice(lines)


# ---------------------------------------------------------------------------
# 流式调用
# ---------------------------------------------------------------------------

def stream_llm(client: OpenAI, messages: list, model: str,
               tools: list | None = None) -> tuple[str, list]:
    """流式调用 LLM——文本实时输出，tool_calls 静默积累。"""
    kwargs = dict(model=model, messages=messages, temperature=0.8,
                  max_tokens=2048, stream=True)
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    try:
        stream = client.chat.completions.create(**kwargs)
    except Exception as e:
        print(f"\n[API 错误] {e}")
        return "", []

    full_text = ""
    tool_calls_acc: dict[int, dict] = {}

    for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta is None:
            continue

        if delta.content:
            full_text += delta.content
            print(delta.content, end="", flush=True)

        if delta.tool_calls:
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index
                if idx not in tool_calls_acc:
                    tool_calls_acc[idx] = {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""}
                    }
                acc = tool_calls_acc[idx]
                if tc_delta.id:
                    acc["id"] += tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        acc["function"]["name"] += tc_delta.function.name
                    if tc_delta.function.arguments:
                        acc["function"]["arguments"] += tc_delta.function.arguments

    tool_calls_list = [tool_calls_acc[i] for i in sorted(tool_calls_acc.keys())]
    return full_text, tool_calls_list


# ---------------------------------------------------------------------------
# 非流式调用
# ---------------------------------------------------------------------------

def call_llm(client: OpenAI, messages: list, model: str,
             tools: list | None = None) -> dict:
    """非流式调用 LLM（保留以备用）。"""
    kwargs = dict(model=model, messages=messages, temperature=0.8, max_tokens=2048)
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    try:
        return client.chat.completions.create(**kwargs)
    except Exception as e:
        print(f"\n[API 错误] {e}")
        return None


# ---------------------------------------------------------------------------
# GLM-4 Flash 快速摘要（检定后即时反馈）
# ---------------------------------------------------------------------------

_glm_client: OpenAI | None = None


def _get_glm() -> OpenAI | None:
    global _glm_client
    if _glm_client is not None:
        return _glm_client
    if GLM_API_KEY:
        _glm_client = OpenAI(api_key=GLM_API_KEY, base_url=GLM_BASE_URL)
        return _glm_client
    return None


def glm_quick_summary(tool_outputs: list[tuple[str, str]], model_context: str) -> str | None:
    """用 GLM-4 Flash 生成 1-2 句即时检定摘要。极快（<1s），免费。"""
    glm = _get_glm()
    if glm is None:
        return None

    dice_info = ""
    sanity_info = ""
    damage_info = ""
    for name, out in tool_outputs:
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            continue
        if "spec" in data:
            dice_info = f"d{data['sides']} = {data['total']}"
            if data.get("rolls"):
                dice_info += f"（掷出 {data['rolls']}）"
        if "loss_amount" in data:
            sanity_info = f"理智 -{data['loss_amount']}"
        if "damage" in data:
            damage_info = f"造成 {data['damage']} 点{data.get('damage_type', '')}伤害"
        if "heal_amount" in data:
            damage_info = f"恢复 {data['heal_amount']} 点生命"

    parts = [p for p in [dice_info, sanity_info, damage_info] if p]
    if not parts:
        return None

    result_summary = "，".join(parts)
    context_snippet = model_context[:300] if model_context else ""

    prompt = (
        f"检定：{result_summary}。\n"
        f"上下文：{context_snippet}\n\n"
        "用1-2句有画面感的中文概述这个检定结果。不要提问，不要给选项，不要剧透NPC秘密。"
    )

    try:
        resp = glm.chat.completions.create(
            model=GLM_MODEL,
            messages=[
                {"role": "system", "content": "你是TRPG游戏检定播报员。用简洁有画面感的中文叙述检定结果。1-2句。不提问，不给选项。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=80,
        )
        return resp.choices[0].message.content
    except Exception:
        return None
