"""Skill 加载 + 存档/读档（文件夹式多槽位 + 世界状态快照）

存档结构:
  saves/slot_000/              ← 自动存档（退出时）
    messages.json              ← LLM 对话历史
    snapshot.json              ← 世界状态快照（读档时恢复，防止线索污染）
    meta.json                  ← { created_at, scene, hp, san, clue_count }
  saves/slot_001/              ← 手动存档
  saves/slot_002/
"""

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from .config import SKILLS_DIR, SKILL_LOAD_ORDER, AUTO_SAVE_SLOT
from . import config as cfg


# ---- Skill 加载 ----

def _module_prompt_content(content: str) -> str:
    """Drop module default PC templates from the runtime prompt.

    The active investigator is copied into world_state.json at game start. Keeping a
    module.md default PC block in the system prompt can make the model call the
    player by the template name after a different character is selected.
    """
    default_pc = ""
    pc_block = re.search(r"\n# PC[^\n]*\n(.*?)(?=\n# )", content, flags=re.DOTALL)
    if pc_block:
        name_match = re.search(r"(?m)^\s*name:\s*(.+?)\s*$", pc_block.group(1))
        if name_match:
            default_pc = name_match.group(1).strip().strip("\"'")

    content = re.sub(
        r"\n# PC[^\n]*\n.*?(?=\n# )",
        "\n# PC - 调查员\n\n（运行时调查员以 world_state.json 的 pc 字段为准；模组模板 PC 不作为玩家身份。）\n",
        content,
        count=1,
        flags=re.DOTALL,
    )
    if default_pc:
        content = content.replace(default_pc, "所选调查员")
        content = content.replace("私家侦探所选调查员", "所选调查员")
    return content


def load_system_prompt() -> str:
    parts = []
    # 核心 skill
    for name in SKILL_LOAD_ORDER:
        path = SKILLS_DIR / name
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                parts.append(content)
    # 当前模组的剧情设定（module.md）——让 GM 知道本模组的故事背景
    module_md = cfg.MODULE_DIR / "module.md"
    if module_md.exists():
        content = _module_prompt_content(module_md.read_text(encoding="utf-8")).strip()
        if content:
            parts.append(content)
    # 仅加载【当前模组】的专属 skill，避免多模组内容串扰
    mod_skills_dir = cfg.MODULE_DIR / "skills"
    if mod_skills_dir.exists():
        for mod_skill in sorted(mod_skills_dir.glob("*.skill")):
            content = mod_skill.read_text(encoding="utf-8").strip()
            if content:
                parts.append(content)
    return "\n\n---\n\n".join(parts)


# ---- 存档 ----

def _slot_dir(slot_id: str) -> Path:
    return cfg.SAVES_DIR / slot_id


def _next_slot() -> str:
    """返回下一个可用的手动存档槽位 ID"""
    existing = sorted(
        int(d.name.split("_")[1])
        for d in cfg.SAVES_DIR.iterdir()
        if d.is_dir() and d.name.startswith("slot_") and d.name != AUTO_SAVE_SLOT
    )
    n = 1
    while n in existing:
        n += 1
    return f"slot_{n:03d}"


def _slot_meta(messages: list, world_state: dict) -> dict:
    """从消息和世界状态生成存档元数据"""
    pc = world_state.get("pc", {})
    scene = world_state.get("current_scene", {})
    clues = world_state.get("clues_found", {})
    if isinstance(clues, dict):
        clue_count = sum(len(v) for v in clues.values())
    else:
        clue_count = len(clues) if isinstance(clues, list) else 0

    return {
        "created_at": datetime.now().isoformat(),
        "scene_id": scene.get("id", ""),
        "scene_name": scene.get("name", ""),
        "character_id": pc.get("character_id", ""),
        "character_name": pc.get("name", ""),
        "character_source": pc.get("character_source", ""),
        "character_source_path": pc.get("character_source_path", ""),
        "hp": f"{pc.get('hp', 0)}/{pc.get('max_hp', 0)}",
        "san": f"{pc.get('san', 0)}/{pc.get('max_san', 0)}",
        "clue_count": clue_count,
        "message_count": len(messages),
    }


def save_game(messages: list, slot_id: str | None = None) -> str:
    """保存游戏到指定槽位（默认自动存档）。返回槽位 ID。"""
    if slot_id is None:
        slot_id = AUTO_SAVE_SLOT

    slot_dir = _slot_dir(slot_id)
    slot_dir.mkdir(parents=True, exist_ok=True)

    # 序列化消息（去掉不可序列化的字段）
    serializable = []
    for m in messages:
        entry = {"role": m["role"], "content": m.get("content", "")}
        if "tool_calls" in m:
            entry["tool_calls"] = m["tool_calls"]
        if "tool_call_id" in m:
            entry["tool_call_id"] = m["tool_call_id"]
        serializable.append(entry)

    # 读取当前世界状态作为快照
    try:
        world_state = json.loads(cfg.STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        world_state = {}

    # 写入文件
    (slot_dir / "messages.json").write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (slot_dir / "snapshot.json").write_text(
        json.dumps(world_state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (slot_dir / "meta.json").write_text(
        json.dumps(_slot_meta(serializable, world_state), ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    return slot_id


def load_game(slot_id: str | None = None) -> tuple[list, dict] | tuple[None, None]:
    """读取存档。返回 (messages, world_snapshot) 或 (None, None)。
    如果 slot_id 为 None，加载最新存档（按修改时间）。
    """
    if slot_id:
        slot_dir = _slot_dir(slot_id)
        if not slot_dir.exists():
            return None, None
        return _load_slot(slot_dir)

    # 找最新存档
    slots = list_saves()
    if not slots:
        return None, None

    latest = slots[0]  # 已按时间倒序
    return _load_slot(_slot_dir(latest["id"]))


def _load_slot(slot_dir: Path) -> tuple[list, dict] | tuple[None, None]:
    """从槽位目录加载"""
    msg_file = slot_dir / "messages.json"
    snap_file = slot_dir / "snapshot.json"

    if not msg_file.exists():
        return None, None

    messages = json.loads(msg_file.read_text(encoding="utf-8"))
    snapshot = json.loads(snap_file.read_text(encoding="utf-8")) if snap_file.exists() else {}

    return messages, snapshot


def _migrate_snapshot(snapshot: dict) -> dict:
    """将旧版快照迁移到最新数据结构（向下兼容）。"""
    # v2: private_memory
    if "private_memory" not in snapshot:
        snapshot["private_memory"] = {
            "goals_and_plans": "",
            "hidden_facts": {},
            "inference_notes": "（从旧存档迁移，请守秘人根据对话历史手动补充）"
        }

    # v2: NPC revealed 字段
    for npc in snapshot.get("npcs", []):
        if "revealed" not in npc:
            npc["revealed"] = {"level": 0, "entries": []}

    # v2: PC psychological_profile
    pc = snapshot.get("pc", {})
    if "psychological_profile" not in pc:
        pc["psychological_profile"] = {
            "traits": [], "key_relationships": [],
            "phobias": [], "manias": []
        }

    return snapshot


def restore_snapshot(snapshot: dict) -> bool:
    """将世界状态快照恢复到 world_state.json（自动迁移旧版数据结构）。返回是否成功。"""
    if not snapshot:
        return False
    try:
        snapshot = _migrate_snapshot(snapshot)
        cfg.STATE_FILE.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return True
    except Exception:
        return False


def list_saves() -> list[dict]:
    """列出所有存档的元数据，按时间倒序"""
    result = []
    if not cfg.SAVES_DIR.exists():
        return result

    for d in sorted(cfg.SAVES_DIR.iterdir(), reverse=True):
        if not d.is_dir() or not d.name.startswith("slot_"):
            continue
        meta_file = d / "meta.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                meta["id"] = d.name
                result.append(meta)
            except json.JSONDecodeError:
                pass
        else:
            result.append({"id": d.name, "created_at": "", "scene_name": "（旧格式存档）"})

    result.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return result


def has_save() -> bool:
    """检查是否有任何存档"""
    return len(list_saves()) > 0


def delete_save(slot_id: str):
    """删除指定存档"""
    slot_dir = _slot_dir(slot_id)
    if slot_dir.exists():
        shutil.rmtree(slot_dir)


def rename_save(slot_id: str, label: str) -> bool:
    """重命名存档——更新 meta.json 中的 label 字段"""
    slot_dir = _slot_dir(slot_id)
    meta_file = slot_dir / "meta.json"
    if not meta_file.exists():
        return False
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        meta["label"] = label
        meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False
