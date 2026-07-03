"""长期调查员与角色选择服务。

角色卡沿用现有 `tools/character.py` 的 JSON 格式；本模块只负责把角色卡
列给界面、复制进当前模组 `world_state.pc`，以及在案件结束后写入长期履历。
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config as cfg

CHARACTER_SOURCES = {"profile", "default", "custom", "module"}


def ensure_character_dirs() -> None:
    cfg.DEFAULT_CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    cfg.CUSTOM_CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    cfg.PROFILES_DIR.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now().isoformat()


def _slug(value: str) -> str:
    text = re.sub(r"\s+", "_", value.strip())
    text = "".join(ch for ch in text if ch.isalnum() or ch in "_-")
    return text or "investigator"


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _module_characters_dir(module_name: str | None = None) -> Path:
    module = module_name or cfg.MODULE_NAME
    return cfg.PROJECT_ROOT / "mod" / module / "characters"


def _safe_file_name(name: str) -> str:
    return Path(name).name


def _source_path_label(path: Path) -> str:
    try:
        return str(path.relative_to(cfg.PROJECT_ROOT))
    except ValueError:
        return str(path)


def _profile_template() -> dict:
    return {
        "version": 1,
        "active_character_id": None,
        "characters": {},
        "updated_at": _now(),
    }


def load_profile() -> dict:
    ensure_character_dirs()
    if not cfg.PLAYER_PROFILE_FILE.exists():
        return _profile_template()
    profile = _read_json(cfg.PLAYER_PROFILE_FILE, _profile_template())
    profile.setdefault("version", 1)
    profile.setdefault("active_character_id", None)
    profile.setdefault("characters", {})
    return profile


def save_profile(profile: dict) -> None:
    profile["updated_at"] = _now()
    _write_json(cfg.PLAYER_PROFILE_FILE, profile)


def _character_id(source: str, char: dict, *, module_name: str | None = None,
                  file_name: str | None = None) -> str:
    explicit = char.get("id")
    if explicit:
        return str(explicit)
    parts = [source]
    if module_name:
        parts.append(module_name)
    parts.append(Path(file_name or char.get("name", "investigator")).stem)
    return ":".join(_slug(p) for p in parts if p)


def _career_template() -> dict:
    return {
        "reputation": 0,
        "titles": [],
        "known_contacts": [],
        "completed_modules": [],
        "case_history": [],
    }


def _normalize_career(career: dict | None) -> dict:
    merged = _career_template()
    if isinstance(career, dict):
        for key, value in career.items():
            merged[key] = copy.deepcopy(value)
    for key in ("titles", "known_contacts", "completed_modules", "case_history"):
        if not isinstance(merged.get(key), list):
            merged[key] = []
    if not isinstance(merged.get("reputation"), int):
        merged["reputation"] = 0
    return merged


def character_to_pc(char: dict, ref: dict | None = None,
                    existing_pc: dict | None = None) -> dict:
    """把角色卡复制成 world_state.pc 兼容结构。"""
    pc = copy.deepcopy(existing_pc or {})
    derived = char.get("derived", {})
    career = _normalize_career(char.get("career"))
    source = (ref or {}).get("source", char.get("origin", "unknown"))
    module_name = (ref or {}).get("module")
    file_name = (ref or {}).get("file")
    char_id = _character_id(source, char, module_name=module_name, file_name=file_name)

    pc.update({
        "name": char.get("name", pc.get("name", "")),
        "occupation": char.get("occupation", pc.get("occupation", "")),
        "hp": derived.get("HP", char.get("hp", pc.get("hp", 10))),
        "max_hp": derived.get("max_HP", char.get("max_hp", pc.get("max_hp", 10))),
        "san": derived.get("SAN", char.get("san", pc.get("san", 50))),
        "max_san": derived.get("max_SAN", char.get("max_san", pc.get("max_san", 50))),
        "attributes": copy.deepcopy(char.get("attributes", pc.get("attributes", {}))),
        "skills": copy.deepcopy(char.get("skills", pc.get("skills", {}))),
        "inventory": copy.deepcopy(char.get("inventory", pc.get("inventory", []))),
        "credit_rating": char.get("credit_rating", pc.get("credit_rating", 0)),
        "backstory": copy.deepcopy(char.get("backstory", pc.get("backstory", {}))),
        "psychological_profile": copy.deepcopy(char.get("psychological_profile", pc.get(
            "psychological_profile",
            {"traits": [], "key_relationships": [], "phobias": [], "manias": []},
        ))),
        "career": career,
        "character_id": char_id,
        "character_source": source,
        "character_source_path": (ref or {}).get("path", ""),
    })

    pc["character_session"] = {
        "character_id": char_id,
        "source": source,
        "source_path": pc.get("character_source_path", ""),
        "module": cfg.MODULE_NAME,
        "started_at": _now(),
        "starting_hp": pc.get("hp", 0),
        "starting_san": pc.get("san", 0),
    }
    return pc


def _profile_entry_to_character(entry: dict) -> dict:
    char = copy.deepcopy(entry.get("character", {}))
    if not char:
        status = entry.get("last_known_status", {})
        char = {
            "id": entry.get("id", ""),
            "name": entry.get("name", ""),
            "occupation": entry.get("occupation", ""),
            "attributes": entry.get("attributes", {}),
            "skills": entry.get("skills", {}),
            "inventory": entry.get("inventory", []),
            "derived": {
                "HP": status.get("hp", entry.get("hp", 10)),
                "max_HP": status.get("max_hp", entry.get("max_hp", 10)),
                "SAN": status.get("san", entry.get("san", 50)),
                "max_SAN": status.get("max_san", entry.get("max_san", 50)),
            },
        }
    char["career"] = _normalize_career(entry.get("career") or char.get("career"))
    return char


def resolve_character(ref: dict | None, module_name: str | None = None) -> tuple[dict | None, dict | None]:
    """根据前端传来的 ref 读取角色卡，返回 (character, normalized_ref)。"""
    if not ref:
        return None, None
    ensure_character_dirs()
    source = ref.get("source", "")
    if source not in CHARACTER_SOURCES:
        return None, None

    if source == "profile":
        profile = load_profile()
        char_id = ref.get("id", "")
        entry = profile.get("characters", {}).get(char_id)
        if not entry:
            return None, None
        normalized = {
            "source": "profile",
            "id": char_id,
            "path": f"profiles/player_profile.json#characters.{char_id}",
        }
        return _profile_entry_to_character(entry), normalized

    file_name = _safe_file_name(ref.get("file", ""))
    if not file_name:
        return None, None
    if source == "default":
        path = cfg.DEFAULT_CHARACTERS_DIR / file_name
        mod = None
    elif source == "custom":
        path = cfg.CUSTOM_CHARACTERS_DIR / file_name
        mod = None
    else:
        mod = ref.get("module") or module_name or cfg.MODULE_NAME
        path = _module_characters_dir(mod) / file_name

    if not path.exists():
        return None, None
    char = _read_json(path, None)
    if not isinstance(char, dict):
        return None, None
    normalized = {
        "source": source,
        "module": mod,
        "file": file_name,
        "path": _source_path_label(path),
    }
    return char, normalized


def _character_summary(char: dict, ref: dict, *, source_label: str) -> dict:
    derived = char.get("derived", {})
    career = _normalize_career(char.get("career"))
    skills = char.get("skills", {})
    numeric_skills = [(key, value) for key, value in skills.items() if isinstance(value, int)]
    top_skills = [
        {"id": key, "value": value}
        for key, value in sorted(numeric_skills, key=lambda item: -item[1])[:5]
    ]
    return {
        "ref": ref,
        "id": _character_id(ref.get("source", ""), char,
                            module_name=ref.get("module"), file_name=ref.get("file")),
        "name": char.get("name", "未命名调查员"),
        "occupation": char.get("occupation", ""),
        "age": char.get("age"),
        "source": ref.get("source", ""),
        "source_label": source_label,
        "hp": derived.get("HP", char.get("hp", 0)),
        "max_hp": derived.get("max_HP", char.get("max_hp", 0)),
        "san": derived.get("SAN", char.get("san", 0)),
        "max_san": derived.get("max_SAN", char.get("max_san", 0)),
        "reputation": career.get("reputation", 0),
        "completed_modules": len(career.get("completed_modules", [])),
        "top_skills": top_skills,
        "description": (char.get("backstory") or {}).get("description", ""),
    }


def _list_character_files(directory: Path, source: str, source_label: str,
                          *, module_name: str | None = None) -> list[dict]:
    result = []
    if not directory.exists():
        return result
    for path in sorted(directory.glob("*.json")):
        char = _read_json(path, None)
        if not isinstance(char, dict):
            continue
        ref = {
            "source": source,
            "file": path.name,
            "path": _source_path_label(path),
        }
        if module_name:
            ref["module"] = module_name
        result.append(_character_summary(char, ref, source_label=source_label))
    return result


def list_character_options(module_name: str | None = None) -> dict:
    ensure_character_dirs()
    module = module_name or cfg.MODULE_NAME
    profile = load_profile()
    experienced = []
    for char_id, entry in sorted(profile.get("characters", {}).items()):
        char = _profile_entry_to_character(entry)
        ref = {"source": "profile", "id": char_id}
        experienced.append(_character_summary(char, ref, source_label="长期角色"))

    groups = [
        {
            "id": "profile",
            "title": "长期角色",
            "characters": experienced,
        },
        {
            "id": "default",
            "title": "默认调查员",
            "characters": _list_character_files(cfg.DEFAULT_CHARACTERS_DIR, "default", "默认调查员"),
        },
        {
            "id": "module",
            "title": f"{module} 特色调查员",
            "characters": _list_character_files(
                _module_characters_dir(module), "module", "模组特色", module_name=module
            ),
        },
        {
            "id": "custom",
            "title": "自定义角色",
            "characters": _list_character_files(cfg.CUSTOM_CHARACTERS_DIR, "custom", "自定义角色"),
        },
    ]
    return {"module": module, "groups": groups}


def apply_character_to_state(ref: dict | None, state: dict,
                             module_name: str | None = None) -> dict | None:
    char, normalized_ref = resolve_character(ref, module_name)
    if char is None or normalized_ref is None:
        return None
    state["pc"] = character_to_pc(char, normalized_ref, state.get("pc", {}))
    return {
        "id": state["pc"].get("character_id", ""),
        "name": state["pc"].get("name", ""),
        "occupation": state["pc"].get("occupation", ""),
        "source": normalized_ref.get("source", ""),
        "path": normalized_ref.get("path", ""),
    }


def default_character_ref(module_name: str | None = None) -> dict | None:
    options = list_character_options(module_name)
    for group_id in ("profile", "default", "module", "custom"):
        group = next((g for g in options["groups"] if g["id"] == group_id), None)
        if group and group["characters"]:
            return group["characters"][0]["ref"]
    return None


def _profile_record_from_pc(pc: dict, char: dict | None = None,
                            ref: dict | None = None) -> dict:
    char_copy = copy.deepcopy(char) if char else character_from_pc(pc)
    char_copy["career"] = _normalize_career(pc.get("career") or char_copy.get("career"))
    return {
        "id": pc.get("character_id", _character_id((ref or {}).get("source", "profile"), char_copy)),
        "name": pc.get("name", ""),
        "occupation": pc.get("occupation", ""),
        "source": pc.get("character_source", (ref or {}).get("source", "")),
        "source_path": pc.get("character_source_path", (ref or {}).get("path", "")),
        "character": char_copy,
        "career": copy.deepcopy(char_copy["career"]),
        "last_known_status": {
            "hp": pc.get("hp", 0),
            "max_hp": pc.get("max_hp", 0),
            "san": pc.get("san", 0),
            "max_san": pc.get("max_san", 0),
        },
        "updated_at": _now(),
    }


def character_from_pc(pc: dict) -> dict:
    return {
        "id": pc.get("character_id", ""),
        "name": pc.get("name", ""),
        "occupation": pc.get("occupation", ""),
        "attributes": copy.deepcopy(pc.get("attributes", {})),
        "skills": copy.deepcopy(pc.get("skills", {})),
        "inventory": copy.deepcopy(pc.get("inventory", [])),
        "credit_rating": pc.get("credit_rating", 0),
        "backstory": copy.deepcopy(pc.get("backstory", {})),
        "psychological_profile": copy.deepcopy(pc.get("psychological_profile", {
            "traits": [], "key_relationships": [], "phobias": [], "manias": [],
        })),
        "career": _normalize_career(pc.get("career")),
        "derived": {
            "HP": pc.get("hp", 0),
            "max_HP": pc.get("max_hp", 0),
            "SAN": pc.get("san", 0),
            "max_SAN": pc.get("max_san", 0),
        },
    }


def _append_unique(items: list, values: list) -> list:
    existing = set(items)
    for value in values:
        if value and value not in existing:
            items.append(value)
            existing.add(value)
    return items


def _revealed_contacts(world_state: dict) -> list[str]:
    contacts = []
    for npc in world_state.get("npcs", []):
        revealed = npc.get("revealed", {})
        if revealed.get("level", 0) > 0:
            contacts.append(npc.get("name", ""))
    return [name for name in contacts if name]


def _reputation_delta(ending_type: str) -> int:
    return {
        "good": 3,
        "secret": 2,
        "neutral": 1,
        "bad": 0,
    }.get(ending_type, 1)


def settle_case(world_state: dict, *, ending_type: str, title: str,
                summary: str, module_name: str | None = None) -> dict:
    """把当前案件的粗粒度结果写入长期 profile。"""
    pc = world_state.get("pc", {})
    if not pc:
        return {"ok": False, "error": "当前世界状态没有 pc"}
    module = module_name or cfg.MODULE_NAME
    char_id = pc.get("character_id") or _character_id("profile", pc, module_name=module)
    pc["character_id"] = char_id

    profile = load_profile()
    characters = profile.setdefault("characters", {})
    existing = characters.get(char_id, {})
    career = _normalize_career(existing.get("career") or pc.get("career"))
    session = pc.get("character_session", {})
    start_san = session.get("starting_san", pc.get("max_san", pc.get("san", 0)))
    start_hp = session.get("starting_hp", pc.get("max_hp", pc.get("hp", 0)))
    rep_delta = _reputation_delta(ending_type)

    case_entry = {
        "module": module,
        "ending_type": ending_type,
        "title": title,
        "summary": summary,
        "san_delta": pc.get("san", 0) - start_san,
        "hp_delta": pc.get("hp", 0) - start_hp,
        "reputation_delta": rep_delta,
        "completed_at": _now(),
    }

    history = career.setdefault("case_history", [])
    if not any(item.get("module") == module and item.get("title") == title for item in history):
        history.append(case_entry)
    if module not in career.setdefault("completed_modules", []):
        career["completed_modules"].append(module)
    career["reputation"] = int(career.get("reputation", 0)) + rep_delta
    _append_unique(career.setdefault("known_contacts", []), _revealed_contacts(world_state))

    pc["career"] = career
    char = character_from_pc(pc)
    record = _profile_record_from_pc(pc, char, {
        "source": pc.get("character_source", "profile"),
        "path": pc.get("character_source_path", ""),
    })
    record["career"] = career
    record["last_case"] = case_entry
    characters[char_id] = record
    profile["active_character_id"] = char_id
    save_profile(profile)
    return {"ok": True, "character_id": char_id, "case": case_entry, "career": career}
