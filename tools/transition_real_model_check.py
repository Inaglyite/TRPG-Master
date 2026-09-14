#!/usr/bin/env python3
"""过渡回合的真实模型验收（带完整轨迹记录）。

一次运行内按固定顺序走过：

    S1 意愿（我想先看看莱特教授的尸体）
    S2 追问（那位医生和我们熟吗）
    S3 明确出发（那麻烦你联系一下，我现在过去）
    S4 换说法（换个说法：我想去莱特的办公室看看）
    S5 取消（算了，医学院先不去了）
    S6 普通直接移动（我们现在就出发去莱特的办公室）

对每一次模型调用记录：system 提示词、发给模型的上下文（含最新玩家消息、待办状态、
最近对话）、请求参数（含**实际生效** max_tokens）、原始响应、finish_reason、usage；
对每一次命令尝试记录：kind、payload、提交结果或**拒绝原因**；每回合记录场景前后与待办状态。

隔离：`TRPG_RUNTIME_ROOT` 指向临时目录，模组取自仓库模板，不碰任何真实存档。
凭据：环境变量优先，其次 `.env.json`；报告只记 api_key 是否存在，不落明文。

用法：
    env -u PYTHONPATH .venv/bin/python tools/transition_real_model_check.py \
        --report /tmp/transition_trace.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODULE = "猩红文档"
CHARACTER = {
    "source": "module",
    "file": "黄千陆.json",
    "path": f"mod/{MODULE}/characters/黄千陆.json",
    "module": MODULE,
}

SCENARIO_GROUPS: list[dict[str, Any]] = [
    {
        # 链条组：意愿 → 追问 → 明确出发（同一个世界、连续三轮）
        "world": "chain",
        "label": "意愿→追问→明确出发",
        "scenarios": [
            {
                "key": "C1_intent",
                "label": "意愿：想先看看遗体",
                "text": "说实话，我想先看看莱特教授的尸体。",
                "expect_movement": False,
            },
            {
                "key": "C2_follow_up",
                "label": "追问：继续交谈",
                "text": "那位值班医生和我们熟吗？",
                "expect_movement": False,
            },
            {
                "key": "C3_go",
                "label": "明确出发：麻烦你联系一下，我现在过去",
                "text": "那麻烦你联系一下，我现在过去。",
                "expect_movement": True,
                # 需求：承接已约定目的地。目的地要么在本轮被约定（上一层叙事），
                # 要么玩家在句子里指明；这里不做任何前端/工具侧的关键词替代。
            },
        ],
    },
    {
        # 对偶组：换一个隔离世界，保证每个用例的起点 ≠ 目标、目标已知可达、无障碍
        "world": "duals",
        "label": "换说法 / 普通直接移动 / 取消",
        "scenarios": [
            {
                "key": "D1_plain_move",
                "label": "普通直接移动：现在就出发去医学院",
                "text": "我们现在就出发去密斯卡托尼克大学医学院。",
                "expect_movement": True,
                "expect_destination": "miskatonic_medical",
                "require_different_scene": "miskatonic_medical",
            },
            {
                "key": "D2_rephrase",
                "label": "换说法：要不我们直接过去莱特的办公室",
                "text": "要不我们直接过去莱特的办公室？",
                "expect_movement": True,
                "expect_destination": "wright_office",
                "require_different_scene": "wright_office",
            },
            {
                "key": "D3_wish_no_move",
                "label": "意愿式说法：我想回医学院再看看",
                "text": "我想回医学院那边再看看。",
                "expect_movement": False,
            },
            {
                "key": "D4_cancel",
                "label": "取消：算了，先不去了",
                "text": "算了，医学院先不去了。",
                "expect_movement": False,
            },
        ],
    },
]


def _load_env() -> None:
    env_file = ROOT / ".env.json"
    if not env_file.exists():
        return
    cfg = json.loads(env_file.read_text(encoding="utf-8"))
    for cfg_key, env_key in (
        ("api_key", "OPENAI_API_KEY"),
        ("base_url", "OPENAI_BASE_URL"),
        ("flash_model", "TRPG_FLASH_MODEL"),
        ("narrative_model", "TRPG_NARRATIVE_MODEL"),
        ("judgement_model", "TRPG_JUDGEMENT_MODEL"),
    ):
        value = cfg.get(cfg_key)
        if value and env_key not in os.environ:
            os.environ[env_key] = str(value)


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

_load_env()
_TEMP_ROOT = tempfile.mkdtemp(prefix="trpg-transition-trace-")
os.environ["TRPG_RUNTIME_ROOT"] = _TEMP_ROOT
os.environ["TRPG_WRITE_COMPAT_EXPORTS"] = "0"

from sqlalchemy import select  # noqa: E402

from src.ai.model import route_service  # noqa: E402
from src.ai.model.route_service import (  # noqa: E402
    EffectiveSettings,
    RoleBinding,
    ServiceSpec,
)
from src.app.config import NARRATIVE_MODEL, PROJECT_ROOT, RUNTIME_ROOT  # noqa: E402
from src.app.engine import GameEngine  # noqa: E402
from src.storage import model_config_store  # noqa: E402
from src.storage.database import PlayerRequest, World, session_scope  # noqa: E402
from src.storage.database_store import DatabaseWorldStore  # noqa: E402
from src.storage.world_branches import WorldBranchService  # noqa: E402
from src.structured import agent_runtime  # noqa: E402
from src.structured.bootstrap import apply_profile_metadata, ensure_local_operator  # noqa: E402
from src.structured.gateway import StructuredGateway  # noqa: E402
from src.structured.service import StructuredPlayService  # noqa: E402

MAX_TOKENS_CEILING = 32768  # 与 agent_runtime 的防御性封顶一致


class _RecordingCompletions:
    """代理 chat.completions：记录请求参数与响应元数据（不改生产行为）。"""

    def __init__(self, inner: Any, sink: list[dict]) -> None:
        self._inner = inner
        self._sink = sink

    def create(self, **kwargs: Any) -> Any:
        entry: dict[str, Any] = {
            "request": {
                "model": kwargs.get("model"),
                "max_tokens": kwargs.get("max_tokens"),
                "temperature": kwargs.get("temperature"),
                "response_format": kwargs.get("response_format"),
                "messages": kwargs.get("messages"),
            }
        }
        try:
            response = self._inner.create(**kwargs)
        except Exception as exc:
            entry["error"] = {"type": type(exc).__name__, "message": str(exc)[:300]}
            self._sink.append(entry)
            raise
        choice = response.choices[0] if response.choices else None
        message = getattr(choice, "message", None)
        entry["response"] = {
            "content": str(getattr(message, "content", "") or ""),
            "reasoning_chars": len(str(getattr(message, "reasoning_content", "") or "")),
            "finish_reason": getattr(choice, "finish_reason", None),
        }
        usage = getattr(response, "usage", None)
        entry["usage"] = {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }
        self._sink.append(entry)
        return response


class _ShimChat:
    def __init__(self, completions: Any) -> None:
        self.completions = completions


class _ShimClient:
    def __init__(self, inner: Any, sink: list[dict]) -> None:
        self._inner = inner
        self.api_key = getattr(inner, "api_key", None)
        self.base_url = getattr(inner, "base_url", None)
        self.chat = _ShimChat(_RecordingCompletions(inner.chat.completions, sink))

    def __getattr__(self, item: str) -> Any:  # 其余属性透传，保持生产行为
        return getattr(self._inner, item)


def install_recorder(sink: list[dict]) -> None:
    """在生产 route resolver 之后套一层记录代理：模型请求与响应全部留痕。"""
    original = route_service.resolve_routes

    def wrapped(settings: EffectiveSettings) -> Any:
        routes = original(settings)
        return type(routes)(
            narrative=_shim_role(routes.narrative, sink),
            judgement=_shim_role(routes.judgement, sink),
            revision=routes.revision,
        )

    route_service.resolve_routes = wrapped  # type: ignore[assignment]
    # 运行器内部是延迟 import，patch 到它引用的模块上。
    agent_runtime.resolve_routes = wrapped  # type: ignore[attr-defined]


def _shim_role(role: Any, sink: list[dict]) -> Any:
    """只替换 client，其余字段原样保留（dataclasses.replace 语义）。"""
    import dataclasses

    return dataclasses.replace(role, client=_ShimClient(role.client, sink))


class _Collector:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def send(self, envelope: dict) -> None:
        self.events.append(envelope)


def configure_byok(database_url: str, world_id: str) -> dict:
    owner = model_config_store.current_world_owner_id(database_url, world_id)
    assert owner, "本地世界缺少 owner 成员行"
    service = ServiceSpec(
        label="transition-trace",
        provider_kind="deepseek",
        base_url=os.environ.get("OPENAI_BASE_URL") or "https://api.deepseek.com",
        api_key=os.environ["OPENAI_API_KEY"],
        model_id=os.environ.get("TRPG_NARRATIVE_MODEL") or NARRATIVE_MODEL,
        window_tokens=None,
        max_output_tokens=int(os.environ.get("TRPG_TRACE_MAX_OUTPUT", "16000")),
        capabilities=None,
        allow_private=True,
    )
    binding = RoleBinding(mode="custom", service=service)
    model_config_store.save_scope(
        database_url,
        owner_user_id=owner,
        world_id=world_id,
        settings_no_revision=EffectiveSettings(
            narrative=binding, judgement=binding, revision=0
        ),
    )
    return {
        "owner_user_id": owner,
        "provider_kind": service.provider_kind,
        "configured_max_output_tokens": service.max_output_tokens,
        "effective_max_tokens": min(service.max_output_tokens, MAX_TOKENS_CEILING),
    }


async def wait_for_run(
    database_url: str, world_id: str, request_id: str, *, timeout: float
) -> str:
    deadline = time.time() + timeout
    status = "unknown"
    while time.time() < deadline:
        await asyncio.sleep(1.0)
        task = agent_runtime._active_runs.get(world_id)  # noqa: SLF001 - 验收工具
        if task is not None and not task.done():
            continue
        with session_scope(database_url) as session:
            row = session.execute(
                select(PlayerRequest).where(
                    PlayerRequest.world_id == world_id,
                    PlayerRequest.request_id == request_id,
                )
            ).scalar_one_or_none()
            status = str(row.status) if row else "missing"
        if status not in {"queued", "processing"}:
            return status
    return f"timeout:{status}"


def _scene_of(context: Any) -> str:
    snapshot = DatabaseWorldStore(
        context.database_url, context.world_id, context.world_dir
    ).snapshot()
    return str((snapshot.state.get("current_scene") or {}).get("id") or "")


def _request_row(database_url: str, world_id: str, request_id: str) -> dict:
    with session_scope(database_url) as session:
        row = session.execute(
            select(PlayerRequest).where(
                PlayerRequest.world_id == world_id,
                PlayerRequest.request_id == request_id,
            )
        ).scalar_one_or_none()
        if row is None:
            return {"status": "missing"}
        return {
            "status": row.status,
            "detail": row.detail,
            "deferred_player_intent": (row.payload or {}).get("awaiting"),
        }


def _pending_requests(database_url: str, world_id: str) -> list[dict]:
    with session_scope(database_url) as session:
        rows = (
            session.execute(
                select(PlayerRequest).where(PlayerRequest.world_id == world_id)
            )
            .scalars()
            .all()
        )
        return sorted(
            (
                {
                    "request_id": row.request_id,
                    "status": row.status,
                    "has_deferred_intent": bool((row.payload or {}).get("awaiting")),
                }
                for row in rows
            ),
            key=lambda item: item["request_id"],
        )


def _context_diagnostics(call: dict) -> dict:
    """历史截断、角色顺序、待办是否被标成授权、提示词是否含关键规则。"""
    messages = (call.get("request") or {}).get("messages") or []
    roles = [message.get("role") for message in messages]
    system = messages[0].get("content", "") if messages else ""
    context_text = messages[1].get("content", "") if len(messages) > 1 else ""
    try:
        context = json.loads(context_text)
    except json.JSONDecodeError:
        context = {}
    recent = context.get("recent_public_messages") or []
    pending = context.get("pending_requests") or []
    deferred_entries = [entry for entry in pending if entry.get("deferred_player_intent")]
    return {
        "roles": roles,
        "system_chars": len(system),
        "context_chars": len(context_text),
        "prompt_has_question_rule": "玩家只是提问" in system,
        "prompt_has_insist_rule": "仍明确坚持就" in system,
        "prompt_has_deferred_not_authorization_rule": "只是记录" in system,
        "recent_message_count": len(recent),
        "recent_message_chars": [len(m.get("text") or "") for m in recent],
        "recent_limit_note": "最多 8 条、每条截断 400 字符（_recent_transcript）",
        "truncated_recent_messages": sum(
            1 for message in recent if len(message.get("text") or "") >= 400
        ),
        "pending_request_count": len(pending),
        "deferred_entries": len(deferred_entries),
        "deferred_intent_marked_not_authorization": (
            bool(deferred_entries)
            and all(
                entry.get("deferred_player_intent_is_authorization") is False
                for entry in deferred_entries
            )
        ),
        "snapshot_keys": sorted((context.get("snapshot") or {}).keys()),
    }


def build_agent_world() -> tuple[Any, str, dict]:
    """建一个隔离的 structured_v1 + agent 世界（临时 root，真实模组）。"""
    service_root = WorldBranchService(PROJECT_ROOT, RUNTIME_ROOT)
    context = service_root.create_root(MODULE)
    engine = GameEngine(context)
    engine.reset(dict(CHARACTER))
    world_id = context.world_id
    with session_scope(context.database_url) as session:
        world = session.get(World, world_id)
        assert world is not None
        world.metadata_json = apply_profile_metadata(
            world.metadata_json, execution_profile="structured_v1", keeper_mode="agent"
        )
        ensure_local_operator(session, world_id)
        session.add(world)
    return context, world_id, configure_byok(context.database_url, world_id)


async def dry_run() -> int:
    """不调用模型：只检查世界/前置条件/目的地是否可用（用于本轮授权前的自检）。"""
    for group in SCENARIO_GROUPS:
        context, world_id, byok = build_agent_world()
        gateway = StructuredGateway(context.database_url)
        payload = gateway.snapshot_envelope(world_id=world_id, user_id=None)["payload"]
        destinations = {d["id"]: d["name"] for d in (payload.get("destinations") or [])}
        print(f"\n[{group['world']}] {group['label']}｜世界 {world_id}｜起始场景 {_scene_of(context)}")
        print(f"  目的地：{destinations}")
        for scenario in group["scenarios"]:
            require = scenario.get("require_different_scene")
            if require and require not in destinations:
                print(f"  ✗ {scenario['key']}：目标 {require} 不在已知目的地里")
                return 1
            expect = scenario.get("expect_destination")
            if expect and expect not in destinations:
                print(f"  ✗ {scenario['key']}：期望目的地 {expect} 不在已知目的地里")
                return 1
            print(
                f"  ✓ {scenario['key']}：{'移动' if scenario['expect_movement'] else '不移动'}"
                f"｜起点需 ≠ {require or '—'}｜期望目的地 {expect or '—'}"
            )
    print("\n自检通过（未调用模型）。")
    return 0


async def run_trace(*, report_path: Path, timeout: float, verbose: bool) -> dict:
    context, world_id, byok = build_agent_world()
    gateway = StructuredGateway(context.database_url)
    model_calls: list[dict] = []
    install_recorder(model_calls)

    # 命令层留痕：拒绝原因（含 schema 拒绝）必须可查。运行器内部会自己 new
    # 一个 StructuredPlayService，所以必须包**类**而不是网关那个实例。
    command_attempts: list[dict] = []
    original_execute = StructuredPlayService.execute_command

    def recording_execute(self: Any, **kwargs: Any) -> Any:
        entry = {
            "kind": kwargs.get("kind"),
            "payload": kwargs.get("payload"),
            "command_id": kwargs.get("command_id"),
        }
        try:
            outcome = original_execute(self, **kwargs)
        except Exception as exc:
            entry["rejected"] = {
                "code": getattr(exc, "code", type(exc).__name__),
                "message": str(getattr(exc, "message", exc))[:300],
            }
            command_attempts.append(entry)
            raise
        entry["result"] = outcome.get("result")
        command_attempts.append(entry)
        return outcome

    StructuredPlayService.execute_command = recording_execute  # type: ignore[method-assign]

    snapshot = gateway.snapshot_envelope(world_id=world_id, user_id=None)
    investigator_id = str(snapshot["payload"].get("investigator_id") or "")
    destinations = snapshot["payload"].get("destinations") or []

    report: dict[str, Any] = {
        "world_id": world_id,
        "module": MODULE,
        "model": os.environ.get("TRPG_NARRATIVE_MODEL") or NARRATIVE_MODEL,
        "runtime_root": str(RUNTIME_ROOT),
        "isolated_test_world": True,
        "api_key_present": bool(os.environ.get("OPENAI_API_KEY")),
        "byok": byok,
        "code_revision": _code_revision(),
        "code_fingerprint": _code_fingerprint(),
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "start_scene": _scene_of(context),
        "destinations": destinations,
        "scenarios": [],
        "worlds": [
            {
                "world": "chain",
                "world_id": world_id,
                "runtime_root": str(RUNTIME_ROOT),
                "destinations": destinations,
            }
        ],
    }
    print(
        f"世界 {world_id}（临时）｜模型 {report['model']}｜生效预算 max_tokens="
        f"{byok['effective_max_tokens']}"
    )
    print(f"起始场景 {report['start_scene']}｜目的地 {[d['name'] for d in destinations][:6]}")

    index = 0
    for group in SCENARIO_GROUPS:
        if group["world"] != "chain":
            # 对偶组换一个隔离世界：保证每个用例起点 ≠ 目标、目标已知可达、无障碍。
            context, world_id, byok = build_agent_world()
            gateway = StructuredGateway(context.database_url)
            snapshot = gateway.snapshot_envelope(world_id=world_id, user_id=None)
            investigator_id = str(snapshot["payload"].get("investigator_id") or "")
            destinations = snapshot["payload"].get("destinations") or []
            report["worlds"].append(
                {
                    "world": group["world"],
                    "world_id": world_id,
                    "runtime_root": str(RUNTIME_ROOT),
                    "destinations": destinations,
                }
            )
            print(f"\n### 对偶组新世界 {world_id}｜起始场景 {_scene_of(context)}")
    for scenario in group["scenarios"]:
        index += 1
        collector = _Collector()
        request_id = f"trace-{group['world'][0]}-{index}"
        snapshot = gateway.snapshot_envelope(world_id=world_id, user_id=None)
        scene_before = _scene_of(context)
        calls_before = len(model_calls)
        attempts_before = len(command_attempts)
        await gateway.handle_frame(
            world_id=world_id,
            user_id=None,
            frame={
                "type": "action_request",
                "protocol_version": 1,
                "world_id": world_id,
                "request_id": request_id,
                "expected_revision": int(snapshot["payload"]["revision"]),
                "investigator_id": investigator_id,
                "action": {"kind": "freeform", "text": scenario["text"]},
            },
            deliver=collector.send,
        )
        require = scenario.get("require_different_scene")
        if require and scene_before == require:
            entry = {
                "key": scenario["key"],
                "label": scenario["label"],
                "player_message": scenario["text"],
                "request_id": request_id,
                "scene_before": scene_before,
                "verdict": "INAPPLICABLE",
                "verdict_note": f"前置条件不成立：起点已是目标场景 {require}（不能把原地移动当成移动用例）",
            }
            report["scenarios"].append(entry)
            print(f"\n=== {scenario['key']}｜{scenario['label']} ===\n"
                  f"判定 INAPPLICABLE：起点已是 {require}")
            continue
        status = await wait_for_run(
            context.database_url, world_id, request_id, timeout=timeout
        )
        scene_after = _scene_of(context)
        moved = scene_before != scene_after

        calls = model_calls[calls_before:]
        for call in calls:
            call["diagnostics"] = _context_diagnostics(call)
        entry: dict[str, Any] = {
            "key": scenario["key"],
            "label": scenario["label"],
            "player_message": scenario["text"],
            "request_id": request_id,
            "expect_movement": scenario["expect_movement"],
            "scene_before": scene_before,
            "scene_after": scene_after,
            "moved": moved,
            "waited_for_player": status == "awaiting_player",
            "request": _request_row(context.database_url, world_id, request_id),
            "events": [
                {"type": envelope.get("type"), "payload": envelope.get("payload")}
                for envelope in collector.events
            ],
            "model_calls": calls,
            "command_attempts": command_attempts[attempts_before:],
            "pending_after": _pending_requests(context.database_url, world_id),
        }
        expect_destination = scenario.get("expect_destination")
        if scenario["expect_movement"] is None:
            entry["verdict"] = "RECORDED"
        elif bool(scenario["expect_movement"]) == moved:
            if moved and expect_destination and scene_after != expect_destination:
                entry["verdict"] = "FAIL"
                entry["verdict_note"] = (
                    f"移动了但没到期望目的地：{scene_after} != {expect_destination}"
                )
            else:
                entry["verdict"] = "PASS"
        else:
            entry["verdict"] = "FAIL"
        report["scenarios"].append(entry)

        if verbose:
            kinds = [
                f"{attempt['kind']}:{'rejected' if 'rejected' in attempt else 'committed'}"
                for attempt in entry["command_attempts"]
            ]
            print(f"\n=== {scenario['key']}｜{scenario['label']} ===")
            print(f"玩家：{scenario['text']}")
            print(
                f"场景 {scene_before}→{scene_after}（moved={moved}）｜请求 {entry['request']['status']}"
                f"｜模型调用 {len(calls)}｜命令 {kinds or '无'}｜判定 {entry['verdict']}"
            )
            for call in calls:
                usage = call.get("usage") or {}
                response = call.get("response") or {}
                print(
                    f"  模型：finish={response.get('finish_reason')} "
                    f"content={len(response.get('content') or '')}字符 "
                    f"reasoning={response.get('reasoning_chars')}字符 "
                    f"usage={usage.get('total_tokens')} max_tokens={call['request']['max_tokens']}"
                )
                if call.get("error"):
                    print(f"  模型错误：{call['error']}")
            for attempt in entry["command_attempts"]:
                if "rejected" in attempt:
                    print(
                        f"  命令被拒：{attempt['kind']} → {attempt['rejected']['code']}"
                        f" {attempt['rejected']['message'][:120]}"
                    )
            for envelope in entry["events"]:
                payload = envelope.get("payload") or {}
                if envelope.get("type") == "message_completed":
                    print(f"  守秘人：{str(payload.get('text'))[:160]}")

    report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    report["end_scene"] = _scene_of(context)
    report["all_model_calls"] = len(model_calls)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


CODE_FINGERPRINT_FILES = (
    "src/structured/agent.py",
    "src/structured/agent_runtime.py",
    "src/structured/agent_prompts.py",
    "src/structured/domains.py",
    "src/structured/service.py",
    "src/structured/validation.py",
    "schemas/structured-play/v1/command_request.json",
    "schemas/structured-play/v1/events.json",
)


def _code_fingerprint() -> dict[str, str]:
    """逐文件 sha256：证明验收跑的是哪一份代码（不靠 HEAD，因为改动未提交）。"""
    import hashlib

    fingerprint = {}
    for rel in CODE_FINGERPRINT_FILES:
        path = ROOT / rel
        if path.exists():
            fingerprint[rel] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return fingerprint


def _code_revision() -> str:
    """记录验收对应的代码版本（HEAD + 工作区是否干净），避免拼接不同版本的结论。"""
    import subprocess

    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True
            ).stdout.strip()
        )
        return f"{head[:7]}{'+dirty' if dirty else ''}"
    except Exception:  # pragma: no cover - 非 git 环境
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default="/tmp/transition_trace.json")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只做自检（世界/前置条件/目的地），不调用模型",
    )
    args = parser.parse_args()
    if args.dry_run:
        return asyncio.run(dry_run())
    report = asyncio.run(
        run_trace(
            report_path=Path(args.report),
            timeout=args.timeout,
            verbose=not args.quiet,
        )
    )
    verdicts = {scenario["key"]: scenario["verdict"] for scenario in report["scenarios"]}
    print(f"\n报告：{args.report}")
    print(f"总判定：{verdicts}")
    failed = [key for key, verdict in verdicts.items() if verdict == "FAIL"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
