#!/usr/bin/env python3
"""TRPG Agent WebSocket 服务器 —— GameEngine + FastAPI"""

import json
import sys
import asyncio
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# ---- 从 .env.json 加载配置到环境变量（与 start.py 行为一致）----
# 必须在 import src.* 之前完成，因为 src/config.py 在导入时就读取 os.environ。
_ENV_FILE = Path(__file__).resolve().parent / ".env.json"
if _ENV_FILE.exists():
    try:
        _cfg = json.loads(_ENV_FILE.read_text(encoding="utf-8"))
        os_environ = __import__("os").environ
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
from src.config import PROJECT_ROOT, AUTO_SAVE_SLOT, MODULE_DIR, THEME_FILE, MODULE_NAME
from src.persistence import delete_save

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="TRPG Agent API")


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
                    print(f"[ws] 回合异常: {e}", file=sys.stderr)
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

    def on_dice(summary: str):
        emit({"type": "dice_result", "summary": summary})

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

    def on_error(msg: str):
        emit({"type": "error", "message": msg})

    # 通知前端存档列表
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

            elif msg_type == "start":
                # 开始新游戏：触发开场 GM 回合（用 reset() 里预置的开场 prompt）
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

            elif msg_type == "quit":
                engine.save("slot_000")  # 退出时存档
                await ws.send_json({"type": "quit_ok"})
                break  # 退出消息循环

            elif msg_type == "load":
                count = engine.load()
                await ws.send_json({"type": "loaded", "ok": count is not None, "count": count or 0})

            elif msg_type == "state":
                # 同时获取 PC 数据和线索（结构化）
                pc = subprocess.run(
                    ["python3", "tools/state_manager.py", "get", "pc"],
                    capture_output=True, text=True, cwd=PROJECT_ROOT
                )
                clues = subprocess.run(
                    ["python3", "tools/state_manager.py", "get", "clues_found"],
                    capture_output=True, text=True, cwd=PROJECT_ROOT
                )
                await ws.send_json({
                    "type": "state_data",
                    "data": pc.stdout,
                    "clues": clues.stdout
                })

    except WebSocketDisconnect:
        pass
    except RuntimeError:
        # 连接已断开（客户端关闭窗口等），收尾即可
        pass


@app.get("/api/theme")
async def get_theme():
    """返回当前模组的主题配置"""
    if THEME_FILE.exists():
        return json.loads(THEME_FILE.read_text(encoding="utf-8"))
    return {"title": "TRPG Agent", "colors": {}, "fonts": {}}


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
    return {"modules": mods, "active": MODULE_NAME}


@app.websocket("/ws")
async def game_ws(ws: WebSocket):
    await ws.accept()
    try:
        engine = GameEngine()
        engine.reset()
    except Exception as e:
        # 配置/初始化失败时，把错误发回客户端而不是静默断开
        await ws.send_json({"type": "error", "message": f"游戏引擎初始化失败：{e}"})
        await ws.close()
        return
    await run_ws_session(ws, engine)


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
    print(f"   ws://localhost:8765/ws    前端: http://localhost:8765/")
    uvicorn.run(app, host="0.0.0.0", port=8765)
