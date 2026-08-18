"""Request-scoped authorization and strict argument validation for model tools.

The provider may return structured function calls or DSML embedded in text.  Both
forms are untrusted model output, so neither may select a handler from the global
registry without carrying the immutable tool catalog that was sent with *this*
model request.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

MODEL_CALLER = "model"
ENGINE_INTERNAL_CALLER = "engine_internal"
REQUEST_METADATA_KEY = "_trpg_request"

# These generic/internal capabilities must never become model-callable merely
# because an old prompt, DSML block, replay record, or future profile lists them.
MODEL_DENIED_TOOL_NAMES = frozenset(
    {
        "read_file",
        "state_get",
        "state_set",
        "get_npc_secret",
        "get_private_memory",
        "update_private_memory",
        "cache_scene",
    }
)

_SAFE_STATE_SEGMENT = r"[A-Za-z_][A-Za-z0-9_-]*"
_ENGINE_INTERNAL_STATE_PATHS = {
    "state_get": (
        re.compile(r"^pc\.(?:hp|san|luck|inventory)$"),
        re.compile(r"^current_scene(?:\.(?:id|name|npcs_present))?$"),
        re.compile(rf"^flags\.{_SAFE_STATE_SEGMENT}$"),
        re.compile(rf"^case_clocks\.{_SAFE_STATE_SEGMENT}$"),
        re.compile(r"^combat_state(?:\.active)?$"),
    ),
    "state_set": (
        re.compile(r"^current_scene(?:\.id)?$"),
        re.compile(rf"^flags\.{_SAFE_STATE_SEGMENT}$"),
        re.compile(rf"^case_clocks\.{_SAFE_STATE_SEGMENT}$"),
        re.compile(r"^npcs\.\d+\.current_location$"),
        re.compile(r"^pc\.hp$"),
        re.compile(r"^combat_state\.active$"),
    ),
}


class ToolPolicyError(ValueError):
    """A rejected model tool call with a stable, non-sensitive error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def allows_engine_internal_state_path(name: str, args: object) -> bool:
    """Allow only compatibility state paths required by trusted engine code."""
    if not isinstance(args, dict):
        return False
    path = str(args.get("path") or "")
    return any(pattern.fullmatch(path) for pattern in _ENGINE_INTERNAL_STATE_PATHS.get(name, ()))


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def catalog_digest(tools: list[dict]) -> str:
    """Digest exactly the ordered tool catalog sent to one provider request."""
    return hashlib.sha256(_canonical_json(tools).encode("utf-8")).hexdigest()


def payload_digest(value: object) -> str:
    """Digest model-visible data for diagnostics without persisting its text twice."""
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ToolRequestSnapshot:
    """Immutable authority evidence carried from request construction to execution."""

    request_id: str
    step: int
    profile: str
    caller: str
    allowed_tool_names: tuple[str, ...]
    tool_catalog_digest: str

    @classmethod
    def create(
        cls,
        *,
        step: int,
        profile: str,
        caller: str,
        tools: list[dict],
    ) -> ToolRequestSnapshot:
        names = tuple(
            str(tool.get("function", {}).get("name") or "")
            for tool in tools
            if str(tool.get("function", {}).get("name") or "")
        )
        return cls(
            request_id=uuid.uuid4().hex,
            step=step,
            profile=profile,
            caller=caller,
            allowed_tool_names=names,
            tool_catalog_digest=catalog_digest(tools),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "step": self.step,
            "profile": self.profile,
            "caller": self.caller,
            "allowed_tool_names": list(self.allowed_tool_names),
            "tool_catalog_digest": self.tool_catalog_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> ToolRequestSnapshot:
        if not isinstance(value, dict):
            raise ToolPolicyError("missing_request_snapshot", "工具调用缺少请求授权快照")
        request_id = value.get("request_id")
        profile = value.get("profile")
        caller = value.get("caller")
        digest = value.get("tool_catalog_digest")
        allowed = value.get("allowed_tool_names")
        step = value.get("step")
        if (
            not isinstance(request_id, str)
            or not request_id
            or not isinstance(profile, str)
            or not isinstance(caller, str)
            or not isinstance(digest, str)
            or not isinstance(step, int)
            or isinstance(step, bool)
            or not isinstance(allowed, list)
            or not all(isinstance(name, str) and name for name in allowed)
        ):
            raise ToolPolicyError("invalid_request_snapshot", "工具调用的请求授权快照无效")
        return cls(
            request_id=request_id,
            step=step,
            profile=profile,
            caller=caller,
            allowed_tool_names=tuple(allowed),
            tool_catalog_digest=digest,
        )


def attach_request_snapshot(tool_call: dict, snapshot: ToolRequestSnapshot) -> dict:
    """Attach server-generated policy metadata without changing provider fields."""
    call = {
        "id": str(tool_call.get("id") or ""),
        "type": str(tool_call.get("type") or "function"),
        "function": dict(tool_call.get("function") or {}),
        REQUEST_METADATA_KEY: snapshot.to_dict(),
    }
    return call


def public_tool_call(tool_call: dict) -> dict:
    """Remove server-only policy metadata before persisting/sending model history."""
    function = tool_call.get("function") if isinstance(tool_call, dict) else {}
    function = function if isinstance(function, dict) else {}
    return {
        "id": str(tool_call.get("id") or ""),
        "type": str(tool_call.get("type") or "function"),
        "function": {
            "name": str(function.get("name") or ""),
            "arguments": str(function.get("arguments") or "{}"),
        },
    }


def _schema_for_name(tool_schemas: dict[str, dict], name: str) -> dict:
    schema = tool_schemas.get(name)
    if not isinstance(schema, dict):
        raise ToolPolicyError("unknown_tool", "工具不在当前服务端目录中")
    return schema


def _validate_value(value: object, schema: dict, path: str) -> None:
    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(value, dict):
            raise ToolPolicyError("invalid_arguments", f"{path} 必须是对象")
        properties = schema.get("properties") or {}
        if not isinstance(properties, dict):
            raise ToolPolicyError("invalid_schema", f"{path} 的 schema 无效")
        unknown = sorted(set(value) - set(properties))
        if unknown:
            raise ToolPolicyError("unknown_argument", f"{path} 包含未允许字段")
        required = schema.get("required") or []
        missing = [key for key in required if key not in value]
        if missing:
            raise ToolPolicyError("missing_argument", f"{path} 缺少必填字段")
        for key, child in properties.items():
            if key in value and isinstance(child, dict):
                _validate_value(value[key], child, f"{path}.{key}")
    elif expected_type == "array":
        if not isinstance(value, list):
            raise ToolPolicyError("invalid_arguments", f"{path} 必须是数组")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_value(item, item_schema, f"{path}[{index}]")
    elif expected_type == "string":
        if not isinstance(value, str):
            raise ToolPolicyError("invalid_arguments", f"{path} 必须是字符串")
    elif expected_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ToolPolicyError("invalid_arguments", f"{path} 必须是整数")
    elif expected_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ToolPolicyError("invalid_arguments", f"{path} 必须是数字")
    elif expected_type == "boolean" and not isinstance(value, bool):
        raise ToolPolicyError("invalid_arguments", f"{path} 必须是布尔值")

    allowed_values = schema.get("enum")
    if isinstance(allowed_values, list) and value not in allowed_values:
        raise ToolPolicyError("invalid_arguments", f"{path} 不在允许范围内")
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(minimum, (int, float)) and value < minimum:
            raise ToolPolicyError("invalid_arguments", f"{path} 小于最小值")
        if isinstance(maximum, (int, float)) and value > maximum:
            raise ToolPolicyError("invalid_arguments", f"{path} 大于最大值")


def authorize_model_tool_call(
    tool_call: dict,
    *,
    tool_schemas: dict[str, dict],
    ordered_catalog: list[dict],
) -> tuple[ToolRequestSnapshot, str, dict[str, Any]]:
    """Return validated ``(snapshot, name, args)`` or raise ``ToolPolicyError``."""
    snapshot = ToolRequestSnapshot.from_dict(tool_call.get(REQUEST_METADATA_KEY))
    if snapshot.caller != MODEL_CALLER:
        raise ToolPolicyError("invalid_caller", "模型工具调用的来源无效")
    if snapshot.tool_catalog_digest != catalog_digest(ordered_catalog):
        raise ToolPolicyError("catalog_mismatch", "工具目录快照与当前请求不一致")
    function = tool_call.get("function") if isinstance(tool_call, dict) else None
    if not isinstance(function, dict):
        raise ToolPolicyError("invalid_tool_call", "工具调用格式无效")
    name = function.get("name")
    if not isinstance(name, str) or not name:
        raise ToolPolicyError("invalid_tool_name", "工具名称无效")
    if name in MODEL_DENIED_TOOL_NAMES:
        raise ToolPolicyError("model_tool_forbidden", "该工具不能由模型调用")
    if name not in snapshot.allowed_tool_names:
        raise ToolPolicyError("tool_not_allowed", "该工具未下发给本次模型请求")
    schema = _schema_for_name(tool_schemas, name)
    raw_arguments = function.get("arguments")
    if not isinstance(raw_arguments, str):
        raise ToolPolicyError("invalid_arguments", "工具参数必须是 JSON 对象")
    try:
        args = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise ToolPolicyError("invalid_arguments", "工具参数不是合法 JSON") from exc
    _validate_value(args, schema, "arguments")
    return snapshot, name, args


def denied_tool_result(error: ToolPolicyError) -> str:
    """Return a safe tool result without reflecting caller-controlled arguments."""
    return json.dumps(
        {"ok": False, "error": "tool_policy_denied", "reason": error.code},
        ensure_ascii=False,
        separators=(",", ":"),
    )
