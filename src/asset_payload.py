"""发言者/调查员头像与资产载荷的共享构建。

从 server.py 抽出的纯函数：输入 world_state/RuntimeContext，输出前端可渲染的
{asset_data_uri, asset_url, alt} 载荷。发言者名称以权威 NPC 表为准；头像从
模组资产目录解析，与人物手册/线索的解锁进度相互独立，无素材时静默省略。
"""

from __future__ import annotations

import base64
import json
import mimetypes
from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:
    from .runtime import RuntimeContext


def asset_payload(filename: str, context: RuntimeContext) -> dict:
    """生成前端可直接渲染的资产信息。"""
    if not filename:
        return {}
    asset_path = (context.assets_dir / filename).resolve()
    allowed = context.assets_dir.resolve()
    if not asset_path.is_relative_to(allowed) or not asset_path.is_file():
        return {}

    mime, _ = mimetypes.guess_type(str(asset_path))
    data = base64.b64encode(asset_path.read_bytes()).decode("ascii")
    return {
        "asset_data_uri": f"data:{mime or 'image/png'};base64,{data}",
        "asset_url": f"/api/assets/{quote(context.module_name)}/{quote(filename)}",
    }


def npc_lookup(world: dict, npc_id: str, *, catalog: dict | None = None) -> dict | None:
    """按 id 查 NPC 的展示信息：{id, name, avatar_file}；名称以权威 NPC 表为准。"""
    if not npc_id:
        return None
    name = None
    for npc in world.get("npcs", []) or []:
        if str(npc.get("id") or "") == npc_id:
            name = str(npc.get("name") or "") or None
            break
    asset_entry = ((world.get("asset_map") or {}).get("npcs") or {}).get(npc_id) or {}
    if not asset_entry and catalog:
        asset_entry = ((catalog.get("asset_map") or {}).get("npcs") or {}).get(npc_id) or {}
    if name is None:
        name = str(asset_entry.get("label") or "") or None
    if name is None:
        return None
    return {
        "id": npc_id,
        "name": name,
        "avatar_file": str(asset_entry.get("file") or "") or None,
    }


def npc_id_known(world: dict, npc_id: str) -> bool:
    """⟦npc:id⟧ 发言标签校验：id 必须存在于权威 NPC 表或 asset_map。"""
    if not npc_id:
        return False
    for npc in world.get("npcs", []) or []:
        if str(npc.get("id") or "") == npc_id:
            return True
    return npc_id in ((world.get("asset_map") or {}).get("npcs") or {})


def speaker_payload(npc_id: str, engine) -> dict | None:
    """构建出场身份；它不读取或修改 seen_handouts，不受人物线索解锁约束。"""
    if engine is None or not npc_id:
        return None
    try:
        world = engine.context.world_store.load()
        catalog = {}
        initial = engine.context.initial_state_file
        if initial.is_file():
            catalog = json.loads(initial.read_text(encoding="utf-8"))
    except Exception:
        return None
    profile = npc_lookup(world, npc_id, catalog=catalog)
    if not profile:
        return None
    payload = {"type": "npc", "id": profile["id"], "name": profile["name"]}
    avatar_file = profile.get("avatar_file")
    if avatar_file:
        avatar = asset_payload(avatar_file, engine.context)
        if avatar:
            avatar["alt"] = f"{profile['name']}肖像"
            payload["avatar"] = avatar
    return payload


class SpeakerPayloadResolver:
    """Session-local immutable presentation cache for NPC identities."""

    def __init__(self, engine):
        self.engine = engine
        self.cache: dict[str, dict] = {}

    def __call__(self, npc_id: str) -> dict | None:
        if npc_id not in self.cache:
            payload = speaker_payload(npc_id, self.engine)
            if payload:
                self.cache[npc_id] = payload
        return self.cache.get(npc_id)

    def clear(self) -> None:
        self.cache.clear()


def enrich_narrative_segments(segments: list, resolve_speaker) -> list[dict]:
    enriched = []
    for index, segment in enumerate(segments):
        item = {"kind": segment.get("kind"), "text": segment.get("text", "")}
        npc_id = segment.get("npc_id")
        speaker = resolve_speaker(npc_id) if npc_id else None
        item["event_id"] = f"narrative_{index}"
        if npc_id:
            item["npc_id"] = npc_id
        if speaker:
            item["speaker"] = speaker
        elif item["kind"] == "narration":
            item["speaker"] = {"type": "keeper", "name": "守秘人"}
        enriched.append(item)
    return enriched


def enrich_pc_for_frontend(pc_data: dict, context: RuntimeContext) -> dict:
    """角色卡 portrait 字段 → 头像载荷（相对模组 assets 解析）；无素材时静默省略。"""
    pc = dict(pc_data or {})
    portrait = str(pc.get("portrait") or "")
    if portrait and "avatar" not in pc:
        avatar = asset_payload(portrait, context)
        if avatar:
            avatar["alt"] = f"{pc.get('name') or '调查员'}肖像"
            pc["avatar"] = avatar
    return pc
