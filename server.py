#!/usr/bin/env python3
# ruff: noqa: E402
"""TRPG Agent WebSocket 服务器 —— GameEngine + FastAPI"""

import asyncio
import json
import os
import runpy
import secrets
import sys
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
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from src.asset_payload import (
    SpeakerPayloadResolver,
    asset_payload,
    enrich_narrative_segments,
    enrich_pc_for_frontend,
)
from src.auth import (
    audit,
    auth_required,
    authorize_world,
    request_user,
    validate_websocket_origin,
    websocket_user,
)
from src.auth_http import AuthHttpDependencies, create_auth_router
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
    WorldMember,
    database_url,
    new_id,
    session_scope,
)
from src.editor_api import create_editor_router
from src.editor_projects import EditorProjectStore
from src.engine import EngineCallbacks, GameEngine
from src.event_stream import OrderedTurnEventStream
from src.frontend_payload import enrich_clues_for_frontend
from src.game_application import (
    ApplicationUseCaseError,
    GameApplication,
    SaveNotFoundError,
)
from src.investigators import (
    activate_investigator,
    initialize_investigator_roster,
    public_investigator_roster,
)
from src.model_settings import ModelSettings, persist_model_settings
from src.module_http import (
    ModuleHttpDependencies,
    create_module_http_router,
    serve_module_asset,
)
from src.module_registry import ModuleRegistry
from src.multiplayer_http import (
    MultiplayerHttpDependencies,
    create_multiplayer_http_router,
)
from src.multiplayer_private_state import reconcile_world_investigator_roster
from src.multiplayer_ws import MultiplayerWsController, MultiplayerWsDependencies
from src.narrative_history import enrich_public_history_record
from src.persistence import delete_save, load_game
from src.player_notes import PlayerNotesConflict, PlayerNotesStore
from src.room_runtime import ActionReservationError, GameRoom, RoomManager
from src.runtime import RuntimeContext
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
ROOM_MANAGER = RoomManager(max_rooms=max(1, int(os.environ.get("TRPG_MAX_ACTIVE_ROOMS", "8"))))
app.include_router(
    create_auth_router(
        AuthHttpDependencies(
            lambda: DATABASE_URL,
            disconnect_session=lambda session_hash: ROOM_MANAGER.disconnect_session(
                session_hash
            ),
        )
    )
)
MULTIPLAYER_WS = MultiplayerWsController(
    MultiplayerWsDependencies(
        database_url=lambda: DATABASE_URL,
        room_manager=lambda: ROOM_MANAGER,
        active_model_settings=lambda: _active_model_settings,
        engine_factory=lambda context: GameEngine(context),
        run_ws_session=lambda *args, **kwargs: run_ws_session(*args, **kwargs),
        list_modules=lambda: _list_mods(),
        load_theme=lambda context: _load_theme(context),
        model_settings_payload=lambda settings: _model_settings_payload(settings),
        enrich_clues=lambda clues, state, context: enrich_clues_for_frontend(clues, state, context),
        project_root=PROJECT_ROOT,
        runtime_root=RUNTIME_ROOT,
    )
)
app.include_router(MULTIPLAYER_WS.router())
app.include_router(
    create_multiplayer_http_router(
        MultiplayerHttpDependencies(
            database_url=lambda: DATABASE_URL,
            room_manager=lambda: ROOM_MANAGER,
            module_registry=MODULE_REGISTRY,
            default_module_name=DEFAULT_MODULE_NAME,
            project_root=PROJECT_ROOT,
            runtime_root=RUNTIME_ROOT,
            persist_room_control=MULTIPLAYER_WS.persist_room_control,
            broadcast_room_state=MULTIPLAYER_WS.broadcast_room_state,
        )
    )
)


def _member_mutation_target(request: Request) -> tuple[str, str | None] | None:
    """Return the room/member addressed by a role-change or removal request."""
    parts = request.url.path.strip("/").split("/")
    if (
        request.method in {"PATCH", "DELETE"}
        and len(parts) == 5
        and parts[:2] == ["api", "worlds"]
        and parts[3] == "members"
    ):
        return parts[2], parts[4]
    if (
        request.method == "POST"
        and len(parts) == 4
        and parts[:2] == ["api", "worlds"]
        and parts[3] == "owner"
    ):
        # Ownership transfer demotes the existing owner. Resolve that user from
        # the live room rather than trusting a client body.
        return parts[2], None
    return None


async def _reserve_current_actor_member_mutation(
    request: Request,
) -> tuple[GameRoom | None, JSONResponse | None]:
    """Serialize actor demotion/removal against an authoritative room turn."""
    target = _member_mutation_target(request)
    if target is None:
        return None, None
    world_id, target_user_id = target
    room = await ROOM_MANAGER.get(world_id)
    target_user_id = target_user_id or (room.owner_user_id if room is not None else "")
    if room is None or target_user_id not in room.protected_member_user_ids():
        return None, None
    combat_member = bool(
        room.driver_transport
        and target_user_id
        in room.driver_transport.combat_participant_controllers()
    )
    if MULTIPLAYER_WS.room_control_change_blocked(room) or combat_member:
        return None, JSONResponse(
            {
                "detail": "当前回合或确认请求结束前不能更换、降级或移除行动者",
                "code": "room_turn_in_progress",
            },
            status_code=409,
        )
    try:
        await room.reserve_action(
            target_user_id,
            f"member-mutation:{secrets.token_hex(12)}",
            require_current_actor=False,
        )
    except ActionReservationError:
        return None, JSONResponse(
            {
                "detail": "当前回合或确认请求结束前不能更换、降级或移除行动者",
                "code": "room_turn_in_progress",
            },
            status_code=409,
        )
    # Actor assignment and pending prompts are also event-loop operations. Check
    # again after acquiring the room lease so a mutation cannot cross their edge.
    if target_user_id not in room.protected_member_user_ids():
        room.release_action()
        return None, None
    if room.pending_reply_kind is not None:
        room.release_action()
        return None, JSONResponse(
            {
                "detail": "当前回合或确认请求结束前不能更换、降级或移除行动者",
                "code": "room_turn_in_progress",
            },
            status_code=409,
        )
    return room, None


@app.middleware("http")
async def authentication_gate(request: Request, call_next):
    """Cloud mode protects every API except health and authentication."""
    public = (
        request.url.path
        in {
            "/api/health",
            "/api/ready",
            "/api/auth/register",
            "/api/auth/login",
        }
        or request.url.path == "/"
    )
    actor_mutation_room: GameRoom | None = None
    if auth_required() and request.url.path.startswith("/api/") and not public:
        user = request_user(request, DATABASE_URL)
        if user is None:
            return JSONResponse({"detail": "未登录或会话已过期"}, status_code=401)
        authoring_request = request.url.path.startswith("/api/editor/") or (
            request.method == "POST"
            and request.url.path
            in {
                "/api/modules/compile",
                "/api/modules/inspect",
                "/api/modules/import",
            }
        )
        if authoring_request:
            administrators = {
                item.strip().lower()
                for item in os.environ.get("TRPG_ADMIN_USERS", "").split(",")
                if item.strip()
            }
            if user.username.lower() not in administrators:
                return JSONResponse(
                    {"detail": "云端模组编辑与导入仅限管理员"},
                    status_code=403,
                )
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
        actor_mutation_room, rejection = await _reserve_current_actor_member_mutation(request)
        if rejection is not None:
            return rejection
        request.state.user = user
    try:
        return await call_next(request)
    finally:
        if actor_mutation_room is not None:
            actor_mutation_room.release_action()


def _world_turn_lock(context: RuntimeContext) -> threading.Lock:
    key = f"{context.runtime_root.resolve()}::{context.world_id}"
    with _world_turn_locks_guard:
        return _world_turn_locks.setdefault(key, threading.Lock())


def _set_active_context(context: RuntimeContext) -> None:
    global _active_context
    _active_context = context


app.include_router(
    create_module_http_router(
        ModuleHttpDependencies(
            registry=lambda: MODULE_REGISTRY,
            project_root=PROJECT_ROOT,
            runtime_root=lambda: RUNTIME_ROOT,
            active_context=lambda: _active_context,
            set_active_context=_set_active_context,
            auth_required=lambda: auth_required(),
        )
    )
)


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
    room_start_guard = threading.Lock()
    room_start_pending = False

    def public_chat_events(record: dict) -> list[dict]:
        """Enrich saved segments and repair legacy/all-narration attribution."""
        public = enrich_public_history_record(
            record,
            engine,
            resolve_speaker=resolve_speaker,
        )
        record.clear()
        record.update(public)
        return public["narrative_segments"]

    def turn_state_busy() -> bool:
        return session.turn_busy

    def release_turn() -> None:
        session.release_turn()

    def mark_room_start_pending() -> None:
        """Track only starts submitted by the authoritative room controller."""
        nonlocal room_start_pending
        room = getattr(ws, "room", None)
        if room is None or getattr(room, "status", None) != "starting":
            return
        with room_start_guard:
            room_start_pending = True

    def finish_room_start(success: bool) -> bool:
        """Atomically promote a committed opening or make a failed one retryable."""
        nonlocal room_start_pending
        with room_start_guard:
            if not room_start_pending:
                return False
            room_start_pending = False
        room = getattr(ws, "room", None)
        if room is None:
            return False
        MULTIPLAYER_WS.set_room_status(room, "playing" if success else "lobby")
        outbound.emit(MULTIPLAYER_WS.room_state_payload(room))
        return True

    def run_reserved_turn(coro_fn, *args, room_start: bool = False):
        """Run a previously reserved turn in the executor."""

        def _wrapped():
            try:
                coro_fn(*args)
            except Exception as e:
                finish_room_start(False)
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
                        "terminal": True,
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
                rolled_back_start = finish_room_start(False)
                if room_start and rolled_back_start:
                    emit(
                        {
                            "type": "error",
                            "message": "开场未能完成，房间已恢复到大厅，请重试。",
                            "terminal": True,
                        }
                    )
                # GameEngine deliberately swallows a cancelled turn after marking
                # its journal record.  An opening still needs a terminal wire
                # event so RoomDriverTransport releases the room reservation.
                if room_start and outbound.has_active_turn:
                    outbound.end_turn()
                release_turn()

        loop.run_in_executor(None, _wrapped)

    async def launch_reserved_turn(
        coro_fn,
        *args,
        turn_kind: str,
        player_input: str | None = None,
        actor: dict | None = None,
    ) -> None:
        """Create the lifecycle only after the session reservation succeeded."""
        try:
            turn_id = engine.begin_turn_record(
                kind=turn_kind,
                player_input=player_input,
                actor=actor,
            )
            turn_record = engine.turn_journal.read(turn_id)
            metadata = {"parent_turn_id": turn_record.get("parent_turn_id")}
            if player_input:
                metadata["player_input"] = player_input
                if actor:
                    metadata["actor"] = actor
            await outbound.begin_turn(
                turn_id,
                metadata=metadata,
            )
        except Exception:
            finish_room_start(False)
            engine.finish_turn_record(
                status="failed",
                error="回合生命周期启动失败",
            )
            release_turn()
            raise
        run_reserved_turn(
            coro_fn,
            *args,
            room_start=(turn_kind == "opening" and getattr(ws, "room", None) is not None),
        )

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

    async def send_character_state(target_user_id: str | None = None):
        """在进入叙述前同步角色栏，不提前发送线索。"""
        try:
            pc_data = engine.context.world_store.load().get("pc", {})
            pc_data = enrich_pc_for_frontend(pc_data, engine.context)
        except Exception:
            pc_data = {}
        payload = {
            "type": "character_state",
            "data": json.dumps(pc_data, ensure_ascii=False),
        }
        if target_user_id:
            payload["target_user_id"] = target_user_id
        await outbound.send(payload)

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
                "responding_investigator_id": info.get("responding_investigator_id"),
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
        finish_room_start(True)
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
        # This callback also carries recoverable model-stream warnings. Only a
        # terminal protocol event may release or roll back a room action.
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
                    include_personal=not auth_required(),
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
                    "terminal": True,
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
                    "terminal": True,
                }
            )
            return
        result = engine.settle_case(
            data.get("ending_type", "neutral"),
            data.get("title", "故事结束"),
            data.get("summary", ""),
            persist_profile=not auth_required(),
        )
        await outbound.send({"type": "case_settled", **result})
        await outbound.send(
            {
                "type": "character_list",
                **list_character_options(
                    engine.context.module_name,
                    context=engine.context,
                    include_personal=not auth_required(),
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
            await outbound.send({"type": "error", "message": str(exc), "terminal": True})
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
            clues_data = enrich_clues_for_frontend(
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
                    **list_character_options(
                        name,
                        context=context,
                        include_personal=not auth_required(),
                    ),
                }
            )
            await outbound.send({"type": "save_list", "saves": engine.list_saves()})
        finally:
            turn_gate.release_session()

    @router.handler("start")
    async def handle_start(data: dict) -> None:
        if not await reserve_turn():
            return
        mark_room_start_pending()
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
            finish_room_start(False)
            release_turn()
            await outbound.send({"type": "error", "message": str(exc), "terminal": True})
            return
        except Exception as exc:
            finish_room_start(False)
            release_turn()
            print(
                f"[room] 开场初始化失败: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            await outbound.send(
                {
                    "type": "error",
                    "message": "开场初始化失败，房间已恢复到大厅，请重试。",
                    "terminal": True,
                }
            )
            return
        try:
            await send_character_state(data.get("_room_actor_user_id"))
        except Exception as exc:
            finish_room_start(False)
            release_turn()
            print(
                f"[room] 开场角色同步失败: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            await outbound.send(
                {
                    "type": "error",
                    "message": "开场初始化失败，房间已恢复到大厅，请重试。",
                    "terminal": True,
                }
            )
            return
        try:
            await launch_reserved_turn(
                engine.handle_action,
                intent.engine_input,
                turn_kind=intent.kind,
            )
        except Exception as exc:
            finish_room_start(False)
            print(
                f"[room] 开场回合启动失败: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            await outbound.send(
                {
                    "type": "error",
                    "message": "开场启动失败，房间已恢复到大厅，请重试。",
                    "terminal": True,
                }
            )

    async def resume_game(
        slot_id: str | None,
        *,
        announce_loaded: bool,
        investigator_id: str | None = None,
        target_user_id: str | None = None,
    ) -> None:
        if not await reserve_turn():
            return
        try:
            intent = game_app.resume_game.execute(slot_id)
            if investigator_id:
                controllers = reconcile_world_investigator_roster(
                    DATABASE_URL, engine.context, engine.context.world_id,
                    preferred_user_id=target_user_id,
                )
                investigator_id = controllers.get(str(target_user_id or ""))
                if not investigator_id:
                    release_turn()
                    await outbound.send({"type": "error", "message": "当前行动者已没有可操作的调查员", "terminal": True})
                    return
        except StaleRevisionError as exc:
            release_turn()
            await outbound.send({"type": "error", "message": str(exc), "terminal": True})
            return
        except SaveNotFoundError:
            release_turn()
            message = "未找到存档。" if announce_loaded else "未找到存档，请开始新游戏。"
            await outbound.send({"type": "error", "message": message, "terminal": True})
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
            await send_character_state(target_user_id)
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
            target_user_id=data.get("_room_actor_user_id"),
        )

    @router.handler("save_load")
    async def handle_save_load(data: dict) -> None:
        await resume_game(
            str(data.get("slot_id") or ""),
            announce_loaded=True,
            investigator_id=data.get("_room_investigator_id"),
            target_user_id=data.get("_room_actor_user_id"),
        )

    @router.handler("action")
    async def handle_action(data: dict) -> None:
        if not await reserve_turn():
            return
        actor = None
        try:
            investigator_id = str(data.get("_room_investigator_id") or "")
            if investigator_id:
                activate_investigator(engine.context, investigator_id)
                engine._multiplayer_roster_active = True
            intent = game_app.perform_action.execute(data.get("content", ""))
            actor_user_id = str(data.get("_room_actor_user_id") or "")
            if actor_user_id and investigator_id:
                state = engine.context.world_store.load()
                investigators = state.get("investigators")
                pc = (
                    investigators.get(investigator_id)
                    if isinstance(investigators, dict)
                    else state.get("pc")
                )
                if isinstance(pc, dict):
                    actor = {
                        "type": "investigator",
                        "user_id": actor_user_id,
                        "investigator_id": investigator_id,
                        "name": str(pc.get("name") or "调查员")[:160],
                    }
        except ApplicationUseCaseError as exc:
            release_turn()
            await outbound.send({"type": "error", "message": str(exc), "terminal": True})
            return
        except Exception:
            release_turn()
            raise
        await launch_reserved_turn(
            engine.handle_action,
            intent.engine_input,
            turn_kind=intent.kind,
            player_input=intent.player_input,
            actor=actor,
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
            **list_character_options(
                engine.context.module_name,
                context=engine.context,
                include_personal=not auth_required(),
            ),
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

            try:
                routed = await router.dispatch(data)
            except Exception as exc:
                # A malformed save/state/control operation must not terminate the
                # shared room driver for every connected player. Turn workers
                # already report their own failures; synchronous protocol
                # handlers are isolated here and the driver stays available.
                import traceback

                print(
                    f"[ws] {data.get('type', 'unknown')} 处理异常: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
                traceback.print_exc(file=sys.stderr)
                await outbound.send(
                    {
                        "type": "error",
                        "code": "operation_failed",
                        "operation": str(data.get("type") or ""),
                        "message": "操作失败，房间连接已保留，请稍后重试。",
                        "terminal": data.get("_room_reserved_action") is True,
                    }
                )
                continue
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
        finish_room_start(False)
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
    """Process liveness; it deliberately does not touch external dependencies."""
    return {
        "ok": True,
        "module": _active_context.module_name,
        "world_id": _active_context.world_id,
    }


@app.get("/api/ready")
def readiness():
    """Deployment readiness, including a real database round trip."""
    try:
        with session_scope(DATABASE_URL) as db_session:
            db_session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            {"ok": False, "detail": "database unavailable"},
            status_code=503,
        )
    return {
        "ok": True,
        "module": _active_context.module_name,
        "world_id": _active_context.world_id,
    }


@app.get("/api/characters")
async def list_characters():
    """列出可用于当前模组的新游戏调查员。"""
    return list_character_options(
        _active_context.module_name,
        context=_active_context,
        include_personal=not auth_required(),
    )


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
            await ws.close(code=4409, reason="账号模式请使用多人房间连接")
            return
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


async def serve_asset(module_name: str, filename: str):
    """Compatibility entry point for callers that imported this endpoint."""
    return serve_module_asset(
        MODULE_REGISTRY,
        hosted=auth_required(),
        module_name=module_name,
        filename=filename,
    )


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

    bind_host = os.environ.get("TRPG_BIND_HOST", "127.0.0.1").strip() or "127.0.0.1"
    bind_port = int(os.environ.get("TRPG_BIND_PORT", "8765"))
    print("🎲 TRPG Agent WebSocket 服务器")
    print(f"   ws://{bind_host}:{bind_port}/ws    前端: http://{bind_host}:{bind_port}/")
    uvicorn.run(app, host=bind_host, port=bind_port)
