"""H2 私有上下文检查点（context checkpoint）——存档/回合提交的可选私有元数据。

``ContextCheckpoint`` 把一次私有上下文压缩绑定到具体的持久化边界（手动存档、
回合提交），本身不携带任何提示词/工具输出内容，只记录定位与完整性信息：

- ``session_id`` + ``session_epoch``：定位 H2 context session（epoch 每次重开递增）；
- ``sequence``：该检查点对应的私有上下文事件游标；
- ``surface_digest``：表面（模型可见）消息的 64 位小写十六进制摘要；
- ``source_turn_id``：产生该检查点的回合（可选，旧数据可能缺失）。

对象刻意保持不可变且自包含。保存侧用 ``merge_into`` 把它放进内部
``metadata["context"]``；公开列表（``list_saves`` / ``list_tree_saves``）必须用
``public_copy`` 剥离该键，确保私有上下文标识不会进入协议/前端可见的存档列表。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = 1
_CONTEXT_KEY = "context"
_SURFACE_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ContextCheckpoint:
    """不可变的私有上下文检查点。字段在 ``from_mapping`` 中严格校验。"""

    session_id: str
    session_epoch: int
    sequence: int
    surface_digest: str
    source_turn_id: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Direct construction is subject to the same persistence contract."""
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != SCHEMA_VERSION
        ):
            raise ValueError(
                f"不支持的 context checkpoint schema_version: {self.schema_version!r}"
            )
        if (
            not isinstance(self.session_id, str)
            or not self.session_id.strip()
            or self.session_id != self.session_id.strip()
        ):
            raise ValueError("session_id 必须是无首尾空白的非空字符串")
        if (
            isinstance(self.session_epoch, bool)
            or not isinstance(self.session_epoch, int)
            or self.session_epoch < 1
        ):
            raise ValueError("session_epoch 必须是 >= 1 的整数")
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 0
        ):
            raise ValueError("sequence 必须是 >= 0 的整数")
        if self.source_turn_id is not None and (
            not isinstance(self.source_turn_id, str)
            or not self.source_turn_id.strip()
            or self.source_turn_id != self.source_turn_id.strip()
        ):
            raise ValueError("source_turn_id 必须是无首尾空白的非空字符串或 None")
        if (
            not isinstance(self.surface_digest, str)
            or _SURFACE_DIGEST_PATTERN.fullmatch(self.surface_digest) is None
        ):
            raise ValueError("surface_digest 必须是 64 位小写十六进制字符串")

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> ContextCheckpoint:
        """严格解析 mapping 为 checkpoint，非法输入抛 ValueError/TypeError。

        - 未知键、非当前 schema_version 直接拒绝；
        - ``session_id`` 必须是非空字符串；
        - ``session_epoch`` 必须是 ``>= 1`` 的整数（拒绝 bool）；
        - ``sequence`` 必须是 ``>= 0`` 的整数（拒绝 bool）；
        - ``source_turn_id`` 可选：None / 非空字符串（空字符串规范化为 None）；
        - ``surface_digest`` 必须匹配 64 位小写十六进制。
        """
        if not isinstance(mapping, Mapping):
            raise TypeError(
                f"context checkpoint 必须是 mapping，收到 {type(mapping).__name__}"
            )
        allowed = {
            "schema_version",
            "session_id",
            "session_epoch",
            "sequence",
            "source_turn_id",
            "surface_digest",
        }
        unknown = sorted(set(mapping) - allowed)
        if unknown:
            raise ValueError(
                f"context checkpoint 含未知字段: {', '.join(map(str, unknown))}"
            )

        schema_version = mapping.get("schema_version", SCHEMA_VERSION)
        if isinstance(schema_version, bool) or schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"不支持的 context checkpoint schema_version: {schema_version!r}"
            )

        session_id = mapping.get("session_id")
        if (
            not isinstance(session_id, str)
            or not session_id.strip()
            or session_id != session_id.strip()
        ):
            raise ValueError("session_id 必须是无首尾空白的非空字符串")

        session_epoch = mapping.get("session_epoch")
        if (
            isinstance(session_epoch, bool)
            or not isinstance(session_epoch, int)
            or session_epoch < 1
        ):
            raise ValueError("session_epoch 必须是 >= 1 的整数")

        sequence = mapping.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("sequence 必须是 >= 0 的整数")

        source_turn_id = mapping.get("source_turn_id")
        if source_turn_id is None:
            source_turn_id = None
        elif isinstance(source_turn_id, str) and not source_turn_id.strip():
            source_turn_id = None
        elif not isinstance(source_turn_id, str) or source_turn_id != source_turn_id.strip():
            raise ValueError("source_turn_id 必须是无首尾空白的字符串或 None")

        surface_digest = mapping.get("surface_digest")
        if (
            not isinstance(surface_digest, str)
            or _SURFACE_DIGEST_PATTERN.fullmatch(surface_digest) is None
        ):
            raise ValueError("surface_digest 必须是 64 位小写十六进制字符串")

        return cls(
            session_id=session_id,
            session_epoch=session_epoch,
            sequence=sequence,
            surface_digest=surface_digest,
            source_turn_id=source_turn_id,
            schema_version=schema_version,
        )

    def to_dict(self) -> dict[str, Any]:
        """规范化的 JSON 表示（roundtrip 稳定）。"""
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "session_epoch": self.session_epoch,
            "sequence": self.sequence,
            "source_turn_id": self.source_turn_id,
            "surface_digest": self.surface_digest,
        }

    def merge_into(self, metadata: Mapping[str, Any]) -> dict[str, Any]:
        """把本检查点合并进内部 metadata 的 ``context`` 键（返回新 dict）。

        不修改传入的 ``metadata``；已存在的 ``context`` 会被本检查点覆盖。
        """
        merged = dict(metadata)
        merged[_CONTEXT_KEY] = self.to_dict()
        return merged


def resolve_checkpoint(
    value: ContextCheckpoint | Mapping[str, Any] | None,
) -> ContextCheckpoint | None:
    """把调用方传入的 checkpoint（实例或 mapping）规范化为实例，写前校验。

    调用方必须在任何持久化写入之前执行本函数：非法 mapping 会在这里抛错，
    从而保证"非法 checkpoint 写前失败"。
    """
    if value is None or isinstance(value, ContextCheckpoint):
        return value
    return ContextCheckpoint.from_mapping(value)


def public_copy(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """返回剥离私有 ``context`` 键的 metadata 副本（公开列表专用）。"""
    result = dict(metadata)
    result.pop(_CONTEXT_KEY, None)
    return result
