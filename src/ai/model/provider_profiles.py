"""Provider presets and wire parameter policies, independent of game state.

Presets are editable starting points, never a claim about a model's context size.
Protocol conversion lives in provider_transport / anthropic_protocol.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

PROVIDER_PRESETS = (
    {"id": "deepseek", "label": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model_id": "deepseek-flash", "hint": "使用 DeepSeek 开放平台 API Key。"},
    {"id": "moonshot", "label": "Kimi / Moonshot", "base_url": "https://api.moonshot.cn/v1", "model_id": "kimi-k3", "hint": "使用开放平台 Key；Kimi Code 套餐的 Key 和地址不能混用。"},
    {"id": "qwen", "label": "通义千问 / 百炼", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model_id": "qwen-plus", "hint": "按 Key 所属地域修改地址，也可填写业务空间专属域名。"},
    {"id": "zhipu", "label": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model_id": "glm-5.3", "hint": "通用按量 API 地址；Coding 套餐须改用套餐专属地址。"},
    {"id": "doubao", "label": "豆包 / 火山方舟", "base_url": "https://ark.cn-beijing.volces.com/api/v3", "model_id": "", "hint": "模型 ID 填控制台已开通的模型名或 ep- 开头的推理接入点。"},
    {"id": "gemini", "label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model_id": "gemini-3.8-flash", "hint": "使用 AI Studio 的 Gemini API Key，通过官方兼容接口接入。"},
    {"id": "openai", "label": "OpenAI", "base_url": "https://api.openai.com/v1", "model_id": "gpt-5", "hint": "使用 API 平台 Key；模型须支持 Chat Completions。"},
    {"id": "openrouter", "label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "model_id": "", "hint": "模型 ID 填厂商/模型名，按所选模型和上游支持情况提供工具调用。"},
    {"id": "siliconflow", "label": "硅基流动", "base_url": "https://api.siliconflow.cn/v1", "model_id": "deepseek-ai/DeepSeek-V4-Flash", "hint": "填写控制台中的完整模型 ID；国际站需修改地址。"},
    {"id": "anthropic", "label": "Anthropic Messages", "base_url": "https://api.anthropic.com", "model_id": "", "hint": "原生 Messages 协议。填写可用 Claude 模型 ID；也支持自定义兼容地址。"},
    {"id": "openai_compatible", "label": "其他 OpenAI 兼容服务", "base_url": "", "model_id": "", "hint": "填写服务提供的 Base URL 与模型 ID；本地模式支持 Ollama 等本机接口。"},
)
PROVIDER_KINDS = frozenset(item["id"] for item in PROVIDER_PRESETS)


def provider_for_url(base_url: str, configured: str = "openai_compatible") -> str:
    """Known destinations outrank legacy provider hints; custom URLs keep their choice."""
    host = (urlsplit(str(base_url)).hostname or "").lower()
    suffixes = {
        "deepseek.com": "deepseek", "moonshot.cn": "moonshot", "moonshot.ai": "moonshot",
        "aliyuncs.com": "qwen", "bigmodel.cn": "zhipu", "z.ai": "zhipu",
        "volces.com": "doubao", "generativelanguage.googleapis.com": "gemini",
        "api.openai.com": "openai", "openrouter.ai": "openrouter",
        "siliconflow.cn": "siliconflow", "siliconflow.com": "siliconflow",
        "api.anthropic.com": "anthropic",
    }
    for suffix, kind in suffixes.items():
        if host == suffix or host.endswith("." + suffix):
            return kind
    return configured


def model_family(model: str) -> str:
    name = str(model).rsplit("/", 1)[-1].lower()
    if re.match(r"kimi-k(?:3|2\.[5-9])(?:$|[-.:])", name):
        return "kimi3" if name.startswith("kimi-k3") else "kimi_thinking"
    if "chat-latest" in name:
        return "other"
    if re.match(r"(?:o[134](?:$|-)|gpt-[5-9](?:$|[-.]))", name):
        return "openai_reasoning"
    if name.startswith("gemini-"):
        return "gemini"
    if name.startswith("glm-"):
        return "glm"
    if name.startswith("qwen3"):
        return "qwen3"
    return "other"


def normalize_chat_body(body: dict, provider: str) -> dict:
    """Adapt known constraints only. Unknown models retain ordinary chat parameters."""
    result = dict(body)
    family = model_family(str(body.get("model", "")))
    thinking = result.pop("thinking", None)
    if family == "kimi3" and isinstance(result.get("tool_choice"), dict):
        # K3 思考模式接受 required，但拒绝强制指定函数。保留“必须调用”
        # 语义，具体函数及参数仍由业务端的工具白名单与结构校验把关。
        if result["tool_choice"].get("type") == "function":
            result["tool_choice"] = "required"
    if family in {"kimi3", "kimi_thinking", "openai_reasoning", "glm", "gemini"}:
        for key in ("temperature", "top_p", "presence_penalty", "frequency_penalty", "n"):
            result.pop(key, None)
    if family in {"kimi3", "openai_reasoning"} and provider not in {"openrouter", "siliconflow", "qwen"}:
        if "max_tokens" in result:
            result.setdefault("max_completion_tokens", result.pop("max_tokens"))
        result.setdefault("reasoning_effort", "low")
    if provider == "openrouter" and family in {"kimi3", "openai_reasoning", "gemini"}:
        result.setdefault("reasoning", {"effort": "low"})
    elif provider == "deepseek":
        if thinking is not None:
            result["thinking"] = thinking
    elif provider == "moonshot" and family == "kimi_thinking":
        if "kimi-k2.7-code" not in str(body.get("model", "")).lower():
            result["thinking"] = thinking or {"type": "disabled"}
    elif provider in {"qwen", "siliconflow"} and family == "qwen3":
        # Explicit *-thinking models cannot turn reasoning off.
        if "thinking" not in str(body.get("model", "")).lower():
            result["enable_thinking"] = bool(thinking and thinking.get("type") == "enabled")
    elif provider == "zhipu" and family == "glm":
        if re.match(r"glm-(?:[5-9]|4\.[5-9])", str(body.get("model", "")).lower()):
            result["thinking"] = thinking or {"type": "disabled"}
    elif provider == "doubao" and thinking is not None:
        result["thinking"] = thinking
    elif provider == "gemini":
        result.setdefault("reasoning_effort", "low")
    return result
