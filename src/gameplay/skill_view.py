"""规则表与角色卡的统一有效技能视图（只读适配，不改写角色卡或存档）。

角色卡上的显式值永远优先；规则表（rules/rule_schema.json）明确列出的技能
在卡上缺项时按基础值可用（基础值来自 schema；DEX/2、EDU 等公式按属性解析，
属性缺失时该技能不可用）。schema 之外的虚构技能在任何一层都不可用——
裁决约束、裁决校验与实际掷骰共用本视图，保持一致。不复用旧 CLI 的
“未知技能默认 50”兼容行为。
"""

from __future__ import annotations

import json
from functools import lru_cache

from src.app.config import PROJECT_ROOT


@lru_cache(maxsize=1)
def rule_skill_catalog() -> dict[str, dict]:
    """rules/rule_schema.json 声明的技能目录：id -> 规则条目。"""
    try:
        schema = json.loads((PROJECT_ROOT / "rules" / "rule_schema.json").read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        str(item["id"]): item
        for item in schema.get("skills", [])
        if isinstance(item, dict) and item.get("id")
    }


@lru_cache(maxsize=1)
def rule_attribute_catalog() -> dict[str, dict]:
    """rules/rule_schema.json 声明的属性目录：id -> 规则条目。"""
    try:
        schema = json.loads((PROJECT_ROOT / "rules" / "rule_schema.json").read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        str(item["id"]): item
        for item in schema.get("attributes", [])
        if isinstance(item, dict) and item.get("id")
    }


def display_name(check_id: str) -> str:
    """规则表中文名（技能优先，其次属性）；卡上自定义技能键本身即显示名。"""
    entry = rule_skill_catalog().get(check_id)
    if entry and entry.get("name"):
        return str(entry["name"])
    attribute = rule_attribute_catalog().get(check_id)
    if attribute and attribute.get("name"):
        return str(attribute["name"])
    return check_id


@lru_cache(maxsize=1)
def rule_name_aliases() -> dict[str, str]:
    """中文名 -> 规则键，只收录全表唯一的名字（子技能类重名不收）。

    供平台把模型用中文名提出的技能提示解析回规则键；多义名字宁可不解析。
    """
    names: dict[str, str] = {}
    ambiguous: set[str] = set()
    for catalog in (rule_skill_catalog(), rule_attribute_catalog()):
        for key, entry in catalog.items():
            name = str(entry.get("name") or "").strip()
            if not name or name in ambiguous:
                continue
            if name in names and names[name] != key:
                ambiguous.add(name)
                del names[name]
                continue
            names[name] = key
    return names


def _schema_base_value(entry: dict, attributes: dict) -> int | None:
    base = entry.get("base")
    if isinstance(base, bool):
        return None
    if isinstance(base, int):
        return base
    # 受限公式：只支持 "<ATTR>" 与 "<ATTR>/2"（如 language_own=EDU、dodge=DEX/2）。
    text = str(base or "").strip().upper()
    halved = text.endswith("/2")
    attribute = text[:-2] if halved else text
    if not attribute or attribute not in attributes:
        return None
    try:
        value = int(attributes[attribute])
    except (TypeError, ValueError):
        return None
    return value // 2 if halved else value


def _overlay_explicit(source: dict, view: dict) -> None:
    """卡上显式值优先；显式损坏（非整数）的键标为不可用，绝不回退到基础值。"""
    for key, value in source.items():
        key = str(key)
        if isinstance(value, bool):
            view.pop(key, None)
            continue
        try:
            view[key] = int(value)
        except (TypeError, ValueError):
            view.pop(key, None)


def effective_skills(pc: dict, *, include_attributes: bool = True) -> dict[str, int]:
    """返回当前角色可参与检定的 {技能/属性 id: 有效值} 只读视图。

    include_attributes=False 用于只认技能键的通道（如结构化 request_check）。
    """
    pc = pc if isinstance(pc, dict) else {}
    attributes = pc.get("attributes") if isinstance(pc.get("attributes"), dict) else {}
    skills = pc.get("skills") if isinstance(pc.get("skills"), dict) else {}
    view: dict[str, int] = {}
    for skill_id, entry in rule_skill_catalog().items():
        base = _schema_base_value(entry, attributes)
        if base is not None:
            view[skill_id] = base
    # 卡上显式技能（含模组自定义技能）覆盖规则表基础值。
    _overlay_explicit(skills, view)
    if include_attributes:
        # 属性键（STR 等）压过同名技能键：与旧掷骰层 attributes 优先一致。
        _overlay_explicit(attributes, view)
    return view


def effective_skill_value(pc: dict, skill: str, *, include_attributes: bool = True) -> int | None:
    """解析单个检定键；schema 与角色卡都不认识的键返回 None。"""
    return effective_skills(pc, include_attributes=include_attributes).get(str(skill))
