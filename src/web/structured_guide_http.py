"""Bounded, on-demand author documents for authorized human keepers.

These are installed module references, not world facts or Agent-selected memory.
Never accept a client path, execute document contents, or send them to players.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

from src.auth.service import auth_required, local_request_trusted, request_user
from src.storage.database import World, session_scope
from src.structured.gateway import StructuredGateway

MAX_DOCUMENT_BYTES = 256 * 1024
MAX_SOURCE_BYTES = 1024 * 1024
MAX_DOCUMENTS = 200


def _text(root: Path, path: Path, limit: int) -> str:
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError("文档路径不可用")
    with resolved.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("文档超出读取上限，未截断显示")
    return data.decode("utf-8-sig")


def read_keeper_guide(deps, world_id: str, user_id: str | None) -> dict | None:
    db_url = deps.database_url()
    gateway = StructuredGateway(db_url)
    if not gateway.is_structured(world_id):
        return None
    principal = gateway.connection_principal(world_id, user_id)
    if principal is None or principal.kind != "keeper":
        return None
    with session_scope(db_url) as session:
        world = session.get(World, world_id)
        if world is None:
            return None
        module_name = world.module_name
    try:
        record = deps.module_registry.resolve(module_name)
        root = record.path.resolve()
        candidates = [("module", "模组正文", root / "module.md")]
        scenes = root / "scenes"
        if scenes.resolve().is_relative_to(root) and scenes.is_dir():
            candidates.extend(
                (f"scene:{path.stem}", path.stem, path) for path in sorted(scenes.glob("*.md"))
            )
        documents: list[dict] = []
        warnings: list[str] = []
        used_bytes = 0

        def add(key: str, title: str, text: str) -> None:
            nonlocal used_bytes
            size = len(text.encode("utf-8"))
            if len(documents) >= MAX_DOCUMENTS or used_bytes + size > MAX_SOURCE_BYTES:
                raise ValueError("资料总量超出上限，该条未载入")
            used_bytes += size
            if text.strip():
                documents.append({"id": key, "title": title[:160], "text": text})

        for key, title, path in candidates:
            if not path.exists():
                continue
            try:
                add(key, title, _text(root, path, MAX_DOCUMENT_BYTES))
            except (OSError, UnicodeError, ValueError):
                warnings.append(f"{title[:160]}：无法完整读取，未显示截断内容。")
        lore = root / "lorebook.json"
        if lore.exists():
            try:
                data = json.loads(_text(root, lore, MAX_SOURCE_BYTES))
                entries = data.get("data", {}).get("entries", [])
                if not isinstance(entries, list):
                    raise ValueError("无效知识库")
                for index, entry in enumerate(entries):
                    if not isinstance(entry, dict) or not isinstance(entry.get("content"), str):
                        continue
                    title = str(entry.get("name") or entry.get("id") or f"条目 {index + 1}")
                    if entry.get("enabled") is False:
                        title += "（作者停用）"
                    add(f"lore:{index}", f"知识库 · {title}", entry["content"])
            except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
                warnings.append("知识库：部分或全部条目无法完整读取，请检查模组文件。")
        return {
            "module_title": record.title,
            "source_version": record.version,
            "documents": documents,
            "warnings": warnings,
        }
    except (OSError, ValueError, FileNotFoundError):
        return None


def register_structured_guide_routes(router, deps) -> None:
    @router.get("/api/worlds/{world_id}/keeper-guide")
    async def keeper_guide(world_id: str, request: Request):
        user = request_user(request, deps.database_url())
        if user is not None:
            user_id = user.id
        elif auth_required():
            return JSONResponse({"detail": "未登录或会话已过期"}, status_code=401)
        elif local_request_trusted(request.headers):
            user_id = None
        else:
            return JSONResponse({"detail": "本地请求不受信任"}, status_code=403)
        payload = await asyncio.to_thread(read_keeper_guide, deps, world_id, user_id)
        return JSONResponse(
            payload or {"detail": "资料不可用，或你没有主持权限"},
            status_code=200 if payload else 404,
            headers={"Cache-Control": "private, no-store", "Vary": "Cookie"},
        )
