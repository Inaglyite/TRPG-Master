"""角色库：可复用角色卡的校验、存储、归属隔离与开局解析。

定位（与 characters.py 的分工）：本模块只管「库」——版本化卡面格式的
校验、按 owner 隔离的 CRUD、导出信封、以及供 resolve_character 使用的
带归属校验的读取。列表/摘要的展示拼接在 characters.py 完成。

关键契约：
- 卡面 JSON 是用户资料，永远只是数据；文件中的 id/owner_user_id/world_id
  等身份与权限字段一律剥离，不作为可信输入（导入告警中明示）。
- 推导字段（HP/SAN/MP/MOV/DB/BUILD）以属性为准由服务端重算，与输入不一致
  时在告警中列出；LUCK 尊重输入（掷骰结果不可复算）。
- 规则上可疑但合法的配置（属性超出建卡范围、技能值过高等）只告警不拦截。
- 修改/删除库条目不影响已开局世界：开局时物化为世界内快照（见
  characters.character_to_pc），之后世界状态与库条目互不回写。
"""

from __future__ import annotations

import copy
from typing import Any

from src.auth.service import auth_required
from src.storage.database import (
    CharacterLibraryEntry,
    World,
    new_id,
    session_scope,
    utcnow,
)

CARD_FORMAT = "trpg-character-card"
CARD_FORMAT_VERSION = 1
MAX_CARD_BYTES = 256 * 1024
MAX_NAME_LEN = 40
MAX_OCCUPATION_LEN = 40
MAX_ERA_LEN = 20
MAX_TEXT_FIELD_LEN = 4000
MAX_INVENTORY_ITEMS = 50
MAX_INVENTORY_ITEM_LEN = 120
MAX_SKILLS = 120

ATTRIBUTES = ("STR", "DEX", "CON", "INT", "POW", "SIZ", "APP", "EDU")
# 建卡范围（3D6*5 / 2D6+6*5）：超出不拦截，只告警（老卡可成长超界）。
ATTRIBUTE_WARN_LOW = 15
ATTRIBUTE_WARN_HIGH = 90
ATTRIBUTE_HARD_LOW = 1
ATTRIBUTE_HARD_HIGH = 200
SKILL_WARN_HIGH = 90
SKILL_HARD_HIGH = 200

# 文件里的身份/权限/版本字段一律不可信：导入/创建时剥离并逐条告警。
IGNORED_FIELDS = (
    "id",
    "created_at",
    "updated_at",
    "owner_user_id",
    "user_id",
    "world_id",
    "controller_user_id",
    "investigator_id",
    "permissions",
    "role",
    "is_admin",
    "format",
    "format_version",
)


class CharacterLibraryError(Exception):
    """可预期的角色库错误：code 供前端分支，message 面向玩家。"""

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


# ---------------------------------------------------------------- 推导字段


def _damage_bonus(total: int) -> str:
    """与 tools/character.py 同源的 CoC 7e 伤害加值表。"""
    if total < 65:
        return "-2"
    if total < 85:
        return "-1"
    if total < 125:
        return "0"
    if total < 165:
        return "+1D4"
    return "+1D6"


def _build(total: int) -> int:
    if total < 65:
        return -2
    if total < 85:
        return -1
    if total < 125:
        return 0
    if total < 165:
        return 1
    return 2


def derive_stats(attributes: dict[str, int]) -> dict[str, Any]:
    """由八项属性推导 HP/SAN/MP/MOV/DB/BUILD（确定性部分）。

    LUCK 是掷骰结果、无法由属性复算，不在此处推导——导入时尊重输入值。
    """
    return {
        "HP": (attributes["SIZ"] + attributes["CON"]) // 10,
        "max_HP": (attributes["SIZ"] + attributes["CON"]) // 10,
        "SAN": attributes["POW"],
        "max_SAN": attributes["POW"],
        "MP": attributes["POW"] // 5,
        "MOV": 8,
        "DB": _damage_bonus(attributes["STR"] + attributes["SIZ"]),
        "BUILD": _build(attributes["STR"] + attributes["SIZ"]),
    }


# ---------------------------------------------------------------- 校验


def _err(errors: list[dict], field: str, message: str) -> None:
    errors.append({"field": field, "message": message})


def _validate_text(
    value: Any,
    field: str,
    errors: list[dict],
    *,
    required: bool = False,
    max_len: int = MAX_TEXT_FIELD_LEN,
    label: str,
) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            _err(errors, field, f"{label}不能为空")
        return ""
    if not isinstance(value, str):
        _err(errors, field, f"{label}必须是字符串")
        return ""
    text = value.strip()
    if len(text) > max_len:
        _err(errors, field, f"{label}超长（{len(text)} 字，上限 {max_len}）")
    return text


def _validate_int(
    value: Any,
    field: str,
    errors: list[dict],
    *,
    low: int,
    high: int,
    label: str,
) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        _err(errors, field, f"{label}必须是整数")
        return None
    if value < low or value > high:
        _err(errors, field, f"{label}超出合法范围（{low}~{high}）")
        return None
    return value


def validate_card(raw: Any) -> tuple[dict, list[dict], list[str]]:
    """校验并规范化一张角色卡。返回 (normalized_card, errors, warnings)。

    errors 非空即不可导入；warnings 只提示不改写数值（推导字段除外，
    重算会逐条列入 warnings）。未识别的顶层字段保留并告警，不静默丢弃。
    """
    errors: list[dict] = []
    warnings: list[str] = []
    if not isinstance(raw, dict):
        _err(errors, "(root)", "角色卡必须是 JSON 对象")
        return {}, errors, warnings

    ignored = [key for key in IGNORED_FIELDS if key in raw]
    if ignored:
        warnings.append(
            "已忽略文件中的身份/权限字段（不作可信输入）：" + "、".join(sorted(ignored))
        )
    unknown = sorted(
        key
        for key in raw
        if key
        not in {
            "name",
            "occupation",
            "age",
            "era",
            "attributes",
            "derived",
            "skills",
            "inventory",
            "credit_rating",
            "backstory",
            "psychological_profile",
            "career",
            "portrait",
            "violence_stance",
        }
        and key not in IGNORED_FIELDS
    )
    if unknown:
        warnings.append("保留了未识别的字段（不影响使用）：" + "、".join(unknown))

    card: dict[str, Any] = {}
    card["name"] = _validate_text(
        raw.get("name"), "name", errors, required=True, max_len=MAX_NAME_LEN, label="姓名"
    )
    card["occupation"] = _validate_text(
        raw.get("occupation"),
        "occupation",
        errors,
        required=True,
        max_len=MAX_OCCUPATION_LEN,
        label="职业",
    )
    card["era"] = _validate_text(
        raw.get("era"), "era", errors, max_len=MAX_ERA_LEN, label="年代"
    )
    age = raw.get("age")
    if age is None or age == "":
        card["age"] = None
    else:
        card["age"] = _validate_int(age, "age", errors, low=1, high=150, label="年龄")
        if card["age"] is not None and (card["age"] < 15 or card["age"] > 90):
            warnings.append(f"年龄 {card['age']} 超出常见范围（15~90）")

    attributes = raw.get("attributes")
    normalized_attrs: dict[str, int] = {}
    if not isinstance(attributes, dict):
        _err(errors, "attributes", "缺少属性表（attributes 必须是对象）")
    else:
        for key in ATTRIBUTES:
            value = _validate_int(
                attributes.get(key),
                f"attributes.{key}",
                errors,
                low=ATTRIBUTE_HARD_LOW,
                high=ATTRIBUTE_HARD_HIGH,
                label=f"属性 {key}",
            )
            if value is None:
                continue
            normalized_attrs[key] = value
            if value < ATTRIBUTE_WARN_LOW or value > ATTRIBUTE_WARN_HIGH:
                warnings.append(
                    f"属性 {key}={value} 超出建卡范围（{ATTRIBUTE_WARN_LOW}~{ATTRIBUTE_WARN_HIGH}），已按原值保留"
                )
        extra_attrs = sorted(set(attributes) - set(ATTRIBUTES))
        if extra_attrs:
            warnings.append("忽略未识别的属性项：" + "、".join(extra_attrs))
    card["attributes"] = normalized_attrs

    if not errors and len(normalized_attrs) == len(ATTRIBUTES):
        derived = derive_stats(normalized_attrs)
        luck = (raw.get("derived") or {}).get("LUCK") if isinstance(raw.get("derived"), dict) else None
        if luck is None:
            warnings.append("未提供幸运（LUCK），按 50 处理")
            derived["LUCK"] = 50
        else:
            luck_value = _validate_int(
                luck, "derived.LUCK", errors, low=1, high=200, label="幸运 LUCK"
            )
            derived["LUCK"] = luck_value if luck_value is not None else 50
        provided = raw.get("derived") if isinstance(raw.get("derived"), dict) else {}
        recomputed = [
            key
            for key, value in derived.items()
            if key in provided and provided[key] != value
        ]
        if recomputed:
            warnings.append(
                "推导字段与属性不一致，已按属性重算：" + "、".join(sorted(recomputed))
            )
        card["derived"] = derived
    else:
        card["derived"] = {}

    skills = raw.get("skills", {})
    normalized_skills: dict[str, int] = {}
    if not isinstance(skills, dict):
        _err(errors, "skills", "技能表（skills）必须是对象")
    elif len(skills) > MAX_SKILLS:
        _err(errors, "skills", f"技能数量超过上限（{MAX_SKILLS}）")
    else:
        for key, value in skills.items():
            skill_id = str(key).strip()
            if not skill_id:
                _err(errors, "skills", "技能 id 不能为空")
                continue
            parsed = _validate_int(
                value,
                f"skills.{skill_id}",
                errors,
                low=0,
                high=SKILL_HARD_HIGH,
                label=f"技能 {skill_id}",
            )
            if parsed is None:
                continue
            if parsed > SKILL_WARN_HIGH:
                warnings.append(f"技能 {skill_id}={parsed} 高于建卡上限 {SKILL_WARN_HIGH}，已按原值保留")
            normalized_skills[skill_id] = parsed
    card["skills"] = normalized_skills

    credit = raw.get("credit_rating")
    if credit is None:
        card["credit_rating"] = normalized_skills.get("credit_rating", 0)
    else:
        card["credit_rating"] = (
            _validate_int(credit, "credit_rating", errors, low=0, high=200, label="信用评级") or 0
        )
        if card["credit_rating"] > 99:
            warnings.append(f"信用评级 {card['credit_rating']} 超出常见范围（0~99）")

    inventory = raw.get("inventory", [])
    normalized_inventory: list[Any] = []
    if not isinstance(inventory, list):
        _err(errors, "inventory", "随身物品（inventory）必须是数组")
    elif len(inventory) > MAX_INVENTORY_ITEMS:
        _err(errors, "inventory", f"随身物品超过上限（{MAX_INVENTORY_ITEMS} 件）")
    else:
        for index, item in enumerate(inventory):
            if isinstance(item, str):
                text = item.strip()
                if not text:
                    continue
                if len(text) > MAX_INVENTORY_ITEM_LEN:
                    _err(errors, f"inventory[{index}]", f"物品条目超长（上限 {MAX_INVENTORY_ITEM_LEN} 字）")
                    continue
                normalized_inventory.append(text)
            elif isinstance(item, dict) and (item.get("label") or item.get("name") or item.get("id")):
                normalized_inventory.append(copy.deepcopy(item))
            else:
                _err(errors, f"inventory[{index}]", "物品条目必须是字符串或含 label/name/id 的对象")
    card["inventory"] = normalized_inventory

    backstory = raw.get("backstory", {})
    if not isinstance(backstory, dict):
        _err(errors, "backstory", "背景资料（backstory）必须是对象")
        backstory = {}
    normalized_backstory: dict[str, Any] = {}
    for key, value in backstory.items():
        field = f"backstory.{key}"
        if isinstance(value, str):
            normalized_backstory[str(key)] = _validate_text(
                value, field, errors, label=f"背景字段 {key}"
            )
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            normalized_backstory[str(key)] = [item.strip() for item in value if item.strip()]
        else:
            _err(errors, field, "背景字段必须是字符串或字符串数组")
    card["backstory"] = normalized_backstory

    profile = raw.get("psychological_profile", {})
    if not isinstance(profile, dict):
        _err(errors, "psychological_profile", "心理档案（psychological_profile）必须是对象")
        profile = {}
    normalized_profile: dict[str, list[str]] = {}
    for key in ("traits", "key_relationships", "phobias", "manias"):
        value = profile.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            _err(errors, f"psychological_profile.{key}", "心理档案字段必须是字符串数组")
            value = []
        normalized_profile[key] = [item.strip() for item in value if item.strip()]
    card["psychological_profile"] = normalized_profile

    career = raw.get("career", {})
    if not isinstance(career, dict):
        _err(errors, "career", "履历（career）必须是对象")
        career = {}
    normalized_career: dict[str, Any] = {
        "reputation": 0,
        "titles": [],
        "known_contacts": [],
        "completed_modules": [],
        "case_history": [],
    }
    reputation = career.get("reputation", 0)
    if isinstance(reputation, bool) or not isinstance(reputation, int):
        _err(errors, "career.reputation", "声望必须是整数")
    else:
        normalized_career["reputation"] = reputation
    for key in ("titles", "known_contacts", "completed_modules"):
        value = career.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            _err(errors, f"career.{key}", "履历字段必须是字符串数组")
            value = []
        normalized_career[key] = [str(item) for item in value]
    case_history = career.get("case_history", [])
    if not isinstance(case_history, list):
        _err(errors, "career.case_history", "案件履历必须是数组")
        case_history = []
    normalized_career["case_history"] = copy.deepcopy(case_history)
    card["career"] = normalized_career

    portrait = raw.get("portrait")
    if portrait is None or portrait == "":
        card["portrait"] = None
    elif isinstance(portrait, str) and len(portrait) <= 500:
        card["portrait"] = portrait
    else:
        _err(errors, "portrait", "头像引用必须是长度不超过 500 的字符串")

    stance = raw.get("violence_stance")
    if stance is not None:
        card["violence_stance"] = _validate_text(
            stance, "violence_stance", errors, max_len=40, label="暴力倾向"
        )

    for key in unknown:
        card[key] = copy.deepcopy(raw[key])
    return card, errors, warnings


def unwrap_payload(payload: Any) -> tuple[Any, list[dict], list[str]]:
    """拆导入信封：优先版本化信封，裸卡按 v1 解析并告警。"""
    errors: list[dict] = []
    warnings: list[str] = []
    if not isinstance(payload, dict):
        _err(errors, "(root)", "文件内容必须是 JSON 对象")
        return None, errors, warnings
    if "format" in payload or "card" in payload:
        if payload.get("format") != CARD_FORMAT:
            _err(
                errors,
                "format",
                f"无法识别的格式标识 {payload.get('format')!r}（期望 {CARD_FORMAT}）",
            )
            return None, errors, warnings
        version = payload.get("format_version")
        if version != CARD_FORMAT_VERSION:
            _err(
                errors,
                "format_version",
                f"不支持的角色卡格式版本 {version!r}（当前支持 {CARD_FORMAT_VERSION}）",
            )
            return None, errors, warnings
        card = payload.get("card")
        if not isinstance(card, dict):
            _err(errors, "card", "信封中的 card 字段必须是对象")
            return None, errors, warnings
        return card, errors, warnings
    warnings.append("文件未声明格式版本，按 trpg-character-card v1 解析")
    return payload, errors, warnings


def export_payload(entry: CharacterLibraryEntry) -> dict:
    """导出信封：只包含角色资料，不含 owner/世界等任何身份信息。"""
    return {
        "format": CARD_FORMAT,
        "format_version": CARD_FORMAT_VERSION,
        "exported_at": utcnow().isoformat(),
        "card": copy.deepcopy(entry.card_json),
    }


# ---------------------------------------------------------------- 摘要


def entry_summary(entry: CharacterLibraryEntry) -> dict:
    """与 characters._character_summary 同构的摘要（供列表/选角复用）。"""
    card = entry.card_json if isinstance(entry.card_json, dict) else {}
    derived = card.get("derived") if isinstance(card.get("derived"), dict) else {}
    skills = card.get("skills") if isinstance(card.get("skills"), dict) else {}
    backstory = card.get("backstory") if isinstance(card.get("backstory"), dict) else {}
    career = card.get("career") if isinstance(card.get("career"), dict) else {}
    numeric_skills = [
        (key, value) for key, value in skills.items() if isinstance(value, int)
    ]
    top_skills = [
        {"id": key, "value": value}
        for key, value in sorted(numeric_skills, key=lambda item: -item[1])[:5]
    ]
    return {
        "ref": {"source": "library", "id": entry.id, "path": f"character_library#{entry.id}"},
        "id": entry.id,
        "name": card.get("name", "未命名调查员"),
        "occupation": card.get("occupation", ""),
        "age": card.get("age"),
        "era": card.get("era", ""),
        "source": "library",
        "source_label": "角色库",
        "hp": derived.get("HP", 0),
        "max_hp": derived.get("max_HP", 0),
        "san": derived.get("SAN", 0),
        "max_san": derived.get("max_SAN", 0),
        "reputation": career.get("reputation", 0) if isinstance(career.get("reputation"), int) else 0,
        "completed_modules": len(career.get("completed_modules", []))
        if isinstance(career.get("completed_modules"), list)
        else 0,
        "top_skills": top_skills,
        "attributes": copy.deepcopy(card.get("attributes", {})),
        "derived": copy.deepcopy(derived),
        # 编辑表单需要全量技能回填；列表展示只用 top_skills。
        "skills": copy.deepcopy(skills),
        "inventory": copy.deepcopy(card.get("inventory", [])),
        "credit_rating": card.get("credit_rating", 0),
        "backstory": copy.deepcopy(backstory),
        "description": backstory.get("description", ""),
        "created_at": entry.created_at.isoformat() if entry.created_at else "",
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else "",
    }


# ---------------------------------------------------------------- CRUD（按 owner 隔离）


def _get_owned(session, owner_id: str, entry_id: str) -> CharacterLibraryEntry:
    entry = session.get(CharacterLibraryEntry, entry_id)
    if entry is None or entry.owner_user_id != owner_id:
        raise CharacterLibraryError("not_found", "角色不存在或不属于当前账号", 404)
    return entry


def list_entries(database_url: str, owner_id: str) -> list[dict]:
    with session_scope(database_url) as session:
        rows = (
            session.query(CharacterLibraryEntry)
            .filter_by(owner_user_id=owner_id)
            .order_by(CharacterLibraryEntry.updated_at.desc())
            .all()
        )
        return [entry_summary(row) for row in rows]


def inspect_payload(database_url: str, owner_id: str, payload: Any) -> dict:
    """导入预览：只校验与提示，不落库。"""
    card_raw, errors, warnings = unwrap_payload(payload)
    if card_raw is None:
        return {"ok": False, "errors": errors, "warnings": warnings, "preview": None}
    card, card_errors, card_warnings = validate_card(card_raw)
    errors = errors + card_errors
    warnings = warnings + card_warnings
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings, "preview": None}
    duplicate = _name_exists(database_url, owner_id, card["name"])
    if duplicate:
        warnings.append(f"角色库已有同名角色「{card['name']}」，导入将作为新角色保存，不会覆盖")
    preview_entry = CharacterLibraryEntry(
        id="(preview)", owner_user_id=owner_id, name=card["name"], card_json=card
    )
    return {
        "ok": True,
        "errors": [],
        "warnings": warnings,
        "preview": entry_summary(preview_entry),
    }


def _name_exists(database_url: str, owner_id: str, name: str) -> bool:
    with session_scope(database_url) as session:
        return (
            session.query(CharacterLibraryEntry.id)
            .filter_by(owner_user_id=owner_id, name=name)
            .first()
            is not None
        )


def create_entry(database_url: str, owner_id: str, payload: Any) -> dict:
    """创建条目（导入与新建共用）。返回 {"entry": summary, "warnings": [...]}。"""
    card_raw, errors, warnings = unwrap_payload(payload)
    if card_raw is not None:
        card, card_errors, card_warnings = validate_card(card_raw)
        errors = errors + card_errors
        warnings = warnings + card_warnings
    else:
        card = {}
    if errors:
        raise CharacterLibraryError("invalid_card", "角色卡校验未通过", 400, errors)
    if _name_exists(database_url, owner_id, card["name"]):
        warnings.append(f"角色库已有同名角色「{card['name']}」，已作为新角色保存，未覆盖旧角色")
    entry = CharacterLibraryEntry(
        id=new_id("chlib"),
        owner_user_id=owner_id,
        name=card["name"],
        card_json=card,
    )
    with session_scope(database_url) as session:
        session.add(entry)
        session.flush()
        return {"entry": entry_summary(entry), "warnings": warnings}


def get_entry(database_url: str, owner_id: str, entry_id: str) -> dict:
    with session_scope(database_url) as session:
        entry = _get_owned(session, owner_id, entry_id)
        return {"entry": entry_summary(entry), "card": copy.deepcopy(entry.card_json)}


def update_entry(database_url: str, owner_id: str, entry_id: str, payload: Any) -> dict:
    """整体替换卡面（编辑保存）。服务端 id/归属不变，不影响任何已开局世界。"""
    card_raw, errors, warnings = unwrap_payload(payload)
    if card_raw is not None:
        card, card_errors, card_warnings = validate_card(card_raw)
        errors = errors + card_errors
        warnings = warnings + card_warnings
    else:
        card = {}
    if errors:
        raise CharacterLibraryError("invalid_card", "角色卡校验未通过", 400, errors)
    with session_scope(database_url) as session:
        entry = _get_owned(session, owner_id, entry_id)
        entry.name = card["name"]
        entry.card_json = card
        entry.updated_at = utcnow()
        session.flush()
        return {"entry": entry_summary(entry), "warnings": warnings}


def duplicate_entry(database_url: str, owner_id: str, entry_id: str) -> dict:
    with session_scope(database_url) as session:
        entry = _get_owned(session, owner_id, entry_id)
        card = copy.deepcopy(entry.card_json)
        card["name"] = f"{entry.name}（副本）"[:MAX_NAME_LEN]
        clone = CharacterLibraryEntry(
            id=new_id("chlib"),
            owner_user_id=owner_id,
            name=card["name"],
            card_json=card,
        )
        session.add(clone)
        session.flush()
        return {"entry": entry_summary(clone), "warnings": []}


def delete_entry(database_url: str, owner_id: str, entry_id: str) -> None:
    with session_scope(database_url) as session:
        entry = _get_owned(session, owner_id, entry_id)
        session.delete(entry)


def get_export_entry(database_url: str, owner_id: str, entry_id: str) -> dict:
    with session_scope(database_url) as session:
        entry = _get_owned(session, owner_id, entry_id)
        return export_payload(entry)


# ---------------------------------------------------------------- 开局解析（供 characters.resolve_character）


def resolve_library_card(entry_id: str, *, context: Any) -> dict | None:
    """按 ref 读取库角色卡，带归属校验。

    本地模式（无账号）只认 owner 为空串的条目；账号模式要求条目属于当前
    世界/房间的创建者——联机房间里其他成员的私有角色因此无法被解析。
    任何一步不满足都返回 None（上层按「无法读取角色」处理），不抛出细节。
    """
    entry_id = str(entry_id or "").strip()
    database_url = getattr(context, "database_url", None)
    world_id = getattr(context, "world_id", "")
    if not entry_id or not database_url:
        return None
    try:
        with session_scope(database_url) as session:
            entry = session.get(CharacterLibraryEntry, entry_id)
            if entry is None:
                return None
            if auth_required():
                world = session.get(World, world_id)
                owner = str(world.created_by or "") if world is not None else ""
                if not owner or entry.owner_user_id != owner:
                    return None
            elif entry.owner_user_id != "":
                return None
            return copy.deepcopy(entry.card_json)
    except Exception:
        # 表不存在（未迁移的旧库）等基础设施问题按「无法解析」处理，
        # 由开局路径的统一错误呈现，不在此处泄露内部异常。
        return None


def list_library_summaries(database_url: str | None, owner_id: str) -> list[dict]:
    """list_character_options 的库分组数据源；基础设施故障时静默降级为空组。"""
    if not database_url:
        return []
    try:
        return list_entries(database_url, owner_id)
    except Exception:
        return []
