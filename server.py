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
import tempfile
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
            "narrative_model": "TRPG_NARRATIVE_MODEL",
            "judgement_model": "TRPG_JUDGEMENT_MODEL",
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
from src.config import (
    AUTO_SAVE_SLOT,
    DEFAULT_MODULE_NAME,
    JUDGEMENT_MODEL,
    MODEL_FLASH,
    MODEL_PRO,
    NARRATIVE_MODEL,
    PROJECT_ROOT,
    RUNTIME_ROOT,
)
from src.model_settings import ModelSettings, persist_model_settings
from src.persistence import delete_save
from src.characters import list_character_options
from src.module_compiler import compile_payload
from src.module_format import manifest_json_schema, module_json_schema
from src.lorebook import lorebook_json_schema
from src.handouts import resolve_handout_asset
from src.module_registry import (
    MAX_PACKAGE_BYTES,
    ModulePackageError,
    ModuleRegistry,
    inspect_package,
)
from src.runtime import RuntimeContext
from src.world_store import StaleRevisionError

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="TRPG Agent API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["null"],
    allow_origin_regex=r"https?://(?:127\.0\.0\.1|localhost)(?::\d+)?",
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Module-Filename"],
)
MODULE_REGISTRY = ModuleRegistry(PROJECT_ROOT, RUNTIME_ROOT)
_active_context = RuntimeContext.local(DEFAULT_MODULE_NAME)
_active_model_settings = ModelSettings.validated(
    NARRATIVE_MODEL, JUDGEMENT_MODEL
)


def _set_active_context(context: RuntimeContext) -> None:
    global _active_context
    _active_context = context


def _model_settings_payload(settings: ModelSettings | None = None) -> dict:
    settings = settings or _active_model_settings
    return {
        "type": "model_settings",
        **settings.to_payload(MODEL_FLASH, MODEL_PRO),
    }


def _load_theme(context: RuntimeContext | None = None) -> dict:
    """读取当前模组的 theme.json"""
    context = context or _active_context
    if context.theme_file.exists():
        return json.loads(context.theme_file.read_text(encoding="utf-8"))
    return {"title": "TRPG Agent", "colors": {}, "fonts": {}}


def _list_mods() -> list:
    """列出所有可用模组"""
    return [record.to_dict() for record in MODULE_REGISTRY.list_modules()]


def _asset_payload(filename: str, context: RuntimeContext | None = None) -> dict:
    """生成前端可直接渲染的资产信息。"""
    context = context or _active_context
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
        _, asset = resolve_handout_asset(world_state, "npc", npc_id)
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


def _enrich_clues_for_frontend(
    clues: dict,
    world_state: dict | None = None,
    context: RuntimeContext | None = None,
) -> dict:
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
                asset.update(_asset_payload(asset["file"], context))
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
    global _active_model_settings

    import threading

    loop = asyncio.get_running_loop()
    # suggest_check 的跨线程握手：工作线程发事件并阻塞，主循环收到回复后置位
    suggest_box: dict = {"event": threading.Event(), "result": False}
    suggest_lock = threading.Lock()
    suggest_active = [False]  # 当前是否有待回复的 suggest
    # 通用多选决定握手。战斗状态机用它确认闪避、反击、寻找掩体等选择。
    decision_box: dict = {"event": threading.Event(), "result": None, "id": None}
    decision_lock = threading.Lock()
    decision_active = [False]
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
                    emit({
                        "type": "error",
                        "message": "本轮处理失败，请重新发送刚才的行动。",
                    })
                    emit({"type": "done"})
        loop.run_in_executor(None, _wrapped)

    def emit(payload: dict):
        """线程安全地把一条消息发到主循环。"""
        try:
            asyncio.run_coroutine_threadsafe(
                ws.send_json(payload), loop
            )
        except Exception as e:
            print(f"[ws] emit 失败: {e}", file=sys.stderr)

    async def send_character_state():
        """在进入叙述前同步角色栏，不提前发送线索。"""
        try:
            pc_data = engine.context.world_store.load().get("pc", {})
        except Exception:
            pc_data = {}
        await ws.send_json({
            "type": "character_state",
            "data": json.dumps(pc_data, ensure_ascii=False),
        })

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

    def on_decision(info: dict) -> str | None:
        ev = threading.Event()
        decision_id = info.get("id")
        with decision_lock:
            decision_box["event"] = ev
            decision_box["result"] = None
            decision_box["id"] = decision_id
            decision_active[0] = True
        emit({"type": "decision_request", **info})
        ev.wait(timeout=120)
        with decision_lock:
            decision_active[0] = False
            result = decision_box["result"]
            decision_box["id"] = None
        selected = result or info.get("default_option")
        emit({
            "type": "decision_resolved",
            "decision_id": decision_id,
            "option_id": selected,
            "automatic": result is None,
        })
        return selected

    def on_done():
        emit({"type": "done"})

    def on_game_over(ending_type: str, title: str, summary: str):
        emit({"type": "game_over", "ending_type": ending_type, "title": title, "summary": summary})

    def on_handout(info: dict):
        emit({"type": "handout", "file": info.get("file", ""),
              "label": info.get("label", ""),
              "asset_id": info.get("asset_id", ""),
              "asset_data_uri": info.get("asset_data_uri", ""),   # base64 data URI（electron 兼容）
              "asset_url": info.get("asset_url", ""),             # HTTP URL（web 兼容，fallback）
              "entity_type": info.get("entity_type", ""), "entity_id": info.get("entity_id", "")})

    def on_error(msg: str):
        emit({"type": "error", "message": msg})

    # 通知前端模组列表 + 角色列表 + 存档列表 + 当前主题
    await ws.send_json({
        "type": "module_list",
        "modules": _list_mods(),
        "active": engine.context.module_name,
    })
    await ws.send_json({
        "type": "character_list",
        **list_character_options(engine.context.module_name, context=engine.context),
    })

    # 发送当前模组主题（electron 用 file:// 加载，fetch('/api/theme') 不可用，
    # 故主题也走 WS 下发）
    await ws.send_json({"type": "theme", "theme": _load_theme(engine.context)})
    await ws.send_json(_model_settings_payload(ModelSettings.validated(
        engine.narrative_model,
        engine.judgement_model,
    )))

    saves = engine.list_saves()
    await ws.send_json({"type": "save_list", "saves": saves})

    engine.cb = EngineCallbacks(
        on_narrative=on_narrative,
        on_tension=on_tension,
        on_dice=on_dice,
        on_glm_summary=on_glm_summary,
        on_suggest=on_suggest,
        on_decision=on_decision,
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

            elif msg_type == "model_settings_get":
                await ws.send_json(_model_settings_payload(ModelSettings.validated(
                    engine.narrative_model,
                    engine.judgement_model,
                )))

            elif msg_type == "model_settings_update":
                if not turn_lock.acquire(blocking=False):
                    await ws.send_json({
                        "type": "model_settings_error",
                        "message": "当前回合尚未结束，请在本轮叙述完成后重试。",
                    })
                    continue
                try:
                    settings = ModelSettings.validated(
                        data.get("narrative_model"),
                        data.get("judgement_model"),
                    )
                    persist_model_settings(_ENV_FILE, settings)
                    engine.configure_models(
                        settings.narrative_model,
                        settings.judgement_model,
                    )
                    _active_model_settings = settings
                    await ws.send_json({
                        **_model_settings_payload(settings),
                        "saved": True,
                    })
                except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                    await ws.send_json({
                        "type": "model_settings_error",
                        "message": f"模型设置保存失败：{exc}",
                    })
                finally:
                    turn_lock.release()

            elif msg_type == "module_list":
                await ws.send_json({
                    "type": "module_list",
                    "modules": _list_mods(),
                    "active": engine.context.module_name,
                })

            elif msg_type == "switch_module":
                # 切换活跃模组（开场前在下拉框选择）
                name = data.get("module", engine.context.module_name)
                try:
                    MODULE_REGISTRY.resolve(name)
                except FileNotFoundError:
                    await ws.send_json({"type": "error", "message": f"模组'{name}'不存在"})
                    continue
                if not turn_lock.acquire(blocking=False):
                    await ws.send_json({
                        "type": "error",
                        "message": "当前回合尚未结束，暂时不能切换模组。",
                    })
                else:
                    try:
                        context = RuntimeContext.local(
                            name,
                            project_root=PROJECT_ROOT,
                            runtime_root=RUNTIME_ROOT,
                        )
                        engine.switch_context(context)
                        _set_active_context(context)
                        # 下发新主题 + 新存档列表
                        await ws.send_json({"type": "theme", "theme": _load_theme(context)})
                        await ws.send_json({
                            "type": "module_list",
                            "modules": _list_mods(),
                            "active": name,
                        })
                        await ws.send_json({
                            "type": "character_list",
                            **list_character_options(name, context=context),
                        })
                        await ws.send_json({"type": "save_list", "saves": engine.list_saves()})
                    finally:
                        turn_lock.release()

            elif msg_type == "start":
                # 开始新游戏：触发开场 GM 回合（用 reset() 里预置的开场 prompt）
                try:
                    engine.reset(data.get("character_ref"))
                except ValueError as exc:
                    await ws.send_json({"type": "error", "message": str(exc)})
                    continue
                await send_character_state()
                await ws.send_json({"type": "gm_turn_start"})
                run_turn(engine.handle_action, None)

            elif msg_type == "continue":
                # 继续游戏：读档 + 恢复快照 + 续写 prompt
                slot = data.get("slot_id")  # 可选：指定槽位
                try:
                    count = engine.load(slot)
                except StaleRevisionError as exc:
                    await ws.send_json({"type": "error", "message": str(exc)})
                    continue
                if count is None:
                    await ws.send_json({"type": "error", "message": "未找到存档，请开始新游戏。"})
                else:
                    engine.append_control_instruction(
                        "继续游戏。之前的存档和世界状态已经恢复。"
                        "请基于当前对话历史中的场景、NPC 状态和已发现线索，"
                        "用 1-2 句话简述玩家当前位置和情况，然后提供行动选项。"
                        "不要从头开场，不要重新介绍世界观。"
                    )
                    await send_character_state()
                    await ws.send_json({"type": "gm_turn_start"})
                    run_turn(engine.handle_action, None)

            elif msg_type == "action":
                # 同上：不 await，避免 on_suggest 阻塞时主循环死锁
                await ws.send_json({"type": "gm_turn_start"})
                run_turn(engine.handle_action, data.get("content", ""))

            elif msg_type == "suggest_reply":
                with suggest_lock:
                    if suggest_active[0]:
                        suggest_box["result"] = data.get("confirmed", False)
                        suggest_box["event"].set()

            elif msg_type == "decision_reply":
                with decision_lock:
                    if decision_active[0] and data.get("decision_id") == decision_box["id"]:
                        decision_box["result"] = data.get("option_id")
                        decision_box["event"].set()

            elif msg_type == "save":
                is_manual = data.get("manual", False)
                slot_id = None if is_manual else AUTO_SAVE_SLOT
                sid = engine.save(slot_id)
                await ws.send_json({"type": "saved", "ok": True, "slot_id": sid})

            elif msg_type == "save_list":
                saves = engine.list_saves()
                await ws.send_json({"type": "save_list", "saves": saves})

            elif msg_type == "character_list":
                await ws.send_json({
                    "type": "character_list",
                    **list_character_options(
                        engine.context.module_name, context=engine.context
                    ),
                })

            elif msg_type == "save_load":
                slot_id = data.get("slot_id", "")
                try:
                    cnt = engine.load(slot_id)
                except StaleRevisionError as exc:
                    await ws.send_json({"type": "error", "message": str(exc)})
                    continue
                if cnt is not None:
                    engine.append_control_instruction(
                        "继续游戏。之前的存档和世界状态已经恢复。"
                        "请基于当前对话历史中的场景、NPC 状态和已发现线索，"
                        "用 1-2 句话简述玩家当前位置和情况，然后提供行动选项。"
                        "不要从头开场，不要重新介绍世界观。"
                    )
                    await ws.send_json({"type": "loaded", "ok": True, "slot_id": slot_id, "count": cnt})
                    await send_character_state()
                    await ws.send_json({"type": "gm_turn_start"})
                    run_turn(engine.handle_action, None)
                else:
                    await ws.send_json({"type": "error", "message": "未找到存档。"})

            elif msg_type == "save_delete":
                slot_id = data.get("slot_id", "")
                if slot_id == AUTO_SAVE_SLOT:
                    await ws.send_json({"type": "error", "message": "自动存档不可手动删除。"})
                else:
                    delete_save(slot_id, context=engine.context)
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
                ok = rename_save(slot_id, label, context=engine.context)
                await ws.send_json({"type": "save_renamed", "slot_id": slot_id, "label": label, "ok": ok})

            elif msg_type == "settle_case":
                result = engine.settle_case(
                    data.get("ending_type", "neutral"),
                    data.get("title", "故事结束"),
                    data.get("summary", ""),
                )
                await ws.send_json({"type": "case_settled", **result})
                await ws.send_json({
                    "type": "character_list",
                    **list_character_options(
                        engine.context.module_name, context=engine.context
                    ),
                })
                await ws.send_json({"type": "state"})

            elif msg_type == "quit":
                engine.save("slot_000")  # 退出时存档
                await ws.send_json({"type": "quit_ok"})
                break  # 退出消息循环

            elif msg_type == "load":
                try:
                    count = engine.load()
                except StaleRevisionError as exc:
                    await ws.send_json({"type": "error", "message": str(exc)})
                    continue
                await ws.send_json({"type": "loaded", "ok": count is not None, "count": count or 0})

            elif msg_type == "state":
                try:
                    _ws = engine.context.world_store.load()
                    pc_data = _ws.get("pc", {})
                    clues_data = _enrich_clues_for_frontend(
                        _ws.get("clues_found", {}), _ws, engine.context
                    )
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
    return _load_theme(_active_context)


@app.get("/api/health")
async def health():
    """桌面壳用于等待内置后端启动完成。"""
    return {
        "ok": True,
        "module": _active_context.module_name,
        "world_id": _active_context.world_id,
    }


@app.get("/api/modules")
async def list_modules():
    """列出所有可用模组"""
    return {"modules": _list_mods(), "active": _active_context.module_name}


def _module_error_response(exc: ModulePackageError) -> JSONResponse:
    status = {
        "version_conflict": 409,
        "package_too_large": 413,
        "expanded_too_large": 413,
        "file_too_large": 413,
        "too_many_files": 413,
    }.get(exc.code, 400)
    return JSONResponse({
        "ok": False,
        "error_code": exc.code,
        "error": exc.message,
        "details": exc.details,
    }, status_code=status)


async def _receive_module_upload(request: Request) -> Path:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_PACKAGE_BYTES:
                raise ModulePackageError("package_too_large", "模组包超过 64 MiB 上限")
        except ValueError:
            raise ModulePackageError("invalid_length", "Content-Length 无效")

    import_dir = RUNTIME_ROOT / ".module-imports"
    import_dir.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        prefix="upload-",
        suffix=".trpgmod",
        dir=import_dir,
        delete=False,
    )
    path = Path(handle.name)
    total = 0
    try:
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_PACKAGE_BYTES:
                raise ModulePackageError("package_too_large", "模组包超过 64 MiB 上限")
            handle.write(chunk)
        handle.close()
        if total == 0:
            raise ModulePackageError("empty_upload", "没有收到模组包内容")
        return path
    except Exception:
        handle.close()
        path.unlink(missing_ok=True)
        raise


@app.get("/api/modules/schema/manifest-v1")
async def get_module_manifest_schema():
    return manifest_json_schema()


@app.get("/api/modules/schema/module-v1")
async def get_module_definition_schema():
    return module_json_schema()


@app.get("/api/modules/schema/lorebook-v3")
async def get_lorebook_schema():
    return lorebook_json_schema()


@app.post("/api/modules/compile")
async def compile_module_preview(data: dict):
    """无副作用地校验并编译作者态数据，供编辑器实时预览。"""
    preview = await asyncio.to_thread(
        compile_payload,
        data.get("manifest"),
        data.get("module"),
        data.get("keeper_document", ""),
        data.get("lorebook"),
    )
    return preview.to_dict()


@app.post("/api/modules/inspect")
async def inspect_module_upload(request: Request):
    """只校验上传包，供导入预览和未来编辑器使用。"""
    try:
        path = await _receive_module_upload(request)
        inspection = await asyncio.to_thread(inspect_package, path)
        return {"ok": True, "module": inspection.summary()}
    except ModulePackageError as exc:
        return _module_error_response(exc)
    finally:
        if "path" in locals():
            path.unlink(missing_ok=True)


@app.post("/api/modules/import")
async def import_module_upload(request: Request):
    """安全校验并版本化安装 .trpgmod。"""
    try:
        path = await _receive_module_upload(request)
        record, inspection, already_installed = await asyncio.to_thread(
            MODULE_REGISTRY.install, path
        )
        return JSONResponse({
            "ok": True,
            "already_installed": already_installed,
            "module": record.to_dict(),
            "inspection": inspection.summary(),
        }, status_code=200 if already_installed else 201)
    except ModulePackageError as exc:
        return _module_error_response(exc)
    finally:
        if "path" in locals():
            path.unlink(missing_ok=True)


@app.get("/api/characters")
async def list_characters():
    """列出可用于当前模组的新游戏调查员。"""
    return list_character_options(
        _active_context.module_name, context=_active_context
    )


@app.post("/api/modules/switch")
async def switch_module(data: dict):
    """切换活跃模组"""
    name = data.get("module", _active_context.module_name)
    try:
        MODULE_REGISTRY.resolve(name)
    except FileNotFoundError:
        return {"ok": False, "error": f"模组'{name}'不存在"}
    context = RuntimeContext.local(
        name,
        project_root=PROJECT_ROOT,
        runtime_root=RUNTIME_ROOT,
    )
    _set_active_context(context)
    return {"ok": True, "module": name, "world_id": context.world_id}


@app.websocket("/ws")
async def game_ws(ws: WebSocket):
    await ws.accept()
    try:
        requested_world = ws.query_params.get("world_id")
        requested_module = ws.query_params.get("module") or _active_context.module_name
        if requested_world:
            context = RuntimeContext.create(
                requested_world,
                requested_module,
                project_root=PROJECT_ROOT,
                runtime_root=RUNTIME_ROOT,
            )
        else:
            context = RuntimeContext.local(
                requested_module,
                project_root=PROJECT_ROOT,
                runtime_root=RUNTIME_ROOT,
            )
        engine = GameEngine(context)
        engine.configure_models(
            _active_model_settings.narrative_model,
            _active_model_settings.judgement_model,
        )
        engine.prepare_session()
    except Exception as e:
        # 配置/初始化失败时，把错误发回客户端而不是静默断开
        await ws.send_json({"type": "error", "message": f"游戏引擎初始化失败：{e}"})
        await ws.close()
        return
    await run_ws_session(ws, engine)


# ---- 资产文件 ----
@app.get("/api/assets/{module_name}/{filename:path}")
async def serve_asset(module_name: str, filename: str):
    """服务模组资产图片，带路径遍历保护。"""
    try:
        record = MODULE_REGISTRY.resolve(module_name)
    except FileNotFoundError:
        return JSONResponse({"error": "module not found"}, status_code=404)
    asset_path = (record.path / "assets" / filename).resolve()
    allowed = (record.path / "assets").resolve()
    if not asset_path.is_relative_to(allowed):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if not asset_path.is_file():
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
