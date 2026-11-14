"""Persistent DeepSeek thinking preference, independent of reasoning display."""

from urllib.parse import urlsplit

from src.app import config


def parse_thinking_mode(value):
    if not isinstance(value, str) or value not in {"auto", "enabled", "disabled"}:
        raise ValueError("思考模式必须是 auto、enabled 或 disabled")
    return value


def supports_thinking(role=None):
    kind = getattr(role, "provider_kind", "server_default")
    host = (
        getattr(role, "base_url_host", "")
        or urlsplit(
            getattr(role, "base_url", "") or (config.BASE_URL if kind == "server_default" else "")
        ).hostname
        or ""
    )
    return (
        kind == "deepseek"
        or host.lower() == "deepseek.com"
        or host.lower().endswith(".deepseek.com")
    )


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
        "thinking_mode": mode,
        "thinking_supported": supported,
        "thinking_enabled": supported and enabled,
    }
