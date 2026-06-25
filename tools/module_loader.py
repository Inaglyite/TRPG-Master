#!/usr/bin/env python3
"""Markdown 模组导入器 —— 解析 .md 文件生成 world_state.json

格式：用 # 一级标题分区（PC/NPC/场景/线索/标志/结局），
     用 ## 二级标题定义具体条目，```yaml 代码块存放结构化数据。
"""

import json
import re
import sys
from pathlib import Path
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MOD_DIR = PROJECT_ROOT / "mod"


def _extract_yaml_blocks(text: str) -> list[dict]:
    """从文本中提取所有 ```yaml ... ``` 块"""
    blocks = []
    for m in re.finditer(r'```yaml\n(.*?)\n```', text, re.DOTALL):
        try:
            data = yaml.safe_load(m.group(1))
            if data is not None:
                blocks.append(data)
        except yaml.YAMLError:
            pass
    return blocks


def _find_section(content: str, start_marker: str) -> str:
    """提取 # 标题之后到下一个 # 标题之前的所有内容"""
    # 先找到标题位置
    start_match = re.search(rf'^{start_marker}.*$', content, re.MULTILINE)
    if not start_match:
        return ""
    start_pos = start_match.end()

    # 找到下一个 # 标题（中英文均可）
    next_match = re.search(r'^# .*$', content[start_pos:], re.MULTILINE)
    if next_match:
        end_pos = start_pos + next_match.start()
    else:
        end_pos = len(content)

    return content[start_pos:end_pos].strip()


def parse_module(md_path: str) -> dict:
    """解析模组 Markdown 文件"""
    text = Path(md_path).read_text(encoding="utf-8")

    state = {
        "module": "",
        "current_scene": {},
        "pc": {"name": "", "occupation": "", "hp": 11, "max_hp": 11, "san": 65,
               "max_san": 65, "attributes": {}, "skills": {}, "inventory": [], "conditions": []},
        "npcs": [],
        "clues_found": {"investigation": [], "event": [], "task": [], "npc": []},
        "flags": {},
        "scene_cache": {},
        "module_rules": {},
        "private_memory": {
            "goals_and_plans": "",
            "hidden_facts": {},
            "inference_notes": "游戏刚开始。所有 NPC 秘密均未揭示。"
        }
    }

    # ── YAML frontmatter ──
    fm_match = re.search(r'^---\n(.*?)\n---', text, re.DOTALL)
    if fm_match:
        try:
            fm = yaml.safe_load(fm_match.group(1))
            if isinstance(fm, dict):
                state["module"] = fm.get("module", "")
        except yaml.YAMLError:
            pass

    # ── PC 区 ──
    pc_section = _find_section(text, r'# PC')
    pc_blocks = _extract_yaml_blocks(pc_section)
    if pc_blocks:
        data = pc_blocks[0]
        state["pc"]["name"] = data.get("name", "")
        state["pc"]["occupation"] = data.get("occupation", "")
        state["pc"]["attributes"] = data.get("attributes", {})
        state["pc"]["skills"] = data.get("skills", {})
        state["pc"]["inventory"] = data.get("inventory", [])
        attrs = state["pc"]["attributes"]
        state["pc"]["max_hp"] = (attrs.get("SIZ", 60) + attrs.get("CON", 50)) // 10
        state["pc"]["hp"] = state["pc"]["max_hp"]
        state["pc"]["max_san"] = attrs.get("POW", 65)
        state["pc"]["san"] = state["pc"]["max_san"]

    # ── NPC 区 ──
    npc_section = _find_section(text, r'# NPC')
    if not npc_section:
        npc_section = _find_section(text, r'# NPCs')
    # 逐个 ## 提取
    for m in re.finditer(r'##\s+(\w+)\s*\n(.*?)(?=\n##|\n# |\Z)', npc_section, re.DOTALL):
        npc_id = m.group(1)
        npc_content = m.group(2)
        yaml_blocks = _extract_yaml_blocks(npc_content)
        if yaml_blocks:
            data = yaml_blocks[0]
            data["id"] = npc_id
            # 初始化 revealed 追踪字段
            if "revealed" not in data:
                data["revealed"] = {"level": 0, "entries": []}
            state["npcs"].append(data)
            # 构建 private_memory 中的 hidden_facts
            secret = data.get("secret", "")
            if secret:
                state["private_memory"]["hidden_facts"][npc_id] = (
                    secret[:150] + "..." if len(secret) > 150 else secret
                )

    # ── 场景区 ──
    scene_section = _find_section(text, r'# 场景')
    if not scene_section:
        scene_section = _find_section(text, r'# Scene')
    first_scene = None
    for m in re.finditer(r'##\s+(\w+)\s*\n(.*?)(?=\n##|\n# |\Z)', scene_section, re.DOTALL):
        scene_id = m.group(1)
        scene_content = m.group(2)
        yaml_blocks = _extract_yaml_blocks(scene_content)
        if yaml_blocks:
            data = yaml_blocks[0]
            desc = data.get("description", "")
            state["scene_cache"][scene_id] = desc
            if first_scene is None:
                first_scene = {
                    "id": scene_id,
                    "name": data.get("name", scene_id),
                    "description": desc,
                    "exits": data.get("exits", []),
                    "npcs_present": data.get("npcs_present", [])
                }

    if first_scene:
        state["current_scene"] = first_scene

    # ── 线索区 ──
    clue_section = _find_section(text, r'# 线索') or _find_section(text, r'# Clue')
    for yaml_block in _extract_yaml_blocks(clue_section):
        if isinstance(yaml_block, list):
            for item in yaml_block:
                if isinstance(item, dict) and "text" in item:
                    cat = item.get("category", "investigation")
                    if cat in state["clues_found"]:
                        state["clues_found"][cat].append({"text": item["text"]})
        elif isinstance(yaml_block, dict) and "text" in yaml_block:
            cat = yaml_block.get("category", "investigation")
            if cat in state["clues_found"]:
                state["clues_found"][cat].append({"text": yaml_block["text"]})

    # ── 标志区 ──
    flag_section = _find_section(text, r'# 标志')
    if not flag_section:
        flag_section = _find_section(text, r'# Flag')
    flag_blocks = _extract_yaml_blocks(flag_section)
    if flag_blocks:
        state["flags"] = flag_blocks[0]

    # ── 规则区 ──
    rules_section = _find_section(text, r'# 规则') or _find_section(text, r'# Rules')
    if rules_section.strip():
        rules_data = {}
        for m in re.finditer(r'##\s+(\w+)\s*\n(.*?)(?=\n##|\Z)', rules_section, re.DOTALL):
            key = m.group(1)
            sub = m.group(2)
            yaml_blocks = _extract_yaml_blocks(sub)
            if yaml_blocks:
                if key == "monsters":
                    rules_data["monsters"] = yaml_blocks[0] if isinstance(yaml_blocks[0], list) else yaml_blocks
                elif key in ("san_triggers", "items", "spells", "custom_mechanics"):
                    rules_data[key] = yaml_blocks[0] if isinstance(yaml_blocks[0], list) else yaml_blocks
        state["module_rules"] = rules_data

    # ── 结局区 ──
    ending_section = _find_section(text, r'# 结局') or _find_section(text, r'# Ending')
    if ending_section.strip():
        state["endings"] = []
        for m in re.finditer(r'##\s+(.+?)\n+(.*?)(?=\n##|\Z)', ending_section, re.DOTALL):
            title = m.group(1).strip()
            desc = m.group(2).strip()
            if desc:
                state["endings"].append({"title": title, "description": desc})

    return state


def export_module(md_path: str, output_path: str | None = None):
    state = parse_module(md_path)
    if output_path is None:
        output_path = Path(md_path).parent / "world_state.json"
    Path(output_path).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    clues = sum(len(v) for v in state["clues_found"].values())
    print(f"✅ 模组已导出: {output_path}")
    print(f"   PC: {state['pc']['name']} ({state['pc']['occupation']})")
    print(f"   NPC: {len(state['npcs'])} 个")
    print(f"   场景: {len(state['scene_cache'])} 个")
    print(f"   线索: {clues} 条")
    print(f"   结局: {len(state.get('endings', []))} 种")
    return state


def main():
    if len(sys.argv) < 2:
        print("用法: python module_loader.py <md_file> [output.json]")
        sys.exit(1)
    export_module(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)


if __name__ == "__main__":
    main()
