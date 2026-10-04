"""Safe user-facing messages for synchronous WebSocket operation failures."""

from src.ai.skills.skill_manifest import CatalogError
from src.ai.skills.skill_pins import PinUnavailable


def operation_error_message(operation: object, exc: Exception) -> str:
    if operation == "switch_module" and isinstance(exc, (CatalogError, PinUnavailable)):
        return f"模组切换失败，原会话已保留：{exc}"
    return "操作失败，房间连接已保留，请稍后重试。"
