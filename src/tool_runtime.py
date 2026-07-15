"""Registry-based execution core for model-callable tools."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .runtime import RuntimeContext


ToolHandler = Callable[[dict[str, Any], RuntimeContext], str]


class DuplicateToolError(ValueError):
    """Raised when multiple handlers claim the same tool name."""


class UnknownToolError(LookupError):
    """Raised when a model or old history references an unsupported tool."""


@dataclass(frozen=True)
class ToolExecutionRecord:
    name: str
    world_id: str
    module_name: str
    started_ns: int
    duration_ns: int
    ok: bool
    error_type: str | None = None


@dataclass
class ToolRuntime:
    """Owns tool registration, execution and bounded in-process audit records."""

    audit_limit: int = 512
    _handlers: dict[str, ToolHandler] = field(default_factory=dict, init=False)
    _audit: list[ToolExecutionRecord] = field(default_factory=list, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def add(self, name: str, handler: ToolHandler) -> None:
        normalized = name.strip()
        if not normalized:
            raise ValueError("tool name must not be empty")
        if normalized in self._handlers:
            raise DuplicateToolError(normalized)
        self._handlers[normalized] = handler

    def handler(self, name: str) -> Callable[[ToolHandler], ToolHandler]:
        def register(fn: ToolHandler) -> ToolHandler:
            self.add(name, fn)
            return fn

        return register

    def execute(
        self,
        name: str,
        args: dict[str, Any],
        context: RuntimeContext,
    ) -> str:
        handler = self._handlers.get(name)
        if handler is None:
            raise UnknownToolError(name)
        started = time.monotonic_ns()
        ok = False
        error_type = None
        try:
            result = handler(dict(args), context)
            if not isinstance(result, str):
                raise TypeError(f"tool {name!r} returned {type(result).__name__}, expected str")
            ok = True
            return result
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            record = ToolExecutionRecord(
                name=name,
                world_id=str(getattr(context, "world_id", "unknown")),
                module_name=str(getattr(context, "module_name", "unknown")),
                started_ns=started,
                duration_ns=time.monotonic_ns() - started,
                ok=ok,
                error_type=error_type,
            )
            with self._lock:
                self._audit.append(record)
                excess = len(self._audit) - self.audit_limit
                if excess > 0:
                    del self._audit[:excess]

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._handlers)

    def audit_snapshot(self) -> tuple[ToolExecutionRecord, ...]:
        with self._lock:
            return tuple(self._audit)
