"""Helpers for reading and writing dot-separated world-state paths."""

from __future__ import annotations


def resolve_path(data: object, path: str) -> object:
    """Read a nested dict/list value using a dot-separated path."""
    current = data
    for part in str(path).split("."):
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                raise KeyError(f"列表索引 '{part}' 不存在于 {current}") from exc
        elif isinstance(current, dict):
            if part not in current:
                raise KeyError(f"键 '{part}' 不存在于 {list(current.keys())}")
            current = current[part]
        else:
            raise KeyError(f"无法从 {type(current)} 中访问 '{part}'")
    return current


def set_path(data: dict, path: str, value: object) -> None:
    """Write a nested dict/list value using a dot-separated path."""
    parts = str(path).split(".")
    current: object = data
    for part in parts[:-1]:
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current.setdefault(part, {})
        else:
            raise KeyError(part)
    if isinstance(current, list):
        current[int(parts[-1])] = value
    elif isinstance(current, dict):
        current[parts[-1]] = value
    else:
        raise KeyError(parts[-1])
