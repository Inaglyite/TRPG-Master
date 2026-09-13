"""稳定 ID 与载荷摘要。"""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any


def canonical_digest(payload: Any) -> str:
    """去重摘要：canonical JSON（键序/空白规范化）的 sha256。"""
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def new_row_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def new_stable_id(prefix: str) -> str:
    """世界内稳定对象 ID（物品/线索注册表、检定、消息等）。"""
    return f"{prefix}_{secrets.token_hex(6)}"
