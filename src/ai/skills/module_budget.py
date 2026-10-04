"""Shared bounded budgets for imported module Skill text, without truncation."""

from src.ai.context.lorebook import estimate_text_tokens

LOCAL_AUTHOR_SKILL_MAX_CONTEXT_TOKENS = 32_000
LOCAL_AUTHOR_SKILL_DEFAULT_CONTEXT_TOKENS = 4_000


def module_skill_budget(content: str) -> int:
    estimate = estimate_text_tokens(content)
    if estimate > LOCAL_AUTHOR_SKILL_MAX_CONTEXT_TOKENS:
        raise ValueError(
            f"Skill 正文估算 {estimate} tokens，超过单篇上限 "
            f"{LOCAL_AUTHOR_SKILL_MAX_CONTEXT_TOKENS}；未截断或修改正文"
        )
    return max(LOCAL_AUTHOR_SKILL_DEFAULT_CONTEXT_TOKENS, estimate)
