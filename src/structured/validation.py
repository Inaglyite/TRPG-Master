"""协议帧与事件校验：以 schemas/structured-play/v1/ 为唯一正本。

收到的请求/命令帧必须过 schema 才进入服务层（schema 有牙齿，服务层再做
事实与权限检查）；服务端发出的事件信封同样过 events.json，防止“后端发出
自己都不认的消息”。
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from .errors import StructuredError

SCHEMA_DIR = Path(__file__).resolve().parent.parent.parent / "schemas" / "structured-play" / "v1"

_FRAME_SCHEMAS = {
    "action_request": "action_request.json",
    "free_roll_request": "free_roll_request.json",
    "check_response": "check_response.json",
    "cancel_request": "cancel_request.json",
    "command_request": "command_request.json",
    "memory_query": "memory_query.json",
}

_registry: Registry | None = None
_schemas: dict[str, dict] | None = None


def _load() -> tuple[Registry, dict[str, dict]]:
    global _registry, _schemas
    if _registry is None or _schemas is None:
        schemas = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in SCHEMA_DIR.glob("*.json")
            if path.name != "permission-matrix.json"
        }
        registry = Registry()
        for schema in schemas.values():
            registry = registry.with_resource(
                schema["$id"],
                Resource.from_contents(schema, default_specification=DRAFT202012),
            )
        _registry, _schemas = registry, schemas
    return _registry, _schemas


def _validator(name: str) -> jsonschema.Draft202012Validator:
    registry, schemas = _load()
    return jsonschema.Draft202012Validator(schemas[name], registry=registry)


def validate_frame(frame_type: str, payload: dict) -> None:
    """校验一帧客户端请求；不合法抛 invalid_action（不进入服务层）。"""
    schema_name = _FRAME_SCHEMAS.get(frame_type)
    if schema_name is None:
        raise StructuredError(
            "unsupported_protocol", f"未知结构化帧类型：{frame_type}", retryable=False
        )
    error = next(iter(_validator(schema_name).iter_errors(payload)), None)
    if error is not None:
        path = ".".join(str(part) for part in error.absolute_path)
        raise StructuredError(
            "invalid_action",
            f"请求不符合协议：{path or '根字段'} {error.message}"[:300],
            retryable=False,
        )


def validate_command(kind: str, payload: dict) -> None:
    """校验一条**程序化构造**的主持命令（Agent 生成 / 内部调用）与冻结 schema 一致。

    客户端帧在网关已过 schema；Agent 生成的 payload 走的是同一条命令入口，
    却没有帧校验——只靠提示词写枚举会让「错误类型进入执行层」（真实模型实测：
    把自由文本写进 outcome、把 audience 写成字符串）。这里用同一份
    command_request.json 校验，让执行层与模型可见定义严格一致。
    """
    envelope = {
        "type": "command_request",
        "protocol_version": 1,
        "command_id": "command-validation",
        "world_id": "command-validation",
        "expected_revision": 0,
        "kind": str(kind or ""),
        "payload": payload if isinstance(payload, dict) else {},
    }
    validate_frame("command_request", envelope)


def validate_event(envelope: dict) -> None:
    """校验服务端待发布事件；不合法是内部错误（不投递、不落 outbox 之外的地方）。"""
    error = next(iter(_validator("events.json").iter_errors(envelope)), None)
    if error is not None:
        raise StructuredError(
            "internal_error",
            f"事件不符合协议：{envelope.get('type')} {error.message}"[:300],
            retryable=False,
        )
