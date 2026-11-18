"""Persistent DeepSeek thinking preference, independent of reasoning display."""

from src.ai.model.provider_profiles import provider_for_url
from src.app import config


def parse_thinking_mode(value):
    if not isinstance(value, str) or value not in {"auto", "enabled", "disabled"}:
        raise ValueError("思考模式必须是 auto、enabled 或 disabled")
    return value


def parse_reasoning_effort(value):
    if not isinstance(value, str) or value not in {"auto", "low", "high", "max"}:
        raise ValueError("思考强度必须是 auto、low、high 或 max")
    return value


def supports_thinking(role=None):
    kind = getattr(role, "provider_kind", "server_default")
    base = getattr(role, "base_url", "") or (config.BASE_URL if kind == "server_default" else "")
    host = getattr(role, "base_url_host", "")
    return provider_for_url(f"https://{host}" if host else base, kind) == "deepseek"


def thinking_for_host(host, request_role, fallback):
    routes = getattr(host, "_model_routes", None)
    if routes is None:
        return fallback
    role = routes.judgement if request_role == "combat" else routes.narrative
    if not supports_thinking(role):
        return None
    mode = getattr(routes, "thinking_mode", "auto")
    return mode if mode != "auto" else fallback


def thinking_view(settings):
    supported = supports_thinking(settings.narrative.service)
    mode = settings.thinking_mode
    effective = mode if mode != "auto" else config.STORY_THINKING_MODE
    model = getattr(settings.narrative.service, "model_id", config.NARRATIVE_MODEL)
    enabled = effective == "enabled" or (
        effective in {"auto", "provider"}
        and (effective == "provider" or model != config.MODEL_FLASH)
    )
    return {
        "reasoning_effort": "high" if settings.reasoning_effort == "auto" else settings.reasoning_effort,
        "thinking_mode": mode,
        "thinking_supported": supported,
        "thinking_enabled": supported and enabled,
    }
