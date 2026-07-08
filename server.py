#!/usr/bin/env python3
# ruff: noqa: E402
"""TRPG Agent WebSocket 服务器 —— GameEngine + FastAPI"""

import json
import sys
import os
import asyncio
import base64
import copy
import mimetypes
import runpy
from urllib.parse import quote
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _prefer_utf8_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


_prefer_utf8_stdio()

# PyInstaller 打包后，工具层仍会通过 `sys.executable tools/*.py ...`
# 启动确定性脚本。此处把自身当作轻量 Python launcher 使用。
if len(sys.argv) > 1 and sys.argv[1].endswith(".py"):
    script = Path(sys.argv[1])
    if not script.is_absolute():
        # 工具脚本以相对路径传入（如 tools/dice.py）。打包后数据在 _internal/
        # 下，而 TRPG_PROJECT_ROOT 指向 exe 目录，二者常不一致——依次在候选
        # 根下找实际存在的脚本，避免 runpy FileNotFoundError（即"骰子服务不可用"）。
        candidates: list[Path] = []
        env_root = os.environ.get("TRPG_PROJECT_ROOT")
        if env_root:
            candidates.append(Path(env_root))
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).resolve().parent
            candidates.append(base / "_internal")
            candidates.append(base)
        candidates.append(Path.cwd())
        resolved = None
        for root in candidates:
            cand = root / script
            if cand.exists():
                resolved = cand
                break
        script = resolved or (candidates[0] / script)
    sys.argv = sys.argv[1:]
    runpy.run_path(str(script), run_name="__main__")
    sys.exit(0)

# ---- 从 .env.json 加载配置到环境变量（与 start.py 行为一致）----
# 必须在 import src.* 之前完成，因为 src/config.py 在导入时就读取 os.environ。
_ROOT_FOR_ENV = Path(os.environ.get("TRPG_PROJECT_ROOT", Path(__file__).resolve().parent))
_ENV_FILE = _ROOT_FOR_ENV / ".env.json"
if _ENV_FILE.exists():
    try:
        _cfg = json.loads(_ENV_FILE.read_text(encoding="utf-8"))
        os_environ = os.environ
        _mapping = {
            "api_key": "OPENAI_API_KEY",
            "base_url": "OPENAI_BASE_URL",
            "flash_model": "TRPG_FLASH_MODEL",
            "pro_model": "TRPG_PRO_MODEL",
            "glm_api_key": "GLM_API_KEY",
            "glm_base_url": "GLM_BASE_URL",
            "glm_model": "GLM_MODEL",
        }
        for cfg_key, env_key in _mapping.items():
            val = _cfg.get(cfg_key)
            if val and env_key not in os_environ:
                os_environ[env_key] = val
    except Exception as e:
        print(f"⚠️  读取 .env.json 失败: {e}", file=sys.stderr)

from src.engine import GameEngine, EngineCallbacks
from src.config import PROJECT_ROOT, AUTO_SAVE_SLOT
import src.config as cfg
from src.persistence import delete_save
from src.characters import list_character_options

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="TRPG Agent API")


def _load_theme() -> dict:
    """读取当前模组的 theme.json"""
    if cfg.THEME_FILE.exists():
        return json.loads(cfg.THEME_FILE.read_text(encoding="utf-8"))
    return {"title": "TRPG Agent", "colors": {}, "fonts": {}}


def _list_mods() -> list:
    """列出所有可用模组"""
    mods = []
    mods_dir = PROJECT_ROOT / "mod"
    if not mods_dir.exists():
        return mods
    for d in sorted(mods_dir.iterdir()):
        if d.is_dir() and (d / "module.md").exists():
            theme = {}
            tf = d / "theme.json"
            if tf.exists():
                theme = json.loads(tf.read_text(encoding="utf-8"))
            mods.append({"id": d.name, "title": theme.get("title", d.name),
                         "description": theme.get("description", "")})
    return mods


def _asset_payload(filename: str) -> dict:
    """生成前端可直接渲染的资产信息。"""
    if not filename:
        return {}
    asset_path = (cfg.MODULE_DIR / "assets" / filename).resolve()
    allowed = (cfg.MODULE_DIR / "assets").resolve()
    if not str(asset_path).startswith(str(allowed)) or not asset_path.exists():
        return {}

    mime, _ = mimetypes.guess_type(str(asset_path))
    data = base64.b64encode(asset_path.read_bytes()).decode("ascii")
    return {
        "asset_data_uri": f"data:{mime or 'image/png'};base64,{data}",
        "asset_url": f"/api/assets/{quote(cfg.MODULE_NAME)}/{quote(filename)}",
    }


def _collect_known_npc_ids(world_state: dict) -> list[str]:
    """收集已发放过人物展示材料的 NPC ID，不把仅被提及的人物提前入册。"""
    known: list[str] = []

    def add(npc_id: str):
        if npc_id and npc_id not in known:
            known.append(npc_id)

    seen = world_state.get("seen_handouts", {}) if isinstance(world_state, dict) else {}
    seen_npcs = seen.get("npcs", []) if isinstance(seen, dict) else []
    if isinstance(seen_npcs, list):
        for npc_id in seen_npcs:
            if isinstance(npc_id, str):
                add(npc_id)

    return known


def _append_npc_profiles(enriched: dict, world_state: dict):
    """把已知 NPC 的公开档案追加到人物线索，不发送 secret/技能等守秘信息。"""
    if not isinstance(enriched, dict) or not isinstance(world_state, dict):
        return

    npc_assets = world_state.get("asset_map", {}).get("npcs", {})
    if not isinstance(npc_assets, dict):
        return

    npc_by_id = {
        npc.get("id"): npc
        for npc in world_state.get("npcs", [])
        if isinstance(npc, dict) and npc.get("id")
    }
    profiles = []
    for npc_id in _collect_known_npc_ids(world_state):
        npc = npc_by_id.get(npc_id)
        asset = npc_assets.get(npc_id)
        if not npc or not isinstance(asset, dict) or not asset.get("file"):
            continue

        tags = npc.get("visible_tags", [])
        public_tags = "、".join(str(tag) for tag in tags[:4]) if isinstance(tags, list) else ""
        name = npc.get("name") or asset.get("label") or npc_id
        text = f"{name}：{public_tags}" if public_tags else str(name)
        profiles.append({
            "id": f"profile_{npc_id}",
            "text": text,
            "type": "profile",
            "tier": 0,
            "source": "npc_profile",
            "related_npcs": [npc_id],
            "related_scenes": [],
            "discovered_at": None,
            "asset": {
                "id": npc_id,
                "file": asset.get("file"),
                "label": asset.get("label", name),
            },
        })

    if not profiles:
        return

    existing = enriched.get("npc", [])
    if not isinstance(existing, list):
        existing = []
    existing_ids = {item.get("id") for item in existing if isinstance(item, dict)}
    new_profiles = [item for item in profiles if item["id"] not in existing_ids]
    enriched["npc"] = new_profiles + existing


def _enrich_clues_for_frontend(clues: dict, world_state: dict | None = None) -> dict:
    """为线索面板补齐图片 data URI，避免 Electron file:// 下无法直接取 HTTP 图。"""
    enriched = copy.deepcopy(clues) if isinstance(clues, dict) else clues
    if not isinstance(enriched, dict):
        return enriched
    if isinstance(world_state, dict):
        _append_npc_profiles(enriched, world_state)
    for items in enriched.values():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            asset = item.get("asset")
            if isinstance(asset, dict) and asset.get("file"):
                asset.update(_asset_payload(asset["file"]))
    return enriched


# ---------------------------------------------------------------------------
# WebSocket 会话 —— 把引擎回调桥接到 WebSocket
# ---------------------------------------------------------------------------

async def run_ws_session(ws: WebSocket, engine: GameEngine):
    """在 WebSocket 连接上下文中运行引擎。

    线程模型：GameEngine.handle_action 是同步阻塞的，通过 run_in_executor
    跑在线程池线程里。引擎从该线程同步调用下面这些回调，因此回调必须是
    普通同步函数；它们用 run_coroutine_threadsafe 把 ws.send_json 安全地
    调度到主事件循环（FastAPI/uvicorn 所在的 loop）。
    """
    import threading

    loop = asyncio.get_running_loop()
    # suggest_check 的跨线程握手：工作线程发事件并阻塞，主循环收到回复后置位
    suggest_box: dict = {"event": threading.Event(), "result": False}
    suggest_lock = threading.Lock()
    suggest_active = [False]  # 当前是否有待回复的 suggest
    # 回合锁：保证同一时间只有一个 handle_action 在跑，避免并发修改 messages
    turn_lock = threading.Lock()

    def run_turn(coro_fn, *args):
        """在 executor 里跑一个回合，用 turn_lock 串行化。fire-and-forget。"""
        def _wrapped():
            with turn_lock:
                try:
                    coro_fn(*args)
                except Exception as e:
                    import traceback
                    print(f"[ws] 回合异常: {e}", file=sys.stderr)
                    traceback.print_exc(file=sys.stderr)
        loop.run_in_executor(None, _wrapped)

    def emit(payload: dict):
        """线程安全地把一条消息发到主循环。"""
        try:
            asyncio.run_coroutine_threadsafe(
                ws.send_json(payload), loop
            )
        except Exception as e:
            print(f"[ws] emit 失败: {e}", file=sys.stderr)

    # ---- 同步回调（被引擎在工作线程里同步调用）----
    def on_narrative(text: str):
        emit({"type": "narrative_chunk", "text": text})

    def on_tension(text: str, cat: str):
        emit({"type": "tension", "text": text, "category": cat})

    def on_dice(summary: str, roll_data: dict | None = None):
        emit({"type": "dice_result", "summary": summary, "roll_data": roll_data or {}})

    def on_glm_summary(text: str):
        emit({"type": "glm_summary", "text": text})

    def on_suggest(info: dict) -> bool:
        """向客户端发起检定确认，阻塞当前工作线程等待回复。

        用 threading.Event 做跨线程握手，避免 asyncio.Future 跨线程的复杂性：
        工作线程在这里阻塞等 event，主循环的 suggest_reply 处理器置位 event。
        """
        ev = threading.Event()
        with suggest_lock:
            suggest_box["event"] = ev
            suggest_box["result"] = False
            suggest_active[0] = True
        emit({"type": "suggest_check", **info})
        # 阻塞工作线程，直到 suggest_reply 处理器 ev.set()
        ev.wait(timeout=120)
        with suggest_lock:
            suggest_active[0] = False
            return suggest_box["result"]

    def on_done():
        emit({"type": "done"})

    def on_game_over(ending_type: str, title: str, summary: str):
        emit({"type": "game_over", "ending_type": ending_type, "title": title, "summary": summary})

    def on_handout(info: dict):
        emit({"type": "handout", "file": info.get("file", ""),
              "label": info.get("label", ""),
              "asset_data_uri": info.get("asset_data_uri", ""),   # base64 data URI（electron 兼容）
              "asset_url": info.get("asset_url", ""),             # HTTP URL（web 兼容，fallback）
              "entity_type": info.get("entity_type", ""), "entity_id": info.get("entity_id", "")})

    def on_error(msg: str):
        emit({"type": "error", "message": msg})

    # 通知前端模组列表 + 角色列表 + 存档列表 + 当前主题
    await ws.send_json({"type": "module_list", "modules": _list_mods(), "active": cfg.MODULE_NAME})
    await ws.send_json({"type": "character_list", **list_character_options(cfg.MODULE_NAME)})

    # 发送当前模组主题（electron 用 file:// 加载，fetch('/api/theme') 不可用，
    # 故主题也走 WS 下发）
    await ws.send_json({"type": "theme", "theme": _load_theme()})

    saves = engine.list_saves()
    await ws.send_json({"type": "save_list", "saves": saves})

    engine.cb = EngineCallbacks(
        on_narrative=on_narrative,
        on_tension=on_tension,
        on_dice=on_dice,
        on_glm_summary=on_glm_summary,
        on_suggest=on_suggest,
        on_done=on_done,
        on_game_over=on_game_over,
        on_handout=on_handout,
        on_error=on_error,
    )

    # 消息循环
    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type", "")

            if msg_type == "ping":
                await ws.send_json({"type": "pong"})

            elif msg_type == "switch_module":
                # 切换活跃模组（开场前在下拉框选择）
                name = data.get("module", cfg.MODULE_NAME)
                target = PROJECT_ROOT / "mod" / name
                if not target.exists() or not (target / "module.md").exists():
                    await ws.send_json({"type": "error", "message": f"模组'{name}'不存在"})
                else:
                    cfg.set_active_module(name)
                    # 切换 system prompt，但不重置 world_state；真正的新游戏重置在 start 时发生。
                    engine.prepare_session()
                    # 下发新主题 + 新存档列表
                    await ws.send_json({"type": "theme", "theme": _load_theme()})
                    await ws.send_json({"type": "module_list", "modules": _list_mods(), "active": name})
                    await ws.send_json({"type": "character_list", **list_character_options(name)})
                    await ws.send_json({"type": "save_list", "saves": engine.list_saves()})

            elif msg_type == "start":
                # 开始新游戏：触发开场 GM 回合（用 reset() 里预置的开场 prompt）
                engine.reset(data.get("character_ref"))
                await ws.send_json({"type": "gm_turn_start"})
                run_turn(engine.handle_action, None)

            elif msg_type == "continue":
                # 继续游戏：读档 + 恢复快照 + 续写 prompt
                slot = data.get("slot_id")  # 可选：指定槽位
                count = engine.load(slot)
                if count is None:
                    await ws.send_json({"type": "error", "message": "未找到存档，请开始新游戏。"})
                else:
                    engine.messages.append({
                        "role": "user",
                        "content": (
                            "（游戏继续。你刚刚读取了之前的存档，世界状态已恢复。"
                            "请基于当前对话历史中的场景、NPC 状态和已发现线索，"
                            "用 1-2 句话简述玩家当前位置和情况，然后提供行动选项。"
                            "不要从头开场，不要重新介绍世界观。）"
                        )
                    })
                    await ws.send_json({"type": "gm_turn_start"})
                    run_turn(engine.handle_action, None)

            elif msg_type == "action":
                # 同上：不 await，避免 on_suggest 阻塞时主循环死锁
                run_turn(engine.handle_action, data.get("content", ""))

            elif msg_type == "suggest_reply":
                with suggest_lock:
                    if suggest_active[0]:
                        suggest_box["result"] = data.get("confirmed", False)
                        suggest_box["event"].set()

            elif msg_type == "save":
                is_manual = data.get("manual", False)
                slot_id = None if is_manual else AUTO_SAVE_SLOT
                sid = engine.save(slot_id)
                await ws.send_json({"type": "saved", "ok": True, "slot_id": sid})

            elif msg_type == "save_list":
                saves = engine.list_saves()
                await ws.send_json({"type": "save_list", "saves": saves})

            elif msg_type == "character_list":
                await ws.send_json({"type": "character_list", **list_character_options(cfg.MODULE_NAME)})

            elif msg_type == "save_load":
                slot_id = data.get("slot_id", "")
                cnt = engine.load(slot_id)
                if cnt is not None:
                    engine.messages.append({
                        "role": "user",
                        "content": (
                            "（游戏继续。你刚刚读取了之前的存档，世界状态已恢复。"
                            "请基于当前对话历史中的场景、NPC 状态和已发现线索，"
                            "用 1-2 句话简述玩家当前位置和情况，然后提供行动选项。"
                            "不要从头开场，不要重新介绍世界观。）"
                        )
                    })
                    await ws.send_json({"type": "loaded", "ok": True, "slot_id": slot_id, "count": cnt})
                    await ws.send_json({"type": "gm_turn_start"})
                    run_turn(engine.handle_action, None)
                else:
                    await ws.send_json({"type": "error", "message": "未找到存档。"})

            elif msg_type == "save_delete":
                slot_id = data.get("slot_id", "")
                if slot_id == AUTO_SAVE_SLOT:
                    await ws.send_json({"type": "error", "message": "自动存档不可手动删除。"})
                else:
                    delete_save(slot_id)
                    await ws.send_json({"type": "save_deleted", "slot_id": slot_id})

            elif msg_type == "save_create":
                existing = engine.list_saves()
                used = set()
                for s in existing:
                    if s["id"].startswith("slot_") and s["id"] != AUTO_SAVE_SLOT:
                        try:
                            used.add(int(s["id"].split("_")[1]))
                        except (ValueError, IndexError):
                            pass
                n = 1
                while n in used:
                    n += 1
                slot_id = f"slot_{n:03d}"
                sid = engine.save(slot_id)
                await ws.send_json({"type": "saved", "ok": True, "slot_id": sid})

            elif msg_type == "save_rename":
                slot_id = data.get("slot_id", "")
                label = data.get("label", "")
                from src.persistence import rename_save
                ok = rename_save(slot_id, label)
                await ws.send_json({"type": "save_renamed", "slot_id": slot_id, "label": label, "ok": ok})

            elif msg_type == "settle_case":
                result = engine.settle_case(
                    data.get("ending_type", "neutral"),
                    data.get("title", "故事结束"),
                    data.get("summary", ""),
                )
                await ws.send_json({"type": "case_settled", **result})
                await ws.send_json({"type": "character_list", **list_character_options(cfg.MODULE_NAME)})
                await ws.send_json({"type": "state"})

            elif msg_type == "quit":
                engine.save("slot_000")  # 退出时存档
                await ws.send_json({"type": "quit_ok"})
                break  # 退出消息循环

            elif msg_type == "load":
                count = engine.load()
                await ws.send_json({"type": "loaded", "ok": count is not None, "count": count or 0})

            elif msg_type == "state":
                # 直接读取当前模组的 world_state.json（不走 state_manager.py 子进程，
                # 否则子进程的 TRPG_MODULE 环境变量不会随运行时模组切换更新）
                try:
                    _ws = json.loads(cfg.STATE_FILE.read_text(encoding="utf-8"))
                    pc_data = _ws.get("pc", {})
                    clues_data = _enrich_clues_for_frontend(_ws.get("clues_found", {}), _ws)
                except Exception:
                    pc_data, clues_data = {}, {}
                await ws.send_json({
                    "type": "state_data",
                    "data": json.dumps(pc_data, ensure_ascii=False),
                    "clues": json.dumps(clues_data, ensure_ascii=False)
                })

    except WebSocketDisconnect:
        pass
    except RuntimeError:
        # 连接已断开（客户端关闭窗口等），收尾即可
        pass


@app.get("/api/theme")
async def get_theme():
    """返回当前模组的主题配置"""
    if cfg.THEME_FILE.exists():
        return json.loads(cfg.THEME_FILE.read_text(encoding="utf-8"))
    return {"title": "TRPG Agent", "colors": {}, "fonts": {}}


@app.get("/api/health")
async def health():
    """桌面壳用于等待内置后端启动完成。"""
    return {"ok": True, "module": cfg.MODULE_NAME}


@app.get("/api/modules")
async def list_modules():
    """列出所有可用模组"""
    mods = []
    mods_dir = PROJECT_ROOT / "mod"
    for d in sorted(mods_dir.iterdir()):
        if d.is_dir() and (d / "module.md").exists():
            theme = {}
            theme_file = d / "theme.json"
            if theme_file.exists():
                theme = json.loads(theme_file.read_text(encoding="utf-8"))
            mods.append({
                "id": d.name,
                "title": theme.get("title", d.name),
                "description": theme.get("description", "")
            })
    return {"modules": mods, "active": cfg.MODULE_NAME}


@app.get("/api/characters")
async def list_characters():
    """列出可用于当前模组的新游戏调查员。"""
    return list_character_options(cfg.MODULE_NAME)


@app.post("/api/modules/switch")
async def switch_module(data: dict):
    """切换活跃模组"""
    name = data.get("module", cfg.MODULE_NAME)
    target = PROJECT_ROOT / "mod" / name
    if not target.exists() or not (target / "module.md").exists():
        return {"ok": False, "error": f"模组'{name}'不存在"}
    cfg.set_active_module(name)
    return {"ok": True, "module": name}


@app.websocket("/ws")
async def game_ws(ws: WebSocket):
    await ws.accept()
    try:
        engine = GameEngine()
        engine.prepare_session()
    except Exception as e:
        # 配置/初始化失败时，把错误发回客户端而不是静默断开
        await ws.send_json({"type": "error", "message": f"游戏引擎初始化失败：{e}"})
        await ws.close()
        return
    await run_ws_session(ws, engine)


# ---- 资产文件 ----
@app.get("/api/assets/{module_name}/{filename}")
async def serve_asset(module_name: str, filename: str):
    """服务模组资产图片，带路径遍历保护。"""
    asset_path = (PROJECT_ROOT / "mod" / module_name / "assets" / filename).resolve()
    allowed = (PROJECT_ROOT / "mod" / module_name / "assets").resolve()
    if not str(asset_path).startswith(str(allowed)):
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if not asset_path.exists():
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "not found"}, status_code=404)
    mime, _ = mimetypes.guess_type(str(asset_path))
    return FileResponse(asset_path, media_type=mime or "image/png")

# ---- 静态文件 ----
FRONTEND_DIR = PROJECT_ROOT / "frontend" / "dist"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:
    @app.get("/")
    async def root():
        return HTMLResponse("<h2>前端未构建。运行: cd frontend && npm run build</h2>")


if __name__ == "__main__":
    import uvicorn
    print("🎲 TRPG Agent WebSocket 服务器")
    print("   ws://localhost:8765/ws    前端: http://localhost:8765/")
    uvicorn.run(app, host="0.0.0.0", port=8765)
