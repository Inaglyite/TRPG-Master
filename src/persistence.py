"""Skill 加载 + 存档/读档"""

import json

from .config import SKILLS_DIR, SKILL_LOAD_ORDER, SAVEFILE


def load_system_prompt() -> str:
    parts = []
    for name in SKILL_LOAD_ORDER:
        path = SKILLS_DIR / name
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                parts.append(content)
    return "\n\n---\n\n".join(parts)


def save_game(messages: list) -> bool:
    try:
        serializable = []
        for m in messages:
            entry = {"role": m["role"], "content": m.get("content", "")}
            if "tool_calls" in m:
                entry["tool_calls"] = m["tool_calls"]
            if "tool_call_id" in m:
                entry["tool_call_id"] = m["tool_call_id"]
            serializable.append(entry)
        data = {"version": 2, "messages": serializable, "message_count": len(serializable)}
        SAVEFILE.parent.mkdir(parents=True, exist_ok=True)
        SAVEFILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        print(f"[存档失败] {e}")
        return False


def load_game() -> list | None:
    if not SAVEFILE.exists():
        return None
    try:
        data = json.loads(SAVEFILE.read_text(encoding="utf-8"))
        return data.get("messages", [])
    except Exception as e:
        print(f"[读档失败] {e}")
        return None
