#!/usr/bin/env python3
# ruff: noqa: E402
"""TRPG Agent WebSocket 服务器 —— GameEngine + FastAPI"""

import asyncio
import copy
import json
import mimetypes
import os
import runpy
import secrets
import sys
import tempfile
import threading
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
if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1].endswith(".py"):
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

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from src.asset_payload import (
    SpeakerPayloadResolver,
    asset_payload,
    enrich_narrative_segments,
    enrich_pc_for_frontend,
)
from src.auth import (
    LOGIN_LIMITER,
    SESSION_COOKIE,
    audit,
    auth_required,
    authenticate,
    authorize_world,
    create_login_session,
    create_user,
    request_user,
    revoke_session,
    validate_websocket_origin,
    websocket_user,
)
from src.characters import list_character_options
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
from src.database import (
    World,
    WorldInvestigator,
    WorldMember,
    database_url,
    new_id,
    session_scope,
    utcnow,
)
from src.editor_api import create_editor_router
from src.editor_projects import EditorProjectStore
from src.engine import EngineCallbacks, GameEngine
from src.event_stream import OrderedTurnEventStream
from src.game_application import (
    ApplicationUseCaseError,
    GameApplication,
    SaveNotFoundError,
)
from src.handouts import resolve_handout_asset
from src.investigators import (
    activate_investigator,
    initialize_investigator_roster,
    public_investigator_roster,
    sync_active_investigator,
    visible_clues_for_investigator,
)
from src.lorebook import lorebook_json_schema
from src.model_settings import ModelSettings, persist_model_settings
from src.module_compiler import compile_payload
from src.module_format import (
    manifest_json_schema,
    manifest_v2_json_schema,
    module_json_schema,
    module_v2_json_schema,
)
from src.module_registry import (
    MAX_PACKAGE_BYTES,
    ModulePackageError,
    ModuleRegistry,
    inspect_package,
)
from src.multiplayer import (
    MultiplayerError,
    accept_invite,
    claim_investigator,
    create_invite,
    list_invites,
    release_investigator,
    remove_member,
    reserve_room_action,
    revoke_invite,
    room_members,
    transfer_owner,
    update_member_role,
)
from src.persistence import delete_save, load_game
from src.player_notes import PlayerNotesConflict, PlayerNotesStore
from src.room_runtime import (
    ActionReservationError,
    GameRoom,
    RoomConnection,
    RoomDriverTransport,
    RoomEventHub,
    RoomManager,
)
from src.runtime import RuntimeContext
from src.speaker_parser import parse_segments as parse_speaker_segments
from src.tool_protocol import strip_tool_protocol
from src.world_branches import WorldBranchService
from src.world_store import StaleRevisionError
from src.ws_router import WsMessageRouter
from src.ws_session import SessionTurnGate, WsSessionContext

app = FastAPI(title="TRPG Agent API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["null"],
    allow_origin_regex=r"https?://(?:127\.0\.0\.1|localhost)(?::\d+)?",
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Module-Filename"],
)
MODULE_REGISTRY = ModuleRegistry(PROJECT_ROOT, RUNTIME_ROOT)
WORLD_BRANCHES = WorldBranchService(PROJECT_ROOT, RUNTIME_ROOT)
EDITOR_PROJECTS = EditorProjectStore(RUNTIME_ROOT)
app.include_router(create_editor_router(EDITOR_PROJECTS))
DATABASE_URL = database_url(RUNTIME_ROOT)
_active_context = RuntimeContext.local(DEFAULT_MODULE_NAME)
_active_model_settings = ModelSettings.validated(NARRATIVE_MODEL, JUDGEMENT_MODEL)
_world_turn_locks: dict[str, threading.Lock] = {}
_world_turn_locks_guard = threading.Lock()
ROOM_MANAGER = RoomManager()


@app.middleware("http")
async def authentication_gate(request: Request, call_next):
    """Cloud mode protects every API except health and authentication."""
    public = (
        request.url.path
        in {
            "/api/health",
            "/api/auth/register",
            "/api/auth/login",
        }
        or request.url.path == "/"
    )
    if auth_required() and request.url.path.startswith("/api/") and not public:
        user = request_user(request, DATABASE_URL)
        if user is None:
            return JSONResponse({"detail": "未登录或会话已过期"}, status_code=401)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin", "")
            allowed = {
                item.strip()
                for item in os.environ.get("TRPG_ALLOWED_ORIGINS", "").split(",")
                if item.strip()
            }
            if not allowed:
                return JSONResponse(
                    {"detail": "服务端尚未配置 TRPG_ALLOWED_ORIGINS"},
                    status_code=503,
                )
            if not origin or origin not in allowed:
                return JSONResponse({"detail": "请求 Origin 不受信任"}, status_code=403)
        request.state.user = user
    return await call_next(request)


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
        profiles.append(
            {
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
            }
        )

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
                asset.update(asset_payload(asset["file"], context))
    return enriched


# ---------------------------------------------------------------------------
# WebSocket 会话 —— 把引擎回调桥接到 WebSocket
# ---------------------------------------------------------------------------


async def run_ws_session(ws: WebSocket, engine: GameEngine, *, user_id: str | None = None):
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
    # 每个发言段各自携带一次内联身份；不按 NPC/会话去重，避免 Electron 退回占位
    pending_inline_speakers: dict[str, dict] = {}
    resolve_speaker = SpeakerPayloadResolver(engine)

    def public_chat_events(record: dict) -> list[dict]:
        """Enrich saved segments and repair legacy/all-narration attribution."""
        segments = record.get("narrative_segments") or []
        narrative = strip_tool_protocol(str(record.get("narrative") or ""))
        record["narrative"] = narrative
        clean_segments = []
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            clean = dict(segment)
            clean["text"] = strip_tool_protocol(str(clean.get("text") or ""))
            if clean["text"].strip():
                clean_segments.append(clean)
        segments = clean_segments
        events = record.get("events")
        if isinstance(events, list):
            for event in events:
                if isinstance(event, dict) and event.get("type") == "narrative_chunk":
                    event["text"] = strip_tool_protocol(str(event.get("text") or ""))
        has_speech = any(
            isinstance(segment, dict) and segment.get("kind") == "speech"
            for segment in segments
        )
        if narrative and not has_speech:
            reparsed, _clean = parse_speaker_segments(
                narrative,
                is_valid_npc=engine.is_valid_npc_id,
                on_unknown_npc=engine.log_unknown_npc_speaker,
                speaker_aliases=engine.npc_speaker_aliases(),
            )
            if any(segment.kind == "speech" for segment in reparsed):
                segments = [segment.to_dict() for segment in reparsed]
        return enrich_narrative_segments(segments, resolve_speaker)

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
                emit(
                    {
                        "type": "error",
                        "message": "本轮处理失败，请重新发送刚才的行动。",
                    }
                )
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
            turn_record = engine.turn_journal.read(turn_id)
            await outbound.begin_turn(
                turn_id,
                metadata={"parent_turn_id": turn_record.get("parent_turn_id")},
            )
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
        source_record = engine.turn_journal.read(turn_id)
        branch_source_turn_id = source_record.get("parent_turn_id") or turn_id
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
                result["branch_source_turn_id"] = branch_source_turn_id
                enriched = public_chat_events(result)
                result["narrative_segments"] = enriched
                result["chat_events"] = enriched
                outbound.end_turn({"type": "turn_rewritten", **result})
            except Exception as exc:
                log_message = f"{type(exc).__name__}: {exc}"
                print(f"[ws] 重新叙述失败: {log_message}", file=sys.stderr)
                outbound.end_turn(
                    {
                        "type": "turn_rewrite_failed",
                        "source_turn_id": turn_id,
                        "branch_source_turn_id": branch_source_turn_id,
                        "message": str(exc) or "重新叙述失败",
                    }
                )
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
        worlds = WORLD_BRANCHES.list_worlds(
            engine.context.module_name,
            active_world_id=engine.context.world_id,
        )
        if auth_required() and user_id:
            with session_scope(DATABASE_URL) as db_session:
                allowed_ids = {
                    row[0]
                    for row in db_session.query(WorldMember.world_id)
                    .filter_by(user_id=user_id)
                    .all()
                }
            worlds = [world for world in worlds if world["world_id"] in allowed_ids]
        return {
            "type": "world_list",
            "active_world_id": engine.context.world_id,
            "worlds": worlds,
        }

    async def send_character_state():
        """在进入叙述前同步角色栏，不提前发送线索。"""
        try:
            pc_data = engine.context.world_store.load().get("pc", {})
            pc_data = enrich_pc_for_frontend(pc_data, engine.context)
        except Exception:
            pc_data = {}
        await outbound.send(
            {
                "type": "character_state",
                "data": json.dumps(pc_data, ensure_ascii=False),
            }
        )

    def turn_recovery_payload(requested_turn_id: str | None = None) -> dict:
        payload = engine.turn_recovery_status(requested_turn_id)
        for key in ("requested", "active", "latest_completed"):
            record = payload.get(key)
            if not isinstance(record, dict):
                continue
            segments = record.get("narrative_segments")
            if isinstance(segments, list) and segments:
                enriched = public_chat_events(record)
                record["narrative_segments"] = enriched
                record["chat_events"] = enriched
            events = record.get("events")
            if not isinstance(events, list):
                continue
            for event in events:
                if isinstance(event, dict) and event.get("type") == "handout" and event.get("file"):
                    event.update(asset_payload(event["file"], engine.context))
        return {"type": "turn_recovery", **payload}

    # ---- 同步回调（被引擎在工作线程里同步调用）----
    def on_narrative(text: str, npc_id: str | None = None):
        engine.mark_first_visible()
        payload = {"type": "narrative_chunk", "text": text}
        if npc_id:
            payload["npc_id"] = npc_id
            # Electron 就地兜底：NPC 首个文本块同时携带身份，不等另一条事件。
            speaker = pending_inline_speakers.pop(npc_id, None)
            if speaker:
                payload["speaker"] = speaker
        emit(payload)

    def on_speaker_segment(npc_id: str):
        speaker = resolve_speaker(npc_id)
        if speaker:
            pending_inline_speakers[npc_id] = speaker
            emit({"type": "narrative_segment", "segment": {"kind": "speech", "speaker": speaker}})

    def on_narrative_segments(segments: list):
        enriched = enrich_narrative_segments(segments, resolve_speaker)
        emit({"type": "chat_events", "events": enriched})

    def on_performance(metrics: dict):
        emit({"type": "turn_performance", "metrics": metrics})

    def on_tension(text: str, cat: str):
        emit({"type": "tension", "text": text, "category": cat})

    def on_dice(summary: str, roll_data: dict | None = None):
        emit({"type": "dice_result", "summary": summary, "roll_data": roll_data or {}})

    def on_glm_summary(text: str):
        emit({"type": "glm_summary", "text": text})

    def on_private_event(info: dict):
        emit({"type": "private_event", **info})

    def on_suggest(info: dict) -> bool:
        """向客户端发起检定确认，工作线程经 threading.Event 阻塞等待回复。"""
        emit({"type": "suggest_check", **info})
        return suggest_reply.wait(timeout=120)

    def on_decision(info: dict) -> str | None:
        decision_id = info.get("id")
        emit({"type": "decision_request", **info})
        result = decision_reply.wait(request_id=decision_id, timeout=120)
        selected = result or info.get("default_option")
        emit(
            {
                "type": "decision_resolved",
                "decision_id": decision_id,
                "option_id": selected,
                "automatic": result is None,
            }
        )
        return selected

    def on_phase(phase: str, label: str):
        emit({"type": "turn_phase", "phase": phase, "label": label})

    def on_choices(choices: list[dict]):
        emit({"type": "choices", "choices": choices})

    def on_done():
        if getattr(engine, "_multiplayer_roster_active", False):
            try:
                sync_active_investigator(engine.context)
                state = engine.context.world_store.load()
                emit(
                    {
                        "type": "investigator_roster",
                        "investigators": public_investigator_roster(state),
                        "active_investigator_id": state.get("active_investigator_id"),
                    }
                )
            except Exception as exc:
                print(f"[room] 调查员状态同步失败: {exc}", file=sys.stderr)
        outbound.end_turn()

    def on_game_over(ending_type: str, title: str, summary: str):
        emit({"type": "game_over", "ending_type": ending_type, "title": title, "summary": summary})

    def on_handout(info: dict):
        emit(
            {
                "type": "handout",
                "file": info.get("file", ""),
                "label": info.get("label", ""),
                "asset_id": info.get("asset_id", ""),
                "asset_data_uri": info.get(
                    "asset_data_uri", ""
                ),  # base64 data URI（electron 兼容）
                "asset_url": info.get("asset_url", ""),  # HTTP URL（web 兼容，fallback）
                "entity_type": info.get("entity_type", ""),
                "entity_id": info.get("entity_id", ""),
            }
        )

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
        await outbound.send(
            {
                "type": "turn_diagnostics",
                "diagnostics": engine.turn_diagnostics(data.get("turn_id")),
            }
        )

    @router.handler("world_list")
    async def handle_world_list(_data: dict) -> None:
        await outbound.send(world_list_payload())

    @router.handler("player_notes_get")
    async def handle_player_notes_get(_data: dict) -> None:
        notes = PlayerNotesStore(engine.context.world_dir, user_id=user_id).load()
        await outbound.send({"type": "player_notes", **notes})

    @router.handler("model_settings_get")
    async def handle_model_settings_get(_data: dict) -> None:
        await outbound.send(
            _model_settings_payload(
                ModelSettings.validated(
                    engine.narrative_model,
                    engine.judgement_model,
                )
            )
        )

    @router.handler("module_list")
    async def handle_module_list(_data: dict) -> None:
        await outbound.send(
            {
                "type": "module_list",
                "modules": _list_mods(),
                "active": engine.context.module_name,
            }
        )

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
        await outbound.send(
            {
                "type": "character_list",
                **list_character_options(
                    engine.context.module_name,
                    context=engine.context,
                ),
            }
        )

    @router.handler("player_notes_update")
    async def handle_player_notes_update(data: dict) -> None:
        try:
            notes = PlayerNotesStore(engine.context.world_dir, user_id=user_id).save(
                data.get("text", ""),
                expected_revision=(
                    int(data["revision"]) if data.get("revision") is not None else None
                ),
            )
            await outbound.send({"type": "player_notes", "saved": True, **notes})
        except PlayerNotesConflict as exc:
            current = PlayerNotesStore(engine.context.world_dir, user_id=user_id).load()
            await outbound.send(
                {
                    "type": "player_notes_conflict",
                    "message": str(exc),
                    **current,
                }
            )
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            await outbound.send(
                {
                    "type": "player_notes_error",
                    "message": str(exc) or "玩家笔记保存失败",
                }
            )

    @router.handler("model_settings_update")
    async def handle_model_settings_update(data: dict) -> None:
        global _active_model_settings
        if not turn_gate.try_acquire_session():
            await outbound.send(
                {
                    "type": "model_settings_error",
                    "message": "当前回合尚未结束，请在本轮叙述完成后重试。",
                }
            )
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
            await outbound.send(
                {
                    **_model_settings_payload(settings),
                    "saved": True,
                }
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            await outbound.send(
                {
                    "type": "model_settings_error",
                    "message": f"模型设置保存失败：{exc}",
                }
            )
        finally:
            turn_gate.release_session()

    @router.handler("save")
    async def handle_save(data: dict) -> None:
        is_manual = data.get("manual", False)
        slot_id = None if is_manual else AUTO_SAVE_SLOT
        if turn_state_busy():
            await outbound.send(
                {
                    "type": "saved",
                    "ok": False,
                    "slot_id": slot_id or "",
                    "reason": "turn_in_progress",
                }
            )
            return
        sid = game_app.manage_saves.save(manual=bool(is_manual))
        await outbound.send({"type": "saved", "ok": True, "slot_id": sid})

    @router.handler("save_delete")
    async def handle_save_delete(data: dict) -> None:
        slot_id = data.get("slot_id", "")
        if slot_id == AUTO_SAVE_SLOT:
            await outbound.send(
                {
                    "type": "error",
                    "message": "自动存档不可手动删除。",
                }
            )
            return
        delete_save(slot_id, context=engine.context)
        await outbound.send({"type": "save_deleted", "slot_id": slot_id})

    @router.handler("save_create")
    async def handle_save_create(_data: dict) -> None:
        if turn_state_busy():
            await outbound.send(
                {
                    "type": "saved",
                    "ok": False,
                    "slot_id": "",
                    "reason": "turn_in_progress",
                }
            )
            return
        sid = game_app.manage_saves.create_slot()
        await outbound.send({"type": "saved", "ok": True, "slot_id": sid})

    @router.handler("save_rename")
    async def handle_save_rename(data: dict) -> None:
        from src.persistence import rename_save

        slot_id = data.get("slot_id", "")
        label = data.get("label", "")
        ok = rename_save(slot_id, label, context=engine.context)
        await outbound.send(
            {
                "type": "save_renamed",
                "slot_id": slot_id,
                "label": label,
                "ok": ok,
            }
        )

    @router.handler("settle_case")
    async def handle_settle_case(data: dict) -> None:
        if turn_state_busy():
            await outbound.send(
                {
                    "type": "error",
                    "message": "当前回合尚未结束，暂时不能结算案件。",
                }
            )
            return
        result = engine.settle_case(
            data.get("ending_type", "neutral"),
            data.get("title", "故事结束"),
            data.get("summary", ""),
        )
        await outbound.send({"type": "case_settled", **result})
        await outbound.send(
            {
                "type": "character_list",
                **list_character_options(
                    engine.context.module_name,
                    context=engine.context,
                ),
            }
        )
        await outbound.send({"type": "state"})

    @router.handler("load")
    async def handle_legacy_load(_data: dict) -> None:
        if turn_state_busy():
            await outbound.send(
                {
                    "type": "error",
                    "message": "当前回合尚未结束，暂时不能读档。",
                }
            )
            return
        try:
            count = engine.load()
        except StaleRevisionError as exc:
            await outbound.send({"type": "error", "message": str(exc)})
            return
        await outbound.send(
            {
                "type": "loaded",
                "ok": count is not None,
                "count": count or 0,
            }
        )

    @router.handler("state")
    async def handle_state(_data: dict) -> None:
        try:
            world_state = engine.context.world_store.load()
            pc_data = enrich_pc_for_frontend(world_state.get("pc", {}), engine.context)
            clues_data = _enrich_clues_for_frontend(
                world_state.get("clues_found", {}),
                world_state,
                engine.context,
            )
        except Exception:
            pc_data, clues_data = {}, {}
        await outbound.send(
            {
                "type": "state_data",
                "data": json.dumps(pc_data, ensure_ascii=False),
                "clues": json.dumps(clues_data, ensure_ascii=False),
            }
        )

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
            await outbound.send(
                {
                    "type": "turn_branch_failed",
                    "message": "缺少分支回合 ID",
                }
            )
            return
        try:
            branch = await asyncio.to_thread(
                WORLD_BRANCHES.create,
                engine.context,
                engine.turn_journal,
                turn_id,
                label=data.get("label", ""),
                user_id=user_id,
            )
            if auth_required() and user_id:
                with session_scope(DATABASE_URL) as db_session:
                    world = db_session.get(World, branch.context.world_id)
                    world.created_by = user_id
                    db_session.add(
                        WorldMember(
                            id=new_id("member"),
                            world_id=branch.context.world_id,
                            user_id=user_id,
                            role="owner",
                        )
                    )
                audit(
                    DATABASE_URL,
                    "world_branched",
                    user_id=user_id,
                    world_id=branch.context.world_id,
                    details={"source_turn_id": turn_id},
                )
            engine.switch_context(branch.context)
            resolve_speaker.clear()
            engine.adopt_message_history(branch.messages)
            turn_gate.rebind_world(_world_turn_lock(branch.context))
            _set_active_context(branch.context)
            history = engine.turn_journal.public_history()
            for turn in history:
                enriched = public_chat_events(turn)
                turn["narrative_segments"] = enriched
                turn["chat_events"] = enriched
        except Exception as exc:
            release_turn()
            await outbound.send(
                {
                    "type": "turn_branch_failed",
                    "source_turn_id": turn_id,
                    "message": str(exc) or "创建时间线分支失败",
                }
            )
            return
        release_turn()
        await outbound.send(
            {
                "type": "turn_branched",
                "source_turn_id": turn_id,
                "world_id": branch.context.world_id,
                "module_name": branch.context.module_name,
                "label": branch.label,
                "history": history,
            }
        )
        await outbound.send(world_context_payload())
        await outbound.send(world_list_payload())
        await outbound.send({"type": "save_list", "saves": engine.list_saves()})
        await send_character_state()

    @router.handler("world_switch")
    async def handle_world_switch(data: dict) -> None:
        if not turn_gate.try_acquire_session():
            await outbound.send(
                {
                    "type": "world_switch_failed",
                    "message": "当前回合尚未结束，暂时不能切换时间线。",
                }
            )
            return
        target_lock: threading.Lock | None = None
        target_lock_acquired = False
        try:
            target_world_id = str(data.get("world_id") or "")
            if auth_required():
                if not user_id:
                    raise PermissionError("未登录")
                authorize_world(DATABASE_URL, user_id, target_world_id, "play")
            context = WORLD_BRANCHES.open(target_world_id)
            target_lock = _world_turn_lock(context)
            if not target_lock.acquire(blocking=False):
                raise RuntimeError("目标时间线正在处理另一个回合，请稍后重试。")
            target_lock_acquired = True
            messages, _snapshot = load_game(AUTO_SAVE_SLOT, context=context)
            engine.switch_context(context)
            resolve_speaker.clear()
            if messages is not None:
                engine.adopt_message_history(messages)
            turn_gate.rebind_world(target_lock)
            _set_active_context(context)
            history = engine.turn_journal.public_history()
            for turn in history:
                enriched = public_chat_events(turn)
                turn["narrative_segments"] = enriched
                turn["chat_events"] = enriched
            if user_id:
                audit(
                    DATABASE_URL,
                    "world_switched",
                    user_id=user_id,
                    world_id=context.world_id,
                )
        except Exception as exc:
            await outbound.send(
                {
                    "type": "world_switch_failed",
                    "message": str(exc) or "切换时间线失败",
                }
            )
            return
        finally:
            if target_lock is not None and target_lock_acquired:
                target_lock.release()
            turn_gate.release_session()
        await outbound.send(
            {
                "type": "world_switched",
                "world_id": engine.context.world_id,
                "module_name": engine.context.module_name,
                "history": history,
            }
        )
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
            await outbound.send(
                {
                    "type": "turn_rewrite_failed",
                    "message": "缺少需要重新叙述的回合 ID",
                }
            )
            return
        await launch_rewrite(turn_id)

    @router.handler("switch_module")
    async def handle_switch_module(data: dict) -> None:
        if auth_required():
            await outbound.send(
                {
                    "type": "error",
                    "message": "账号模式下请先创建对应模组的新世界，再切换世界。",
                }
            )
            return
        name = data.get("module", engine.context.module_name)
        try:
            MODULE_REGISTRY.resolve(name)
        except FileNotFoundError:
            await outbound.send({"type": "error", "message": f"模组'{name}'不存在"})
            return
        if not turn_gate.try_acquire_session():
            await outbound.send(
                {
                    "type": "error",
                    "message": "当前回合尚未结束，暂时不能切换模组。",
                }
            )
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
            await outbound.send(
                {
                    "type": "module_list",
                    "modules": _list_mods(),
                    "active": name,
                }
            )
            await outbound.send(
                {
                    "type": "character_list",
                    **list_character_options(name, context=context),
                }
            )
            await outbound.send({"type": "save_list", "saves": engine.list_saves()})
        finally:
            turn_gate.release_session()

    @router.handler("start")
    async def handle_start(data: dict) -> None:
        if not await reserve_turn():
            return
        try:
            intent = game_app.start_game.execute(data.get("character_ref"))
            roster = data.get("_room_roster")
            active_investigator_id = str(data.get("_room_investigator_id") or "")
            if isinstance(roster, list) and roster:
                initialize_investigator_roster(
                    engine.context,
                    roster,
                    active_investigator_id=active_investigator_id,
                )
                engine._multiplayer_roster_active = True
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

    async def resume_game(
        slot_id: str | None,
        *,
        announce_loaded: bool,
        investigator_id: str | None = None,
    ) -> None:
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
            if investigator_id:
                activate_investigator(engine.context, investigator_id)
                engine._multiplayer_roster_active = True
            if announce_loaded:
                await outbound.send(
                    {
                        "type": "loaded",
                        "ok": True,
                        "slot_id": intent.slot_id or "",
                        "count": intent.loaded_message_count,
                    }
                )
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
        await resume_game(
            data.get("slot_id"),
            announce_loaded=False,
            investigator_id=data.get("_room_investigator_id"),
        )

    @router.handler("save_load")
    async def handle_save_load(data: dict) -> None:
        await resume_game(
            str(data.get("slot_id") or ""),
            announce_loaded=True,
            investigator_id=data.get("_room_investigator_id"),
        )

    @router.handler("action")
    async def handle_action(data: dict) -> None:
        if not await reserve_turn():
            return
        try:
            investigator_id = str(data.get("_room_investigator_id") or "")
            if investigator_id:
                activate_investigator(engine.context, investigator_id)
                engine._multiplayer_roster_active = True
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
    await outbound.send(
        {
            "type": "module_list",
            "modules": _list_mods(),
            "active": engine.context.module_name,
            "world_id": engine.context.world_id,
            "module_name": engine.context.module_name,
        }
    )
    await outbound.send(
        {
            "type": "character_list",
            **list_character_options(engine.context.module_name, context=engine.context),
        }
    )

    # 发送当前模组主题（electron 用 file:// 加载，fetch('/api/theme') 不可用，
    # 故主题也走 WS 下发）
    await outbound.send({"type": "theme", "theme": _load_theme(engine.context)})
    await outbound.send(
        _model_settings_payload(
            ModelSettings.validated(
                engine.narrative_model,
                engine.judgement_model,
            )
        )
    )

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
        on_speaker_segment=on_speaker_segment,
        on_narrative_segments=on_narrative_segments,
        on_performance=on_performance,
        on_private_event=on_private_event,
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
            await outbound.send(
                {
                    "type": "protocol_error",
                    "code": "unknown_message_type",
                    "message_type": routed.message_type,
                    "message": "客户端发送了当前服务端不支持的消息类型。",
                }
            )
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


@app.post("/api/auth/register", status_code=201)
async def register(data: dict, request: Request, response: Response):
    if os.environ.get("TRPG_ALLOW_REGISTRATION", "1").lower() not in {"1", "true", "yes", "on"}:
        return JSONResponse({"detail": "注册已关闭"}, status_code=403)
    ip = request.client.host if request.client else ""
    LOGIN_LIMITER.check(f"register:{ip}")
    user = create_user(DATABASE_URL, data.get("username"), data.get("password"))
    token = create_login_session(
        DATABASE_URL, user, user_agent=request.headers.get("user-agent", ""), ip_address=ip
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=auth_required(),
        samesite="lax",
        max_age=30 * 86400,
        path="/",
    )
    audit(DATABASE_URL, "register", user_id=user.id, ip_address=ip)
    return {"id": user.id, "username": user.username}


@app.post("/api/auth/login")
async def login(data: dict, request: Request, response: Response):
    ip = request.client.host if request.client else ""
    key = f"login:{ip}:{str(data.get('username') or '').lower()}"
    LOGIN_LIMITER.check(key)
    user = authenticate(DATABASE_URL, data.get("username"), data.get("password"))
    if user is None:
        audit(DATABASE_URL, "login", success=False, ip_address=ip)
        return JSONResponse({"detail": "用户名或密码错误"}, status_code=401)
    LOGIN_LIMITER.clear(key)
    token = create_login_session(
        DATABASE_URL, user, user_agent=request.headers.get("user-agent", ""), ip_address=ip
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=auth_required(),
        samesite="lax",
        max_age=30 * 86400,
        path="/",
    )
    audit(DATABASE_URL, "login", user_id=user.id, ip_address=ip)
    return {"id": user.id, "username": user.username}


@app.post("/api/auth/logout", status_code=204)
async def logout(request: Request, response: Response):
    user = request_user(request, DATABASE_URL)
    revoke_session(DATABASE_URL, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    if user:
        audit(DATABASE_URL, "logout", user_id=user.id)


@app.get("/api/auth/me")
async def current_user(request: Request):
    user = request_user(request, DATABASE_URL)
    if user is None:
        return JSONResponse({"detail": "未登录"}, status_code=401)
    return {"id": user.id, "username": user.username, "status": user.status}


@app.get("/api/worlds")
async def owned_worlds(request: Request):
    user = request_user(request, DATABASE_URL)
    if user is None:
        return JSONResponse({"detail": "未登录"}, status_code=401)
    with session_scope(DATABASE_URL) as session:
        rows = (
            session.query(World, WorldMember)
            .join(WorldMember, WorldMember.world_id == World.id)
            .filter(WorldMember.user_id == user.id, World.status == "active")
            .all()
        )
        return {
            "worlds": [
                {
                    "world_id": world.id,
                    "module": world.module_name,
                    "role": member.role,
                    "updated_at": world.updated_at.isoformat(),
                    "metadata": world.metadata_json,
                    "member_count": session.query(WorldMember)
                    .filter_by(world_id=world.id)
                    .count(),
                }
                for world, member in rows
            ]
        }


@app.post("/api/worlds", status_code=201)
async def create_world(data: dict, request: Request):
    user = request_user(request, DATABASE_URL)
    if user is None:
        return JSONResponse({"detail": "未登录"}, status_code=401)
    module = str(data.get("module") or DEFAULT_MODULE_NAME)
    MODULE_REGISTRY.resolve(module)
    world_id = f"world-{secrets.token_hex(12)}"
    context = RuntimeContext.create(
        world_id, module, project_root=PROJECT_ROOT, runtime_root=RUNTIME_ROOT
    )
    with session_scope(DATABASE_URL) as session:
        world = session.get(World, world_id)
        world.created_by = user.id
        world.metadata_json = {
            **dict(world.metadata_json or {}),
            "name": str(data.get("name") or "").strip()[:120]
            or f"{user.username} 的房间",
            "room_status": "lobby",
            "max_players": max(2, min(int(data.get("max_players") or 4), 4)),
        }
        session.add(
            WorldMember(id=new_id("member"), world_id=world_id, user_id=user.id, role="owner")
        )
    audit(DATABASE_URL, "world_created", user_id=user.id, world_id=world_id)
    return {"world_id": context.world_id, "module": module}


def _multiplayer_error(exc: MultiplayerError) -> JSONResponse:
    return JSONResponse(
        {"detail": exc.message, "code": exc.code}, status_code=exc.status_code
    )


@app.post("/api/worlds/{world_id}/invites", status_code=201)
async def create_world_invite(world_id: str, data: dict, request: Request):
    user = request_user(request, DATABASE_URL)
    if user is None:
        return JSONResponse({"detail": "未登录"}, status_code=401)
    try:
        result = create_invite(
            DATABASE_URL,
            world_id,
            user.id,
            role=str(data.get("role") or "player"),
            expires_in_hours=int(data.get("expires_in_hours") or 24),
            max_uses=int(data.get("max_uses") or 1),
        )
    except (TypeError, ValueError):
        return JSONResponse(
            {"detail": "邀请参数无效", "code": "invalid_invite"}, status_code=400
        )
    except MultiplayerError as exc:
        return _multiplayer_error(exc)
    audit(DATABASE_URL, "world_invite_created", user_id=user.id, world_id=world_id)
    return result


@app.get("/api/worlds/{world_id}/invites")
async def get_world_invites(world_id: str, request: Request):
    user = request_user(request, DATABASE_URL)
    if user is None:
        return JSONResponse({"detail": "未登录"}, status_code=401)
    try:
        return list_invites(DATABASE_URL, world_id, user.id)
    except MultiplayerError as exc:
        return _multiplayer_error(exc)


@app.delete("/api/worlds/{world_id}/invites/{invite_id}", status_code=204)
async def delete_world_invite(world_id: str, invite_id: str, request: Request):
    user = request_user(request, DATABASE_URL)
    if user is None:
        return JSONResponse({"detail": "未登录"}, status_code=401)
    try:
        revoke_invite(DATABASE_URL, world_id, invite_id, user.id)
    except MultiplayerError as exc:
        return _multiplayer_error(exc)
    audit(DATABASE_URL, "world_invite_revoked", user_id=user.id, world_id=world_id)


@app.post("/api/invites/{token}/accept")
async def join_world_by_invite(token: str, request: Request):
    user = request_user(request, DATABASE_URL)
    if user is None:
        return JSONResponse({"detail": "未登录"}, status_code=401)
    try:
        result = accept_invite(DATABASE_URL, token, user.id)
    except MultiplayerError as exc:
        return _multiplayer_error(exc)
    audit(
        DATABASE_URL,
        "world_invite_accepted",
        user_id=user.id,
        world_id=result["world_id"],
    )
    return result


@app.get("/api/worlds/{world_id}/members")
async def get_world_members(world_id: str, request: Request):
    user = request_user(request, DATABASE_URL)
    if user is None:
        return JSONResponse({"detail": "未登录"}, status_code=401)
    try:
        return room_members(DATABASE_URL, world_id, user.id)
    except MultiplayerError as exc:
        return _multiplayer_error(exc)


@app.get("/api/worlds/{world_id}/investigators/options")
async def get_world_investigator_options(world_id: str, request: Request):
    user = request_user(request, DATABASE_URL)
    if user is None:
        return JSONResponse({"detail": "未登录"}, status_code=401)
    try:
        authorize_world(DATABASE_URL, user.id, world_id, "read")
        with session_scope(DATABASE_URL) as db_session:
            world = db_session.get(World, world_id)
            if world is None or world.status != "active":
                raise MultiplayerError("world_not_found", "房间不存在", 404)
            module_name = world.module_name
        context = RuntimeContext.create(
            world_id,
            module_name,
            project_root=PROJECT_ROOT,
            runtime_root=RUNTIME_ROOT,
        )
        return list_character_options(module_name, context=context)
    except MultiplayerError as exc:
        return _multiplayer_error(exc)


@app.patch("/api/worlds/{world_id}/members/{target_user_id}")
async def patch_world_member(
    world_id: str, target_user_id: str, data: dict, request: Request
):
    user = request_user(request, DATABASE_URL)
    if user is None:
        return JSONResponse({"detail": "未登录"}, status_code=401)
    try:
        result = update_member_role(
            DATABASE_URL,
            world_id,
            target_user_id,
            user.id,
            str(data.get("role") or ""),
        )
    except MultiplayerError as exc:
        return _multiplayer_error(exc)
    room = await ROOM_MANAGER.get(world_id)
    if room is not None:
        await room.hub.update_user_role(target_user_id, result["role"])
        if result["role"] == "viewer":
            room.set_ready(target_user_id, False)
            if room.current_actor_user_id == target_user_id:
                room.assign_actor(room.owner_user_id)
                _persist_room_control(room)
        await _broadcast_room_state(room)
    audit(
        DATABASE_URL,
        "world_member_role_changed",
        user_id=user.id,
        world_id=world_id,
        details={"target_user_id": target_user_id, "role": result["role"]},
    )
    return result


@app.post("/api/worlds/{world_id}/owner")
async def transfer_world_owner(world_id: str, data: dict, request: Request):
    user = request_user(request, DATABASE_URL)
    if user is None:
        return JSONResponse({"detail": "未登录"}, status_code=401)
    target_user_id = str(data.get("user_id") or "")
    try:
        result = transfer_owner(
            DATABASE_URL,
            world_id,
            target_user_id,
            user.id,
        )
    except MultiplayerError as exc:
        return _multiplayer_error(exc)
    room = await ROOM_MANAGER.get(world_id)
    if room is not None:
        room.owner_user_id = target_user_id
        await room.hub.update_user_role(user.id, "player")
        await room.hub.update_user_role(target_user_id, "owner")
        if room.current_actor_user_id is None:
            room.assign_actor(target_user_id)
        _persist_room_control(room)
        await room.hub.broadcast(
            {
                "type": "owner_changed",
                "previous_owner_user_id": user.id,
                "owner_user_id": target_user_id,
            }
        )
        await _broadcast_room_state(room)
    audit(
        DATABASE_URL,
        "world_owner_transferred",
        user_id=user.id,
        world_id=world_id,
        details={"owner_user_id": target_user_id},
    )
    return result


@app.delete("/api/worlds/{world_id}/members/{target_user_id}", status_code=204)
async def delete_world_member(world_id: str, target_user_id: str, request: Request):
    user = request_user(request, DATABASE_URL)
    if user is None:
        return JSONResponse({"detail": "未登录"}, status_code=401)
    try:
        remove_member(DATABASE_URL, world_id, target_user_id, user.id)
    except MultiplayerError as exc:
        return _multiplayer_error(exc)
    room = await ROOM_MANAGER.get(world_id)
    if room is not None:
        room.set_ready(target_user_id, False)
        if room.current_actor_user_id == target_user_id:
            room.assign_actor(room.owner_user_id)
            _persist_room_control(room)
        await room.hub.disconnect_user(target_user_id)
        room.connected_users.pop(target_user_id, None)
        await room.hub.broadcast(
            {"type": "member_removed", "user_id": target_user_id}
        )
        await _broadcast_room_state(room)
    audit(
        DATABASE_URL,
        "world_member_removed",
        user_id=user.id,
        world_id=world_id,
        details={"target_user_id": target_user_id},
    )


@app.post("/api/worlds/{world_id}/investigators/claim")
async def claim_world_investigator(world_id: str, data: dict, request: Request):
    user = request_user(request, DATABASE_URL)
    if user is None:
        return JSONResponse({"detail": "未登录"}, status_code=401)
    character_key = str(data.get("character_key") or "")
    try:
        with session_scope(DATABASE_URL) as db_session:
            world = db_session.get(World, world_id)
            if world is None:
                raise MultiplayerError("world_not_found", "房间不存在", 404)
            module_name = world.module_name
        context = RuntimeContext.create(
            world_id,
            module_name,
            project_root=PROJECT_ROOT,
            runtime_root=RUNTIME_ROOT,
        )
        options = list_character_options(module_name, context=context)
        selected = next(
            (
                character
                for group in options.get("groups", [])
                for character in group.get("characters", [])
                if str(character.get("id") or "") == character_key
            ),
            None,
        )
        if selected is None or not isinstance(selected.get("ref"), dict):
            raise MultiplayerError("invalid_character", "调查员不在当前房间的角色列表中")
        result = claim_investigator(
            DATABASE_URL,
            world_id,
            character_key,
            user.id,
            character_ref=selected["ref"],
        )
    except MultiplayerError as exc:
        return _multiplayer_error(exc)
    audit(
        DATABASE_URL,
        "investigator_claimed",
        user_id=user.id,
        world_id=world_id,
        details={"investigator_id": result["id"]},
    )
    return result


@app.delete(
    "/api/worlds/{world_id}/investigators/{investigator_id}/claim",
    status_code=204,
)
async def release_world_investigator(
    world_id: str, investigator_id: str, request: Request
):
    user = request_user(request, DATABASE_URL)
    if user is None:
        return JSONResponse({"detail": "未登录"}, status_code=401)
    try:
        release_investigator(DATABASE_URL, world_id, investigator_id, user.id)
    except MultiplayerError as exc:
        return _multiplayer_error(exc)
    audit(
        DATABASE_URL,
        "investigator_released",
        user_id=user.id,
        world_id=world_id,
        details={"investigator_id": investigator_id},
    )


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
    return JSONResponse(
        {
            "ok": False,
            "error_code": exc.code,
            "error": exc.message,
            "details": exc.details,
        },
        status_code=status,
    )


async def _receive_module_upload(request: Request) -> Path:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_PACKAGE_BYTES:
                raise ModulePackageError("package_too_large", "模组包超过 64 MiB 上限")
        except ValueError as exc:
            raise ModulePackageError("invalid_length", "Content-Length 无效") from exc

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
        return JSONResponse(
            {
                "ok": True,
                "already_installed": already_installed,
                "module": record.to_dict(),
                "inspection": inspection.summary(),
            },
            status_code=200 if already_installed else 201,
        )
    except ModulePackageError as exc:
        return _module_error_response(exc)
    finally:
        if "path" in locals():
            path.unlink(missing_ok=True)


@app.get("/api/characters")
async def list_characters():
    """列出可用于当前模组的新游戏调查员。"""
    return list_character_options(_active_context.module_name, context=_active_context)


@app.post("/api/modules/switch")
async def switch_module(data: dict):
    """切换活跃模组"""
    if auth_required():
        return JSONResponse(
            {"detail": "账号模式下请创建对应模组的新世界"}, status_code=409
        )
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
    try:
        validate_websocket_origin(ws)
        requested_world = ws.query_params.get("world_id")
        requested_module = ws.query_params.get("module") or _active_context.module_name
        user = websocket_user(ws, DATABASE_URL)
        if auth_required():
            if user is None:
                await ws.close(code=4401, reason="未登录或会话已过期")
                return
            effective_world = requested_world or _active_context.world_id
            authorize_world(DATABASE_URL, user.id, effective_world, "play")
            with session_scope(DATABASE_URL) as session:
                world = session.get(World, effective_world)
                if world is None or world.status != "active":
                    await ws.close(code=4404, reason="世界不存在")
                    return
                requested_world = world.id
                requested_module = world.module_name
        await ws.accept()
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
        if ws.client_state.name == "CONNECTED":
            await ws.send_json({"type": "error", "message": f"游戏引擎初始化失败：{e}"})
            await ws.close()
        else:
            await ws.close(code=4403, reason="连接被拒绝")
        return
    await run_ws_session(ws, engine, user_id=user.id if user else None)


async def _room_bootstrap(ws: WebSocket, room: GameRoom) -> None:
    engine = room.engine
    await ws.send_json(
        {
            "type": "module_list",
            "modules": _list_mods(),
            "active": engine.context.module_name,
            "world_id": engine.context.world_id,
            "module_name": engine.context.module_name,
        }
    )
    await ws.send_json(
        {
            "type": "character_list",
            **list_character_options(engine.context.module_name, context=engine.context),
        }
    )
    await ws.send_json({"type": "theme", "theme": _load_theme(engine.context)})
    await ws.send_json(
        _model_settings_payload(
            ModelSettings.validated(engine.narrative_model, engine.judgement_model)
        )
    )
    await ws.send_json({"type": "save_list", "saves": engine.list_saves()})


async def _room_full_recovery_payload(room: GameRoom) -> dict:
    """Build a public-only recovery image after restart or replay-buffer gaps."""
    try:
        history = room.engine.turn_journal.public_history()
    except Exception:
        history = []
    try:
        state = room.engine.context.world_store.load()
        investigators = public_investigator_roster(state)
        active_investigator_id = state.get("active_investigator_id")
    except Exception:
        investigators = []
        active_investigator_id = None
    return {
        "type": "room_full_state",
        "status": room.status,
        "owner_user_id": room.owner_user_id,
        "current_actor_user_id": room.current_actor_user_id,
        "ready_user_ids": sorted(room.ready_users),
        "online_user_ids": sorted(room.connected_users),
        "latest_event_id": await room.hub.latest_event_id(),
        "history": history,
        "investigators": investigators,
        "active_investigator_id": active_investigator_id,
    }


async def _broadcast_room_state(room: GameRoom) -> None:
    connections = await room.hub.connection_snapshot()
    await room.hub.broadcast(
        {
            "type": "room_state",
            "status": room.status,
            "owner_user_id": room.owner_user_id,
            "current_actor_user_id": room.current_actor_user_id,
            "ready_user_ids": sorted(room.ready_users),
            "online_user_ids": sorted({item["user_id"] for item in connections}),
        }
    )


def _persist_room_control(room: GameRoom) -> None:
    with session_scope(DATABASE_URL) as db_session:
        world = db_session.get(World, room.world_id)
        if world is None:
            return
        metadata = dict(world.metadata_json or {})
        metadata["room_status"] = room.status
        metadata["current_actor_user_id"] = room.current_actor_user_id
        world.metadata_json = metadata
        world.updated_at = utcnow()


def _room_roster(world_id: str) -> tuple[list[dict], set[str]]:
    """Return claimed investigators and every member required to be ready."""
    with session_scope(DATABASE_URL) as db_session:
        playable_members = {
            member.user_id
            for member in db_session.query(WorldMember)
            .filter(WorldMember.world_id == world_id)
            .all()
            if member.role in {"owner", "player"}
        }
        claims = (
            db_session.query(WorldInvestigator)
            .filter_by(world_id=world_id, status="claimed")
            .all()
        )
        roster = [
            {
                "investigator_id": claim.id,
                "user_id": claim.controller_user_id,
                "character_ref": dict(claim.character_ref or {}),
            }
            for claim in claims
            if claim.controller_user_id in playable_members
        ]
    return roster, playable_members


def _room_investigator_id(world_id: str, user_id: str) -> str | None:
    with session_scope(DATABASE_URL) as db_session:
        claim = (
            db_session.query(WorldInvestigator)
            .filter_by(
                world_id=world_id,
                controller_user_id=user_id,
                status="claimed",
            )
            .one_or_none()
        )
        return claim.id if claim is not None else None


async def _retire_room_after_grace(
    world_id: str, room: GameRoom, idle_seconds: float
) -> None:
    await asyncio.sleep(idle_seconds)
    if await ROOM_MANAGER.remove_if_idle(world_id, idle_seconds=idle_seconds):
        if room.driver_transport is not None:
            await room.driver_transport.close_input()
        if room.driver_task is not None:
            try:
                await room.driver_task
            except (RuntimeError, asyncio.CancelledError):
                pass


@app.websocket("/ws/room")
async def multiplayer_room_ws(ws: WebSocket):
    """Join one authoritative shared engine using the authenticated user session."""
    try:
        validate_websocket_origin(ws)
        user = websocket_user(ws, DATABASE_URL)
        if user is None:
            await ws.close(code=4401, reason="未登录或会话已过期")
            return
        world_id = str(ws.query_params.get("world_id") or "")
        if not world_id:
            await ws.close(code=4400, reason="缺少房间 ID")
            return
        role = authorize_world(DATABASE_URL, user.id, world_id, "read")
        with session_scope(DATABASE_URL) as db_session:
            world = db_session.get(World, world_id)
            if world is None or world.status != "active":
                await ws.close(code=4404, reason="房间不存在")
                return
            module_name = world.module_name
            owner_member = (
                db_session.query(WorldMember)
                .filter_by(world_id=world_id, role="owner")
                .one_or_none()
            )
            if owner_member is None:
                await ws.close(code=4403, reason="房间没有有效房主")
                return
            owner_user_id = owner_member.user_id

        def create_room() -> GameRoom:
            context = RuntimeContext.create(
                world_id,
                module_name,
                project_root=PROJECT_ROOT,
                runtime_root=RUNTIME_ROOT,
            )
            engine = GameEngine(context)
            engine.configure_models(
                _active_model_settings.narrative_model,
                _active_model_settings.judgement_model,
            )
            engine.prepare_session()
            room = GameRoom(
                world_id,
                engine,
                RoomEventHub(world_id),
                owner_user_id,
                current_actor_user_id=(
                    str((world.metadata_json or {}).get("current_actor_user_id") or "")
                    or owner_user_id
                ),
                status=str((world.metadata_json or {}).get("room_status") or "lobby"),
            )
            transport = RoomDriverTransport(room)
            room.driver_transport = transport
            return room

        room, created = await ROOM_MANAGER.get_or_create(world_id, create_room)
        await ws.accept()
        connection_id = f"connection_{secrets.token_hex(12)}"
        await room.hub.attach(RoomConnection(connection_id, user.id, role, ws))
        room.member_connected(user.id)
        if created:
            room.driver_task = asyncio.create_task(
                run_ws_session(room.driver_transport, room.engine, user_id=owner_user_id)
            )
        else:
            await _room_bootstrap(ws, room)
        await ws.send_json(await _room_full_recovery_payload(room))
        await room.hub.broadcast(
            {
                "type": "member_joined",
                "user_id": user.id,
                "role": role,
            }
        )
        await _broadcast_room_state(room)
    except Exception as exc:
        if ws.client_state.name == "CONNECTED":
            await ws.close(code=4403, reason=str(exc)[:120] or "连接被拒绝")
        return

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            try:
                role = authorize_world(DATABASE_URL, user.id, world_id, "read")
            except Exception:
                await ws.close(code=4403, reason="房间成员权限已被移除")
                break
            message_type = str(data.get("type") or "")
            if message_type == "ping":
                await ws.send_json({"type": "pong"})
                continue
            if message_type == "room_ack":
                accepted = await room.hub.acknowledge(
                    connection_id, int(data.get("event_id") or 0)
                )
                if not accepted:
                    await ws.send_json(
                        {"type": "protocol_error", "code": "invalid_room_ack"}
                    )
                continue
            if message_type == "room_sync":
                replay = await room.hub.replay_after(
                    connection_id, int(data.get("after_event_id") or 0)
                )
                if replay["gap"]:
                    await ws.send_json(
                        {
                            "type": "room_event_gap",
                            "latest_event_id": replay["latest_event_id"],
                        }
                    )
                    await ws.send_json(await _room_full_recovery_payload(room))
                else:
                    for event in replay["events"]:
                        await ws.send_json(event)
                continue
            if message_type == "room_ready":
                if role == "viewer":
                    await ws.send_json(
                        {
                            "type": "room_action_rejected",
                            "code": "player_required",
                            "message": "旁观者不能准备游戏",
                        }
                    )
                    continue
                room.set_ready(user.id, bool(data.get("ready", True)))
                await _broadcast_room_state(room)
                continue
            if message_type == "actor_assign":
                if role != "owner":
                    await ws.send_json(
                        {
                            "type": "room_action_rejected",
                            "code": "owner_required",
                            "message": "只有房主可以指定行动者",
                        }
                    )
                    continue
                target_user_id = str(data.get("user_id") or "")
                member_state = room_members(DATABASE_URL, world_id, user.id)
                eligible = {
                    member["user_id"]
                    for member in member_state["members"]
                    if member["role"] in {"owner", "player"}
                }
                if target_user_id not in eligible:
                    await ws.send_json(
                        {
                            "type": "room_action_rejected",
                            "code": "invalid_actor",
                            "message": "目标成员不能成为行动者",
                        }
                    )
                    continue
                room.assign_actor(target_user_id)
                _persist_room_control(room)
                await room.hub.broadcast(
                    {"type": "actor_changed", "user_id": target_user_id}
                )
                await _broadcast_room_state(room)
                continue
            if message_type in {"player_notes_get", "player_notes_update"}:
                notes_store = PlayerNotesStore(
                    room.engine.context.world_dir,
                    user_id=user.id,
                )
                try:
                    if message_type == "player_notes_get":
                        payload = {"type": "player_notes", **notes_store.load()}
                    else:
                        saved = notes_store.save(
                            data.get("text", ""),
                            expected_revision=(
                                int(data["revision"])
                                if data.get("revision") is not None
                                else None
                            ),
                        )
                        payload = {"type": "player_notes", "saved": True, **saved}
                except PlayerNotesConflict as exc:
                    payload = {
                        "type": "player_notes_conflict",
                        "message": str(exc),
                        **notes_store.load(),
                    }
                except (OSError, TypeError, ValueError, RuntimeError) as exc:
                    payload = {
                        "type": "player_notes_error",
                        "message": str(exc) or "玩家笔记保存失败",
                    }
                await ws.send_json(payload)
                continue
            if message_type == "state":
                try:
                    world_state = room.engine.context.world_store.load()
                    controllers = world_state.get("investigator_controllers", {})
                    investigators = world_state.get("investigators", {})
                    investigator_id = (
                        controllers.get(user.id) if isinstance(controllers, dict) else None
                    )
                    own_pc = (
                        investigators.get(investigator_id, {})
                        if isinstance(investigators, dict)
                        else {}
                    )
                    if not own_pc and user.id == room.owner_user_id:
                        own_pc = world_state.get("pc", {})
                    pc_data = enrich_pc_for_frontend(
                        own_pc if isinstance(own_pc, dict) else {},
                        room.engine.context,
                    )
                    clues_data = _enrich_clues_for_frontend(
                        visible_clues_for_investigator(
                            world_state.get("clues_found", {}),
                            investigator_id,
                        ),
                        world_state,
                        room.engine.context,
                    )
                except Exception:
                    pc_data, clues_data = {}, {}
                await ws.send_json(
                    {
                        "type": "state_data",
                        "data": json.dumps(pc_data, ensure_ascii=False),
                        "clues": json.dumps(clues_data, ensure_ascii=False),
                    }
                )
                continue
            if message_type == "turn_recovery_get":
                await ws.send_json(await _room_full_recovery_payload(room))
                continue
            if message_type in {"suggest_reply", "decision_reply"}:
                if room.current_actor_user_id != user.id:
                    await ws.send_json(
                        {
                            "type": "room_action_rejected",
                            "code": "not_current_actor",
                            "message": "只有当前行动者可以作出这个决定",
                        }
                    )
                    continue
            owner_control_types = {
                "model_settings_update",
                "save",
                "save_delete",
                "save_create",
                "save_rename",
                "settle_case",
                "load",
                "turn_branch_create",
                "world_switch",
                "switch_module",
            }
            if message_type in owner_control_types and role != "owner":
                await ws.send_json(
                    {
                        "type": "room_action_rejected",
                        "code": "owner_required",
                        "message": "只有房主可以执行房间管理操作",
                    }
                )
                continue
            mutating_turn_types = {
                "start",
                "continue",
                "save_load",
                "action",
                "turn_rewrite",
            }
            if message_type in mutating_turn_types:
                if message_type == "start" and role != "owner":
                    await ws.send_json(
                        {
                            "type": "room_action_rejected",
                            "code": "owner_required",
                            "message": "只有房主可以开始游戏",
                        }
                    )
                    continue
                roster, playable_members = _room_roster(world_id)
                claims_by_user = {
                    str(item["user_id"]): item for item in roster if item.get("user_id")
                }
                actor_id = room.current_actor_user_id or user.id
                actor_claim = claims_by_user.get(actor_id)
                if actor_claim is None:
                    await ws.send_json(
                        {
                            "type": "room_action_rejected",
                            "code": "investigator_required",
                            "message": "当前行动者需要先选择调查员",
                        }
                    )
                    continue
                if message_type == "start":
                    missing_claims = sorted(playable_members - claims_by_user.keys())
                    missing_ready = sorted(playable_members - room.ready_users)
                    if missing_claims or missing_ready:
                        await ws.send_json(
                            {
                                "type": "room_action_rejected",
                                "code": "room_not_ready",
                                "message": "所有玩家选择调查员并准备后才能开始",
                                "missing_claim_user_ids": missing_claims,
                                "missing_ready_user_ids": missing_ready,
                            }
                        )
                        continue
                action_id = str(data.get("action_id") or "")
                try:
                    await room.reserve_action(user.id, action_id)
                except ActionReservationError as exc:
                    await ws.send_json(
                        {
                            "type": "room_action_rejected",
                            "code": exc.code,
                            "message": str(exc),
                        }
                    )
                    continue
                try:
                    reserve_room_action(
                        DATABASE_URL,
                        world_id,
                        action_id,
                        user.id,
                        message_type,
                    )
                except MultiplayerError as exc:
                    room.release_action()
                    await ws.send_json(
                        {
                            "type": "room_action_rejected",
                            "code": exc.code,
                            "message": str(exc),
                        }
                    )
                    continue
                if message_type == "start":
                    room.status = "playing"
                    _persist_room_control(room)
                    await _broadcast_room_state(room)
                    data["character_ref"] = actor_claim["character_ref"]
                    data["_room_roster"] = roster
                data["_room_investigator_id"] = actor_claim["investigator_id"]
            data["_room_user_id"] = user.id
            data["_room_connection_id"] = connection_id
            try:
                await room.driver_transport.submit(json.dumps(data, ensure_ascii=False))
            except Exception:
                room.release_action()
                raise
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await room.hub.detach(connection_id)
        room.member_disconnected(user.id)
        if user.id not in room.connected_users:
            await room.hub.broadcast({"type": "member_left", "user_id": user.id})
        await _broadcast_room_state(room)
        if not room.connected_users:
            idle_seconds = max(
                0.0, float(os.environ.get("TRPG_ROOM_IDLE_SECONDS", "30"))
            )
            asyncio.create_task(
                _retire_room_after_grace(world_id, room, idle_seconds)
            )


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
