"""LLM 辅助功能：GLM-4 Flash 快速摘要 + 沉浸式等待文本"""

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
    for _name, out in tool_outputs:
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
            damage = data["damage"]
            if isinstance(damage, dict) and damage.get("amount") is not None:
                damage_info = f"造成 {damage['amount']} 点伤害"
            elif isinstance(damage, (int, float)):
                damage_info = f"造成 {damage} 点{data.get('damage_type', '')}伤害"
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
