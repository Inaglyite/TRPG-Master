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
import secrets
import tempfile
import threading
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
from src.event_stream import OrderedTurnEventStream
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
from src.player_notes import PlayerNotesConflict, PlayerNotesStore
from src.persistence import delete_save, load_game
from src.characters import list_character_options
from src.module_compiler import compile_payload
from src.module_format import (
    manifest_json_schema,
    manifest_v2_json_schema,
    module_json_schema,
    module_v2_json_schema,
)
from src.lorebook import lorebook_json_schema
from src.handouts import resolve_handout_asset
from src.module_registry import (
    MAX_PACKAGE_BYTES,
    ModulePackageError,
    ModuleRegistry,
    inspect_package,
)
from src.runtime import RuntimeContext
from src.world_branches import WorldBranchService
from src.world_store import StaleRevisionError
from src.game_application import (
    ApplicationUseCaseError,
    GameApplication,
    SaveNotFoundError,
)
from src.ws_session import SessionTurnGate, WsSessionContext
from src.ws_router import WsMessageRouter

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
WORLD_BRANCHES = WorldBranchService(PROJECT_ROOT, RUNTIME_ROOT)
_active_context = RuntimeContext.local(DEFAULT_MODULE_NAME)
_active_model_settings = ModelSettings.validated(
    NARRATIVE_MODEL, JUDGEMENT_MODEL
)
_world_turn_locks: dict[str, threading.Lock] = {}
_world_turn_locks_guard = threading.Lock()


def _world_turn_lock(context: RuntimeContext) -> threading.Lock:
    key = f"{context.runtime_root.resolve()}::{context.world_id}"
    with _world_turn_locks_guard:
        return _world_turn_locks.setdefault(key, threading.Lock())


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
    普通同步函数；所有输出进入同一个 FIFO sender，避免跨线程 send_json
    任务与主循环响应互相抢序。
    """
    global _active_model_settings

    loop = asyncio.get_running_loop()
    outbound = OrderedTurnEventStream(ws, loop)
    turn_gate = SessionTurnGate(_world_turn_lock(engine.context))
    session = WsSessionContext(outbound=outbound, turn_gate=turn_gate)
    game_app = GameApplication.for_engine(engine, auto_slot=AUTO_SAVE_SLOT)
    suggest_reply = session.suggest_reply
    decision_reply = session.decision_reply
    reserve_turn = session.reserve_turn

    def turn_state_busy() -> bool:
        return session.turn_busy

    def release_turn() -> None:
        session.release_turn()

    def run_reserved_turn(coro_fn, *args):
        """Run a previously reserved turn in the executor."""
        def _wrapped():
            try:
                coro_fn(*args)
            except Exception as e:
                engine.finish_turn_record(
                    status="failed",
                    error=f"{type(e).__name__}: {e}",
                )
                import traceback
                print(f"[ws] 回合异常: {e}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                emit({
                    "type": "error",
                    "message": "本轮处理失败，请重新发送刚才的行动。",
                })
                if outbound.has_active_turn:
                    outbound.end_turn()
            finally:
                if engine.active_turn_id is not None:
                    engine.finish_turn_record(
                        status="failed",
                        error="回合执行结束但没有产生完整提交记录",
                    )
                release_turn()
        loop.run_in_executor(None, _wrapped)

    async def launch_reserved_turn(
        coro_fn,
        *args,
        turn_kind: str,
        player_input: str | None = None,
    ) -> None:
        """Create the lifecycle only after the session reservation succeeded."""
        try:
            turn_id = engine.begin_turn_record(
                kind=turn_kind,
                player_input=player_input,
            )
            await outbound.begin_turn(turn_id)
        except Exception:
            engine.finish_turn_record(
                status="failed",
                error="回合生命周期启动失败",
            )
            release_turn()
            raise
        run_reserved_turn(coro_fn, *args)

    async def launch_rewrite(turn_id: str) -> None:
        operation_id = f"rewrite:{secrets.token_hex(8)}"
        try:
            await outbound.begin_turn(
                operation_id,
                turn_kind="rewrite",
                metadata={"source_turn_id": turn_id},
            )
        except Exception:
            release_turn()
            raise

        def _rewrite_worker():
            try:
                result = game_app.rewrite_turn.execute(turn_id)
                result["source_turn_id"] = result.pop("turn_id")
                outbound.end_turn({"type": "turn_rewritten", **result})
            except Exception as exc:
                log_message = f"{type(exc).__name__}: {exc}"
                print(f"[ws] 重新叙述失败: {log_message}", file=sys.stderr)
                outbound.end_turn({
                    "type": "turn_rewrite_failed",
                    "source_turn_id": turn_id,
                    "message": str(exc) or "重新叙述失败",
                })
            finally:
                release_turn()

        loop.run_in_executor(None, _rewrite_worker)

    def emit(payload: dict):
        """线程安全地把一条消息加入有序发送流。"""
        engine.record_turn_event(payload)
        outbound.emit(payload)

    def world_context_payload() -> dict:
        return {
            "type": "world_context",
            "world_id": engine.context.world_id,
            "module_name": engine.context.module_name,
        }

    def world_list_payload() -> dict:
        return {
            "type": "world_list",
            "active_world_id": engine.context.world_id,
            "worlds": WORLD_BRANCHES.list_worlds(
                engine.context.module_name,
                active_world_id=engine.context.world_id,
            ),
        }

    async def send_character_state():
        """在进入叙述前同步角色栏，不提前发送线索。"""
        try:
            pc_data = engine.context.world_store.load().get("pc", {})
        except Exception:
            pc_data = {}
        await outbound.send({
            "type": "character_state",
            "data": json.dumps(pc_data, ensure_ascii=False),
        })

    def turn_recovery_payload(requested_turn_id: str | None = None) -> dict:
        payload = engine.turn_recovery_status(requested_turn_id)
        for key in ("requested", "active", "latest_completed"):
            record = payload.get(key)
            if not isinstance(record, dict):
                continue
            events = record.get("events")
            if not isinstance(events, list):
                continue
            for event in events:
                if (
                    isinstance(event, dict)
                    and event.get("type") == "handout"
                    and event.get("file")
                ):
                    event.update(_asset_payload(event["file"], engine.context))
        return {"type": "turn_recovery", **payload}

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
        emit({"type": "suggest_check", **info})
        return suggest_reply.wait(timeout=120)

    def on_decision(info: dict) -> str | None:
        decision_id = info.get("id")
        emit({"type": "decision_request", **info})
        result = decision_reply.wait(request_id=decision_id, timeout=120)
        selected = result or info.get("default_option")
        emit({
            "type": "decision_resolved",
            "decision_id": decision_id,
            "option_id": selected,
            "automatic": result is None,
        })
        return selected

    def on_phase(phase: str, label: str):
        emit({"type": "turn_phase", "phase": phase, "label": label})

    def on_choices(choices: list[dict]):
        emit({"type": "choices", "choices": choices})

    def on_done():
        outbound.end_turn()

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

    router = WsMessageRouter()

    @router.handler("ping")
    async def handle_ping(_data: dict) -> None:
        await outbound.send({"type": "pong"})

    @router.handler("turn_recovery_get")
    async def handle_turn_recovery(data: dict) -> None:
        await outbound.send(turn_recovery_payload(data.get("turn_id")))

    @router.handler("turn_diagnostics_get")
    async def handle_turn_diagnostics(data: dict) -> None:
        await outbound.send({
            "type": "turn_diagnostics",
            "diagnostics": engine.turn_diagnostics(data.get("turn_id")),
        })

    @router.handler("world_list")
    async def handle_world_list(_data: dict) -> None:
        await outbound.send(world_list_payload())

    @router.handler("player_notes_get")
    async def handle_player_notes_get(_data: dict) -> None:
        notes = PlayerNotesStore(engine.context.world_dir).load()
        await outbound.send({"type": "player_notes", **notes})

    @router.handler("model_settings_get")
    async def handle_model_settings_get(_data: dict) -> None:
        await outbound.send(_model_settings_payload(ModelSettings.validated(
            engine.narrative_model,
            engine.judgement_model,
        )))

    @router.handler("module_list")
    async def handle_module_list(_data: dict) -> None:
        await outbound.send({
            "type": "module_list",
            "modules": _list_mods(),
            "active": engine.context.module_name,
        })

    @router.handler("suggest_reply")
    async def handle_suggest_reply(data: dict) -> None:
        suggest_reply.resolve(bool(data.get("confirmed", False)))

    @router.handler("decision_reply")
    async def handle_decision_reply(data: dict) -> None:
        decision_reply.resolve(
            data.get("option_id"),
            request_id=data.get("decision_id"),
        )

    @router.handler("save_list")
    async def handle_save_list(_data: dict) -> None:
        await outbound.send({"type": "save_list", "saves": engine.list_saves()})

    @router.handler("character_list")
    async def handle_character_list(_data: dict) -> None:
        await outbound.send({
            "type": "character_list",
            **list_character_options(
                engine.context.module_name,
                context=engine.context,
            ),
        })

    @router.handler("player_notes_update")
    async def handle_player_notes_update(data: dict) -> None:
        try:
            notes = PlayerNotesStore(engine.context.world_dir).save(
                data.get("text", ""),
                expected_revision=(
                    int(data["revision"])
                    if data.get("revision") is not None
                    else None
                ),
            )
            await outbound.send({"type": "player_notes", "saved": True, **notes})
        except PlayerNotesConflict as exc:
            current = PlayerNotesStore(engine.context.world_dir).load()
            await outbound.send({
                "type": "player_notes_conflict",
                "message": str(exc),
                **current,
            })
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            await outbound.send({
                "type": "player_notes_error",
                "message": str(exc) or "玩家笔记保存失败",
            })

    @router.handler("model_settings_update")
    async def handle_model_settings_update(data: dict) -> None:
        global _active_model_settings
        if not turn_gate.try_acquire_session():
            await outbound.send({
                "type": "model_settings_error",
                "message": "当前回合尚未结束，请在本轮叙述完成后重试。",
            })
            return
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
            await outbound.send({
                **_model_settings_payload(settings),
                "saved": True,
            })
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            await outbound.send({
                "type": "model_settings_error",
                "message": f"模型设置保存失败：{exc}",
            })
        finally:
            turn_gate.release_session()

    @router.handler("save")
    async def handle_save(data: dict) -> None:
        is_manual = data.get("manual", False)
        slot_id = None if is_manual else AUTO_SAVE_SLOT
        if turn_state_busy():
            await outbound.send({
                "type": "saved",
                "ok": False,
                "slot_id": slot_id or "",
                "reason": "turn_in_progress",
            })
            return
        sid = game_app.manage_saves.save(manual=bool(is_manual))
        await outbound.send({"type": "saved", "ok": True, "slot_id": sid})

    @router.handler("save_delete")
    async def handle_save_delete(data: dict) -> None:
        slot_id = data.get("slot_id", "")
        if slot_id == AUTO_SAVE_SLOT:
            await outbound.send({
                "type": "error",
                "message": "自动存档不可手动删除。",
            })
            return
        delete_save(slot_id, context=engine.context)
        await outbound.send({"type": "save_deleted", "slot_id": slot_id})

    @router.handler("save_create")
    async def handle_save_create(_data: dict) -> None:
        if turn_state_busy():
            await outbound.send({
                "type": "saved",
                "ok": False,
                "slot_id": "",
                "reason": "turn_in_progress",
            })
            return
        sid = game_app.manage_saves.create_slot()
        await outbound.send({"type": "saved", "ok": True, "slot_id": sid})

    @router.handler("save_rename")
    async def handle_save_rename(data: dict) -> None:
        from src.persistence import rename_save

        slot_id = data.get("slot_id", "")
        label = data.get("label", "")
        ok = rename_save(slot_id, label, context=engine.context)
        await outbound.send({
            "type": "save_renamed",
            "slot_id": slot_id,
            "label": label,
            "ok": ok,
        })

    @router.handler("settle_case")
    async def handle_settle_case(data: dict) -> None:
        if turn_state_busy():
            await outbound.send({
                "type": "error",
                "message": "当前回合尚未结束，暂时不能结算案件。",
            })
            return
        result = engine.settle_case(
            data.get("ending_type", "neutral"),
            data.get("title", "故事结束"),
            data.get("summary", ""),
        )
        await outbound.send({"type": "case_settled", **result})
        await outbound.send({
            "type": "character_list",
            **list_character_options(
                engine.context.module_name,
                context=engine.context,
            ),
        })
        await outbound.send({"type": "state"})

    @router.handler("load")
    async def handle_legacy_load(_data: dict) -> None:
        if turn_state_busy():
            await outbound.send({
                "type": "error",
                "message": "当前回合尚未结束，暂时不能读档。",
            })
            return
        try:
            count = engine.load()
        except StaleRevisionError as exc:
            await outbound.send({"type": "error", "message": str(exc)})
            return
        await outbound.send({
            "type": "loaded",
            "ok": count is not None,
            "count": count or 0,
        })

    @router.handler("state")
    async def handle_state(_data: dict) -> None:
        try:
            world_state = engine.context.world_store.load()
            pc_data = world_state.get("pc", {})
            clues_data = _enrich_clues_for_frontend(
                world_state.get("clues_found", {}),
                world_state,
                engine.context,
            )
        except Exception:
            pc_data, clues_data = {}, {}
        await outbound.send({
            "type": "state_data",
            "data": json.dumps(pc_data, ensure_ascii=False),
            "clues": json.dumps(clues_data, ensure_ascii=False),
        })

    @router.handler("quit")
    async def handle_quit(_data: dict) -> None:
        engine.save(AUTO_SAVE_SLOT)
        await outbound.send({"type": "quit_ok"})
        session.close_requested = True

    @router.handler("turn_branch_create")
    async def handle_turn_branch_create(data: dict) -> None:
        if not await reserve_turn():
            return
        turn_id = str(data.get("turn_id") or "")
        if not turn_id:
            release_turn()
            await outbound.send({
                "type": "turn_branch_failed",
                "message": "缺少分支回合 ID",
            })
            return
        try:
            branch = await asyncio.to_thread(
                WORLD_BRANCHES.create,
                engine.context,
                engine.turn_journal,
                turn_id,
                label=data.get("label", ""),
            )
            engine.switch_context(branch.context)
            engine.adopt_message_history(branch.messages)
            turn_gate.rebind_world(_world_turn_lock(branch.context))
            _set_active_context(branch.context)
            history = engine.turn_journal.public_history()
        except Exception as exc:
            release_turn()
            await outbound.send({
                "type": "turn_branch_failed",
                "source_turn_id": turn_id,
                "message": str(exc) or "创建时间线分支失败",
            })
            return
        release_turn()
        await outbound.send({
            "type": "turn_branched",
            "source_turn_id": turn_id,
            "world_id": branch.context.world_id,
            "module_name": branch.context.module_name,
            "label": branch.label,
            "history": history,
        })
        await outbound.send(world_context_payload())
        await outbound.send(world_list_payload())
        await outbound.send({"type": "save_list", "saves": engine.list_saves()})
        await send_character_state()

    @router.handler("world_switch")
    async def handle_world_switch(data: dict) -> None:
        if not turn_gate.try_acquire_session():
            await outbound.send({
                "type": "world_switch_failed",
                "message": "当前回合尚未结束，暂时不能切换时间线。",
            })
            return
        target_lock: threading.Lock | None = None
        target_lock_acquired = False
        try:
            context = WORLD_BRANCHES.open(str(data.get("world_id") or ""))
            target_lock = _world_turn_lock(context)
            if not target_lock.acquire(blocking=False):
                raise RuntimeError("目标时间线正在处理另一个回合，请稍后重试。")
            target_lock_acquired = True
            messages, _snapshot = load_game(AUTO_SAVE_SLOT, context=context)
            engine.switch_context(context)
            if messages is not None:
                engine.adopt_message_history(messages)
            turn_gate.rebind_world(target_lock)
            _set_active_context(context)
            history = engine.turn_journal.public_history()
        except Exception as exc:
            await outbound.send({
                "type": "world_switch_failed",
                "message": str(exc) or "切换时间线失败",
            })
            return
        finally:
            if target_lock is not None and target_lock_acquired:
                target_lock.release()
            turn_gate.release_session()
        await outbound.send({
            "type": "world_switched",
            "world_id": engine.context.world_id,
            "module_name": engine.context.module_name,
            "history": history,
        })
        await outbound.send(world_context_payload())
        await outbound.send(world_list_payload())
        await outbound.send({"type": "theme", "theme": _load_theme(engine.context)})
        await outbound.send({"type": "save_list", "saves": engine.list_saves()})
        await send_character_state()

    @router.handler("turn_rewrite")
    async def handle_turn_rewrite(data: dict) -> None:
        if not await reserve_turn():
            return
        turn_id = str(data.get("turn_id") or "")
        if not turn_id:
            release_turn()
            await outbound.send({
                "type": "turn_rewrite_failed",
                "message": "缺少需要重新叙述的回合 ID",
            })
            return
        await launch_rewrite(turn_id)

    @router.handler("switch_module")
    async def handle_switch_module(data: dict) -> None:
        name = data.get("module", engine.context.module_name)
        try:
            MODULE_REGISTRY.resolve(name)
        except FileNotFoundError:
            await outbound.send({"type": "error", "message": f"模组'{name}'不存在"})
            return
        if not turn_gate.try_acquire_session():
            await outbound.send({
                "type": "error",
                "message": "当前回合尚未结束，暂时不能切换模组。",
            })
            return
        try:
            context = RuntimeContext.local(
                name,
                project_root=PROJECT_ROOT,
                runtime_root=RUNTIME_ROOT,
            )
            engine.switch_context(context)
            turn_gate.rebind_world(_world_turn_lock(context))
            _set_active_context(context)
            await outbound.send(world_context_payload())
            await outbound.send(world_list_payload())
            await outbound.send({"type": "theme", "theme": _load_theme(context)})
            await outbound.send({
                "type": "module_list",
                "modules": _list_mods(),
                "active": name,
            })
            await outbound.send({
                "type": "character_list",
                **list_character_options(name, context=context),
            })
            await outbound.send({"type": "save_list", "saves": engine.list_saves()})
        finally:
            turn_gate.release_session()

    @router.handler("start")
    async def handle_start(data: dict) -> None:
        if not await reserve_turn():
            return
        try:
            intent = game_app.start_game.execute(data.get("character_ref"))
        except ValueError as exc:
            release_turn()
            await outbound.send({"type": "error", "message": str(exc)})
            return
        except Exception:
            release_turn()
            raise
        try:
            await send_character_state()
        except Exception:
            release_turn()
            raise
        await launch_reserved_turn(
            engine.handle_action,
            intent.engine_input,
            turn_kind=intent.kind,
        )

    async def resume_game(slot_id: str | None, *, announce_loaded: bool) -> None:
        if not await reserve_turn():
            return
        try:
            intent = game_app.resume_game.execute(slot_id)
        except StaleRevisionError as exc:
            release_turn()
            await outbound.send({"type": "error", "message": str(exc)})
            return
        except SaveNotFoundError:
            release_turn()
            message = "未找到存档。" if announce_loaded else "未找到存档，请开始新游戏。"
            await outbound.send({"type": "error", "message": message})
            return
        except Exception:
            release_turn()
            raise
        try:
            if announce_loaded:
                await outbound.send({
                    "type": "loaded",
                    "ok": True,
                    "slot_id": intent.slot_id or "",
                    "count": intent.loaded_message_count,
                })
            await send_character_state()
        except Exception:
            release_turn()
            raise
        await launch_reserved_turn(
            engine.handle_action,
            intent.engine_input,
            turn_kind=intent.kind,
        )

    @router.handler("continue")
    async def handle_continue(data: dict) -> None:
        await resume_game(data.get("slot_id"), announce_loaded=False)

    @router.handler("save_load")
    async def handle_save_load(data: dict) -> None:
        await resume_game(str(data.get("slot_id") or ""), announce_loaded=True)

    @router.handler("action")
    async def handle_action(data: dict) -> None:
        if not await reserve_turn():
            return
        try:
            intent = game_app.perform_action.execute(data.get("content", ""))
        except ApplicationUseCaseError as exc:
            release_turn()
            await outbound.send({"type": "error", "message": str(exc)})
            return
        await launch_reserved_turn(
            engine.handle_action,
            intent.engine_input,
            turn_kind=intent.kind,
            player_input=intent.player_input,
        )

    # 保持首连的五条初始化消息稳定；世界身份随 module_list 一并下发。
    await outbound.send({
        "type": "module_list",
        "modules": _list_mods(),
        "active": engine.context.module_name,
        "world_id": engine.context.world_id,
        "module_name": engine.context.module_name,
    })
    await outbound.send({
        "type": "character_list",
        **list_character_options(engine.context.module_name, context=engine.context),
    })

    # 发送当前模组主题（electron 用 file:// 加载，fetch('/api/theme') 不可用，
    # 故主题也走 WS 下发）
    await outbound.send({"type": "theme", "theme": _load_theme(engine.context)})
    await outbound.send(_model_settings_payload(ModelSettings.validated(
        engine.narrative_model,
        engine.judgement_model,
    )))

    saves = engine.list_saves()
    await outbound.send({"type": "save_list", "saves": saves})

    engine.cb = EngineCallbacks(
        on_narrative=on_narrative,
        on_tension=on_tension,
        on_dice=on_dice,
        on_glm_summary=on_glm_summary,
        on_suggest=on_suggest,
        on_decision=on_decision,
        on_phase=on_phase,
        on_choices=on_choices,
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

            routed = await router.dispatch(data)
            if routed.handled:
                if session.close_requested:
                    break
                continue
            await outbound.send({
                "type": "protocol_error",
                "code": "unknown_message_type",
                "message_type": routed.message_type,
                "message": "客户端发送了当前服务端不支持的消息类型。",
            })
    except WebSocketDisconnect:
        pass
    except RuntimeError:
        # 连接已断开（客户端关闭窗口等），收尾即可
        pass
    finally:
        # Reloading/closing the client must stop the old streaming request;
        # otherwise it keeps the world lock while the new-game screen waits.
        engine.cancel_active_turn()
        # A disconnected browser can no longer answer modal handshakes. Wake
        # the worker immediately so it can take the safe default and release
        # the world-level turn lock instead of waiting the full timeout.
        session.cancel_pending_replies()
        await outbound.close()


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


@app.get("/api/modules/schema/manifest-v2")
async def get_module_manifest_v2_schema():
    return manifest_v2_json_schema()


@app.get("/api/modules/schema/module-v2")
async def get_module_definition_v2_schema():
    return module_v2_json_schema()


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
