"""Persist departures independently of dialogue formatting and context summaries."""

from __future__ import annotations


def departed_speakers(world: dict, scene_id: str | None = None) -> dict[str, dict]:
    scene_id = scene_id or str((world.get("current_scene") or {}).get("id") or "")
    return {
        str(key): dict(value)
        for key, value in ((world.get("scene_speaker_presence") or {}).get(scene_id) or {}).items()
        if isinstance(value, dict) and value.get("present") is False
    }


def departure_ids_this_turn(engine) -> set[str]:
    return {
        str(event["target"])
        for event in (getattr(engine, "_adjudicated_outcome", None) or {}).get("events", [])
        if isinstance(event, dict) and event.get("type") == "speaker_departure" and event.get("target")
    }


def speaker_display_name(world: dict, speaker_id: str) -> str:
    """玩家可见称呼：guest 注册表 display_name → NPC 目录 name → 资源表 label。"""
    entry = (world.get("guest_speakers") or {}).get(speaker_id)
    if isinstance(entry, dict) and str(entry.get("display_name") or "").strip():
        return str(entry["display_name"])
    for npc in world.get("npcs") or []:
        if isinstance(npc, dict) and str(npc.get("id") or "") == speaker_id:
            return str(npc.get("name") or npc.get("display_name") or "")
    asset = ((world.get("asset_map") or {}).get("npcs") or {}).get(speaker_id)
    if isinstance(asset, dict):
        return str(asset.get("label") or asset.get("name") or "")
    return ""


def departed_speaker_briefs(world: dict, scene_id: str | None = None) -> list[dict]:
    """当前场景已离场人物的修复/提示用摘要（name 解析不到时回退为 id）。"""
    return [
        {
            "id": speaker_id,
            "name": speaker_display_name(world, speaker_id) or speaker_id,
            "reason": str(info.get("reason") or ""),
            "turn_id": str(info.get("turn_id") or ""),
        }
        for speaker_id, info in sorted(departed_speakers(world, scene_id).items())
    ]


def return_speaker(world: dict, speaker_id: str, *, reason: str, turn_id: str = "", evidence: str = "") -> dict:
    """登记一次有证据的回归：只有本场景存在离场记录的人物才能回到现场。

    回归必须是新的权威事件（裁决效果带玩家输入证据），不能是遭遇缓存或
    修复工具的顺带结果；没有离场事实的"回归"视为强行复活，直接拒绝。
    """
    scene = world.get("current_scene") or {}
    scene_id = str(scene.get("id") or "")
    entries = world.setdefault("scene_speaker_presence", {}).setdefault(scene_id, {})
    record = entries.get(speaker_id)
    if not (isinstance(record, dict) and record.get("present") is False):
        raise ValueError("没有离场事实的回归是伪造的")
    entries[speaker_id] = {
        "present": True, "reason": reason, "turn_id": turn_id, "evidence": evidence,
    }
    if speaker_id not in scene.get("npcs_present", []):
        scene.setdefault("npcs_present", []).append(speaker_id)
    catalog_scene = (world.get("scene_catalog") or {}).get(scene_id)
    if isinstance(catalog_scene, dict) and speaker_id not in catalog_scene.get("npcs_present", []):
        catalog_scene.setdefault("npcs_present", []).append(speaker_id)
    for npc in world.get("npcs") or []:
        if npc.get("id") == speaker_id:
            npc["current_location"] = scene_id
    return {"type": "speaker_return", "target": speaker_id, "scene_id": scene_id, "reason": reason}


def depart_speaker(world: dict, speaker_id: str, *, reason: str, turn_id: str = "") -> dict:
    scene = world.get("current_scene") or {}
    scene_id = str(scene.get("id") or "")
    entries = world.setdefault("scene_speaker_presence", {}).setdefault(scene_id, {})
    entries[speaker_id] = {"present": False, "reason": reason, "turn_id": turn_id}
    scene["npcs_present"] = [key for key in scene.get("npcs_present", []) if key != speaker_id]
    catalog_scene = (world.get("scene_catalog") or {}).get(scene_id)
    if isinstance(catalog_scene, dict):
        catalog_scene["npcs_present"] = [
            key for key in catalog_scene.get("npcs_present", []) if key != speaker_id
        ]
    for npc in world.get("npcs") or []:
        if npc.get("id") == speaker_id and npc.get("current_location") == scene_id:
            npc["current_location"] = ""
    return {"type": "speaker_departure", "target": speaker_id, "scene_id": scene_id, "reason": reason}
