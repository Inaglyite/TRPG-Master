"""协议错误：code 一律取自 common.json 的 error_code 枚举。"""

from __future__ import annotations


class StructuredError(Exception):
    """可预期的协议/领域拒绝。code 决定前端映射；message 面向用户可读。"""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def to_payload(self, **extra) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            **extra,
        }
