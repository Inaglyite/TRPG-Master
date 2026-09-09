"""模型配置门面：本地 / 在线单人 / 多人房间共用的读写、测试与投影。

传输层（server.py 本地 /ws、multiplayer 房间循环）只做权限判定与委派，
所有解析、校验、存储与脱敏投影都收口在本模块：

- 凭据永不离开服务端：payload 只给 has_key，测试连接只回结构化可读结果；
- 保存=写入存储并待下回合生效（engine 在回合边界冻结路由），
  不打断进行中的回合，也不拒绝回合中的保存；
- 自定义服务必须显式确认数据发送与额度归属（confirm_data_sharing）。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from openai import OpenAI

from src.ai.model import route_service
from src.ai.model.egress_guard import validate_outbound_url
from src.ai.model.pinned_transport import pinned_http_client
from src.ai.model.provider_adapter import (
    ERROR_AUTH,
    ERROR_BUSY,
    ERROR_CONTEXT_WINDOW,
    ERROR_QUOTA,
    ERROR_RATE_LIMIT,
    ERROR_SERVER,
    ERROR_TIMEOUT,
    ERROR_TRANSPORT,
    classify_provider_error,
)
from src.ai.model.route_service import EffectiveSettings, RoleBinding
from src.app.config import (
    JUDGEMENT_MODEL,
    MODEL_FLASH,
    MODEL_PRO,
    NARRATIVE_MODEL,
    context_window_tokens,
    max_output_tokens,
)

TEST_COOLDOWN_SECONDS = 10.0
TEST_TIMEOUT_SECONDS = 20.0
# 连接测试每日上限（按 UTC 日、作用域计）：用户承担模型费用，不代表探针
# 请求没有服务器成本；与注册/行动限流同为进程内存口径，重启清零。
TEST_DAILY_LIMIT = 30

_ROLE_LABELS = {"narrative": "叙述模型", "judgement": "裁决模型"}

_ERROR_TEXT = {
    ERROR_AUTH: "认证失败：API Key 无效或权限不足",
    ERROR_QUOTA: "额度不足：请检查账户余额",
    ERROR_RATE_LIMIT: "触发服务商限流，请稍后重试",
    ERROR_TIMEOUT: "连接超时",
    ERROR_TRANSPORT: "无法连接到服务（地址不可达或被拒绝）",
    ERROR_SERVER: "服务商错误（5xx）",
    ERROR_CONTEXT_WINDOW: "上下文窗口不足",
    ERROR_BUSY: "服务繁忙",
}


@dataclass(frozen=True)
class SettingsScope:
    """一次设置读/写的作用域与权限上下文。"""

    mode: str  # "local" | "solo" | "room"
    can_edit: bool
    user_id: str | None = None
    # 房间/单人世界的当前房主（配置解析以房主为准，成员只见脱敏视图）
    owner_user_id: str | None = None
    world_id: str | None = None
    db_url: str | None = None
    local_path: Path | None = None
    engine: object | None = None

    @property
    def is_local(self) -> bool:
        return self.mode == "local"


# ---------------------------------------------------------------- 作用域存取


def _scope_world_key(scope: SettingsScope, data: dict | None) -> str:
    """写入目标作用域：本地无世界维度；solo 可选 account/world；room 固定房间。"""
    if scope.is_local:
        return ""
    if scope.mode == "room":
        return scope.world_id or ""
    requested = str((data or {}).get("scope") or "account").strip()
    if requested == "world" and scope.world_id:
        return scope.world_id
    return ""


def _load_saved(scope: SettingsScope, world_key: str | None = None) -> EffectiveSettings:
    """读取作用域自己的保存值（不含回落），用于展示与 Key 沿用。"""
    if scope.is_local:
        return route_service.load_local_settings(scope.local_path)
    from src.storage import model_config_store

    key = scope.world_id if world_key is None else world_key
    loaded = model_config_store.load_scope(scope.db_url, scope.user_id or "", key)
    return loaded if loaded is not None else route_service.default_settings()


def _load_resolved(scope: SettingsScope) -> tuple[EffectiveSettings, str | None, bool]:
    """解析当前生效视图：(settings, blocked_reason, world_override_exists)。"""
    if scope.is_local:
        return _load_saved(scope), None, False
    from src.storage import model_config_store

    override_exists = model_config_store.has_world_row(scope.db_url, scope.world_id or "")
    try:
        settings = model_config_store.resolve_cloud_settings(
            scope.db_url,
            owner_user_id=scope.owner_user_id or scope.user_id or "",
            world_id=scope.world_id,
        )
        return settings, None, override_exists
    except route_service.RouteBlockedError as exc:
        return route_service.default_settings(), str(exc), True


# ---------------------------------------------------------------- 投影


def _role_view(binding: RoleBinding, slot: str, *, reveal_url: bool) -> dict:
    if binding.mode != "custom" or binding.service is None:
        return {
            "mode": "default",
            "model_id": NARRATIVE_MODEL if slot == "narrative" else JUDGEMENT_MODEL,
            "window_tokens": context_window_tokens(),
            "window_source": "legacy_default",
            "max_output_tokens": max_output_tokens(),
            "capabilities": None,
            "service": None,
        }
    service = binding.service
    host = route_service.host_of(service.base_url)
    return {
        "mode": "custom",
        "model_id": service.model_id,
        "window_tokens": service.window_tokens,
        "window_source": "manual" if service.window_tokens else "unknown",
        "max_output_tokens": service.max_output_tokens or max_output_tokens(),
        "capabilities": service.capabilities,
        "service": {
            "label": service.label,
            "provider_kind": service.provider_kind,
            # 成员只见目的地主机名；完整 URL 仅配置所有者可见（供编辑）。
            "base_url": service.base_url if reveal_url else None,
            "base_url_host": host,
            "has_key": True,
        },
    }


def build_payload(
    scope: SettingsScope, _data: dict | None = None, *, notice: str | None = None
) -> dict:
    settings, blocked, world_override = _load_resolved(scope)
    reveal = scope.is_local or scope.can_edit
    applied = getattr(getattr(scope.engine, "_model_routes", None), "revision", None)
    scope_label = {
        "local": "本地默认（本机所有冒险共用）",
        "solo": "当前账号" + ("（此冒险已单独覆盖）" if world_override else ""),
        "room": "当前房间（房主配置，成员只读）",
    }[scope.mode]
    payload = {
        "type": "model_settings",
        "mode": scope.mode,
        "can_edit": scope.can_edit,
        # 云端一律 BYOK-only：前端据此把 default 角色渲染为"未配置"并引导配置。
        "byok_required": not scope.is_local,
        "scope_label": scope_label,
        "world_override": world_override,
        "revision": settings.revision,
        "applied_revision": applied,
        "blocked": blocked,
        "narrative": _role_view(settings.narrative, "narrative", reveal_url=reveal),
        "judgement": _role_view(settings.judgement, "judgement", reveal_url=reveal),
        "server_defaults": {
            "narrative_model": NARRATIVE_MODEL,
            "judgement_model": JUDGEMENT_MODEL,
            "available_models": [
                {"id": MODEL_FLASH, "label": "Flash"},
                {"id": MODEL_PRO, "label": "Pro"},
            ],
            "window_tokens": context_window_tokens(),
            "window_source": "legacy_default",
            "max_output_tokens": max_output_tokens(),
        },
        # 前端兼容：旧版只读这两个顶层字段。
        "narrative_model": _role_view(settings.narrative, "narrative", reveal_url=reveal)[
            "model_id"
        ],
        "judgement_model": _role_view(settings.judgement, "judgement", reveal_url=reveal)[
            "model_id"
        ],
        "available_models": [
            {"id": MODEL_FLASH, "label": "Flash"},
            {"id": MODEL_PRO, "label": "Pro"},
        ],
    }
    if notice:
        payload["notice"] = notice
    return payload


# ---------------------------------------------------------------- 写操作


def _require_edit(scope: SettingsScope) -> None:
    if not scope.can_edit:
        raise PermissionError(
            "仅房主可修改模型配置" if scope.mode == "room" else "当前账号没有修改权限"
        )


def apply_update(scope: SettingsScope, data: dict) -> dict:
    """保存配置：写入存储、下回合生效；回合中保存不打断进行中的请求。"""
    _require_edit(scope)
    world_key = _scope_world_key(scope, data)
    saved = _load_saved(scope, world_key if not scope.is_local else None)
    allow_private = scope.is_local
    narrative = route_service.parse_role_binding(
        data.get("narrative"),
        role_label=_ROLE_LABELS["narrative"],
        allow_private=allow_private,
        existing=saved.narrative.service,
    )
    judgement = route_service.parse_role_binding(
        data.get("judgement"),
        role_label=_ROLE_LABELS["judgement"],
        allow_private=allow_private,
        existing=saved.judgement.service,
    )
    if (narrative.mode == "custom" or judgement.mode == "custom") and not data.get(
        "confirm_data_sharing"
    ):
        raise ValueError("请先确认：故事与角色上下文将发送到你配置的服务商，额度由你的 Key 承担")

    if scope.is_local:
        updated = EffectiveSettings(
            narrative=narrative, judgement=judgement, revision=saved.revision + 1
        )
        route_service.save_local_settings(scope.local_path, updated)
    else:
        from src.storage import model_config_store

        updated = model_config_store.save_scope(
            scope.db_url,
            scope.user_id or "",
            world_key,
            EffectiveSettings(narrative=narrative, judgement=judgement, revision=0),
        )
        from src.auth.service import audit

        audit(
            scope.db_url,
            "model_settings_update",
            user_id=scope.user_id,
            world_id=scope.world_id,
            details={
                "scope": world_key or "account",
                "narrative_mode": narrative.mode,
                "judgement_mode": judgement.mode,
                "revision": updated.revision,
            },
        )
    return {
        **build_payload(scope),
        "saved": True,
        "notice": "配置已保存，将从下一回合生效",
    }


def restore_default(scope: SettingsScope, data: dict) -> dict:
    """恢复默认：只解除用户覆盖，不动存档与游戏事实，不泄露服务器默认凭据。

    云端 BYOK-only 下"恢复默认"= 回到未配置状态（平台不兜底）；
    本地部署才回到部署者自己的平台默认。
    """
    _require_edit(scope)
    world_key = _scope_world_key(scope, data)
    if scope.is_local:
        file = Path(scope.local_path)
        if file.exists():
            file.unlink()
        notice = "已恢复平台默认配置，将从下一回合生效"
    else:
        from src.storage import model_config_store

        removed = model_config_store.delete_scope(scope.db_url, scope.user_id or "", world_key)
        if removed:
            from src.auth.service import audit

            audit(
                scope.db_url,
                "model_settings_restore_default",
                user_id=scope.user_id,
                world_id=scope.world_id,
                details={"scope": world_key or "account"},
            )
        notice = "已清除自定义配置：当前为未配置状态，重新保存自定义服务后才能开始游戏"
    return {
        **build_payload(scope),
        "saved": True,
        "notice": notice,
    }


# ---------------------------------------------------------------- 测试连接

_test_cooldowns: dict[tuple[str, str, str], float] = {}
_test_cooldowns_lock = threading.Lock()
_test_daily: dict[tuple[str, str, str], int] = {}


def _check_cooldown(scope: SettingsScope) -> None:
    key = (scope.mode, scope.user_id or "local", scope.world_id or "")
    now = time.monotonic()
    day = time.strftime("%Y-%m-%d", time.gmtime())
    with _test_cooldowns_lock:
        last = _test_cooldowns.get(key, 0.0)
        if now - last < TEST_COOLDOWN_SECONDS:
            raise ValueError(f"测试连接间隔过短，请 {int(TEST_COOLDOWN_SECONDS)} 秒后再试")
        daily_key = (day, scope.mode, scope.user_id or "local")
        used = _test_daily.get(daily_key, 0)
        if used >= TEST_DAILY_LIMIT:
            raise ValueError(f"测试连接次数已达每日上限（{TEST_DAILY_LIMIT} 次），请明天再试")
        _test_cooldowns[key] = now
        _test_daily[daily_key] = used + 1
        # 惰性清理：字典只在跨日且累积时才收缩，避免长时间运行缓慢膨胀。
        if len(_test_daily) > 1000:
            for stale in [item for item in _test_daily if item[0] != day]:
                del _test_daily[stale]


def _readable_error(exc: BaseException) -> str:
    return _ERROR_TEXT.get(classify_provider_error(exc), "未知错误（未通过连接测试）")


def _probe_client(base_url: str, api_key: str, addresses: tuple[str, ...] = ()) -> OpenAI:
    """探针专用短超时、最多一次重试的直连 client（不进共享缓存）；有已校验地址即钉扎。"""
    http_client = (
        pinned_http_client(addresses, timeout=TEST_TIMEOUT_SECONDS)
        if addresses
        else httpx.Client(follow_redirects=False, timeout=TEST_TIMEOUT_SECONDS, trust_env=False)
    )
    return OpenAI(
        api_key=api_key or "sk-probe",
        base_url=base_url,
        timeout=TEST_TIMEOUT_SECONDS,
        max_retries=1,
        http_client=http_client,
    )


def _probe_chat(client, model: str) -> None:
    client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "请只回复一个字：好"}],
        max_tokens=16,
        temperature=0,
        stream=False,
        timeout=TEST_TIMEOUT_SECONDS,
    )


def _probe_tool_calling(client, model: str) -> None:
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "调用 probe_noop 工具，echo 填 ok"}],
        max_tokens=64,
        temperature=0,
        stream=False,
        timeout=TEST_TIMEOUT_SECONDS,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "probe_noop",
                    "description": "连接能力探针，不产生任何效果",
                    "parameters": {
                        "type": "object",
                        "properties": {"echo": {"type": "string"}},
                        "required": ["echo"],
                    },
                },
            }
        ],
        tool_choice={"type": "function", "function": {"name": "probe_noop"}},
    )
    message = response.choices[0].message if response.choices else None
    if not message or not getattr(message, "tool_calls", None):
        raise ValueError("该服务未按要求返回工具调用（不支持 tool calling）")


def run_test_connection(scope: SettingsScope, data: dict) -> dict:
    """测试连接：连通性 + 固定无剧情探针；失败给可读原因，不回显原始异常。"""
    _require_edit(scope)
    role = str(data.get("role") or "narrative").strip()
    if role not in _ROLE_LABELS:
        raise ValueError("role 必须是 narrative 或 judgement")

    service_raw = data.get("service")
    if service_raw and not data.get("confirm_data_sharing"):
        raise ValueError("请先确认测试探针将发送到该服务商（不含故事内容，可能消耗少量额度）")
    started = time.monotonic()
    if service_raw:
        saved = _load_saved(scope, _scope_world_key(scope, data) if not scope.is_local else None)
        existing = saved.narrative.service if role == "narrative" else saved.judgement.service
        binding = route_service.parse_role_binding(
            {"mode": "custom", "service": service_raw},
            role_label=_ROLE_LABELS[role],
            allow_private=scope.is_local,
            existing=existing,
        )
        service = binding.service
        addresses = validate_outbound_url(service.base_url, allow_private=scope.is_local).addresses
        client = _probe_client(service.base_url, service.api_key, addresses)
        model = service.model_id
        target_host = route_service.host_of(service.base_url)
    else:
        # 测试当前生效绑定。
        settings, _, _ = _load_resolved(scope)
        binding = settings.narrative if role == "narrative" else settings.judgement
        if binding.mode == "custom" and binding.service is not None:
            service = binding.service
            addresses = validate_outbound_url(
                service.base_url, allow_private=scope.is_local
            ).addresses
            client = _probe_client(service.base_url, service.api_key, addresses)
            model = service.model_id
            target_host = route_service.host_of(service.base_url)
        elif not scope.is_local:
            # 云端 BYOK-only：默认绑定即"未配置"，绝不探测平台默认模型。
            return {
                "type": "model_settings_test_result",
                "role": role,
                "ok": False,
                "target_host": "未配置",
                "checks": [
                    {
                        "name": "configured",
                        "ok": False,
                        "detail": f"{_ROLE_LABELS[role]}尚未配置自定义服务；云端模式不调用平台默认模型",
                    }
                ],
                "capabilities": None,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
        else:
            service = None
            client = route_service.server_default_client()
            model = NARRATIVE_MODEL if role == "narrative" else JUDGEMENT_MODEL
            target_host = "平台默认服务"

    # 校验全部通过后才占用冷却窗口：校验失败不消耗测试频率额度。
    _check_cooldown(scope)

    checks: list[dict] = []

    def run_check(name: str, probe) -> bool:
        try:
            probe()
            checks.append({"name": name, "ok": True, "detail": "通过"})
            return True
        except Exception as exc:  # 探针失败必须转成可读原因，不回显原始异常
            checks.append({"name": name, "ok": False, "detail": _readable_error(exc)})
            return False

    run_check("connectivity", lambda: client.models.list(timeout=TEST_TIMEOUT_SECONDS))
    generated = run_check("generation", lambda: _probe_chat(client, model))
    tool_ok = None
    if generated and role == "judgement":
        tool_ok = run_check("tool_calling", lambda: _probe_tool_calling(client, model))

    capabilities = {"streaming": True, "tool_calling": bool(tool_ok)} if generated else None
    if generated and capabilities is not None and service is not None:
        _record_capabilities(scope, role, service, capabilities, data)

    return {
        "type": "model_settings_test_result",
        "role": role,
        "ok": all(check["ok"] for check in checks),
        "target_host": target_host,
        "checks": checks,
        "capabilities": capabilities,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _record_capabilities(
    scope: SettingsScope, role: str, service, capabilities: dict, data: dict
) -> None:
    """探针成功后把能力标记写回同一保存配置（不影响 revision/路由冻结）。"""
    try:
        world_key = "" if scope.is_local else _scope_world_key(scope, data)
        saved = _load_saved(scope, world_key if not scope.is_local else None)
        binding = saved.narrative if role == "narrative" else saved.judgement
        current = binding.service
        if (
            binding.mode != "custom"
            or current is None
            or current.base_url != service.base_url
            or current.model_id != service.model_id
        ):
            return  # 测试的是未保存的新表单，不回写
        updated_service = route_service.with_capabilities(current, capabilities)
        updated_binding = RoleBinding(mode="custom", service=updated_service)
        if scope.is_local:
            route_service.save_local_settings(
                scope.local_path,
                EffectiveSettings(
                    narrative=updated_binding if role == "narrative" else saved.narrative,
                    judgement=updated_binding if role == "judgement" else saved.judgement,
                    revision=saved.revision,
                ),
            )
        else:
            from src.storage import model_config_store

            model_config_store.save_scope_same_revision(
                scope.db_url,
                scope.user_id or "",
                world_key,
                EffectiveSettings(
                    narrative=updated_binding if role == "narrative" else saved.narrative,
                    judgement=updated_binding if role == "judgement" else saved.judgement,
                    revision=saved.revision,
                ),
            )
    except Exception:
        # 能力回写是优化项，失败不影响测试结果本身。
        pass
