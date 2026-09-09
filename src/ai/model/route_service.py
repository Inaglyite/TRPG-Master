"""模型路由服务：角色绑定（平台默认 / 自定义服务）、客户端缓存与回合冻结。

职责边界：
- 只处理"哪个角色走哪个服务"的解析与校验，不碰 WS/HTTP 传输；
- 凭据只以 ServiceSpec 存在于服务端内存与加密存储，payload 投影绝不回传；
- ``apply_pending_routes`` 在回合边界被 engine 调用，是配置生效的唯一入口，
  保证一回合内所有调用点读到同一组冻结路由。

client 复用键含 base_url+api_key 摘要，密钥轮换自动淘汰旧 client；
不存在多用户共享的可变全局路由。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from openai import OpenAI

from ...app.config import (
    API_KEY,
    BASE_URL,
    JUDGEMENT_MODEL,
    NARRATIVE_MODEL,
    context_window_tokens,
    max_output_tokens,
    model_timeout_seconds,
)
from .egress_guard import validate_outbound_url
from .model_settings import validate_model_id
from .pinned_transport import pinned_http_client

PROVIDER_KINDS = frozenset({"deepseek", "openai_compatible"})
WINDOW_MIN, WINDOW_MAX = 8192, 1_048_576
MAX_OUTPUT_MIN, MAX_OUTPUT_MAX = 64, 131_072

# 流式调用角色 → 绑定槽位。战斗/裁决/审计/摘要走裁决绑定，其余走叙述绑定。
ROLE_SLOT = {
    "story": "narrative",
    "rewrite": "narrative",
    "consistency": "narrative",
    "combat": "judgement",
    "adjudication": "judgement",
    "audit": "judgement",
    "summary": "judgement",
}


class RouteBlockedError(RuntimeError):
    """路由被安全策略阻断（如房主更换后原房间配置失效）；message 可读可回显。"""


class RouteNotConfiguredError(RuntimeError):
    """BYOK-only 作用域下所需角色未绑定自定义服务；message 可读可回显。"""


@dataclass(frozen=True)
class ServiceSpec:
    """自定义服务配置（含明文密钥，仅服务端内存态）。"""

    label: str
    provider_kind: str
    base_url: str
    api_key: str
    model_id: str
    window_tokens: int | None
    max_output_tokens: int | None
    capabilities: dict | None  # {"streaming": bool, "tool_calling": bool}，未探测为 None
    # 是否允许本机/私网目标：由保存时的作用域强制（本地 True / 云端 False），
    # 不接受用户载荷里的同名字段。
    allow_private: bool = False

    @property
    def binding_id(self) -> str:
        digest = hashlib.sha256(
            f"{self.base_url}\n{self.model_id}\n{self.api_key}".encode()
        ).hexdigest()
        return f"svc_{digest[:12]}"


@dataclass(frozen=True)
class RoleBinding:
    mode: str  # "default" | "custom"
    service: ServiceSpec | None


@dataclass(frozen=True)
class EffectiveSettings:
    narrative: RoleBinding
    judgement: RoleBinding
    revision: int
    # True = 该作用域禁止平台兜底（云端账号/房间）：角色必须绑定自定义服务，
    # 否则 resolve_routes 抛 RouteNotConfiguredError。仅由作用域解析方烧入，
    # 不参与本地/云端持久化序列化。
    byok_required: bool = False


@dataclass(frozen=True)
class ResolvedRole:
    client: OpenAI
    model_id: str
    window_tokens: int | None
    window_source: str  # manual | legacy_default | unknown
    max_output_tokens: int
    provider_kind: str  # server_default | deepseek | openai_compatible
    binding_id: str
    base_url_host: str
    config_revision: int


@dataclass(frozen=True)
class ModelRoutes:
    narrative: ResolvedRole
    judgement: ResolvedRole
    revision: int

    def role(self, slot: str) -> ResolvedRole:
        return self.narrative if slot == "narrative" else self.judgement


# ---------------------------------------------------------------- 校验与解析


def _parse_window(raw: object, field: str) -> int | None:
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{field} 必须是整数 token 数") from None
    if not WINDOW_MIN <= value <= WINDOW_MAX:
        raise ValueError(f"{field} 超出允许范围 {WINDOW_MIN}-{WINDOW_MAX}")
    return value


def _parse_max_output(raw: object) -> int | None:
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError("最大输出 token 必须是整数") from None
    if not MAX_OUTPUT_MIN <= value <= MAX_OUTPUT_MAX:
        raise ValueError(f"最大输出 token 超出允许范围 {MAX_OUTPUT_MIN}-{MAX_OUTPUT_MAX}")
    return value


def parse_service_spec(raw: object, *, role_label: str, allow_private: bool) -> ServiceSpec:
    """把前端提交的自定义服务表单校验为 ServiceSpec；失败抛 ValueError（可读）。"""
    if not isinstance(raw, dict):
        raise ValueError(f"{role_label} 的自定义服务格式不合法")
    label = str(raw.get("label") or "").strip()[:40]
    provider_kind = str(raw.get("provider_kind") or "").strip()
    if provider_kind not in PROVIDER_KINDS:
        raise ValueError(f"{role_label} 的服务类型必须是 {' / '.join(sorted(PROVIDER_KINDS))}")
    endpoint = validate_outbound_url(raw.get("base_url"), allow_private=allow_private)
    api_key = str(raw.get("api_key") or "").strip()
    if not api_key:
        raise ValueError(f"{role_label} 需要填写 API Key（留空表示沿用已保存的 Key）")
    model_id = validate_model_id(raw.get("model_id"), f"{role_label} 模型 ID")
    capabilities = raw.get("capabilities")
    if capabilities is not None:
        if not isinstance(capabilities, dict):
            raise ValueError("capabilities 格式不合法")
        capabilities = {
            key: bool(capabilities[key])
            for key in ("streaming", "tool_calling")
            if key in capabilities
        }
    return ServiceSpec(
        label=label or endpoint.host,
        provider_kind=provider_kind,
        base_url=endpoint.base_url,
        api_key=api_key,
        model_id=model_id,
        window_tokens=_parse_window(raw.get("window_tokens"), "上下文窗口"),
        max_output_tokens=_parse_max_output(raw.get("max_output_tokens")),
        capabilities=capabilities,
        allow_private=allow_private,
    )


def parse_role_binding(
    raw: object,
    *,
    role_label: str,
    allow_private: bool,
    existing: ServiceSpec | None = None,
) -> RoleBinding:
    """解析单个角色绑定；api_key 留空且已有保存值时沿用 existing。"""
    if not isinstance(raw, dict):
        raise ValueError(f"{role_label} 绑定格式不合法")
    mode = str(raw.get("mode") or "default").strip()
    if mode == "default":
        return RoleBinding(mode="default", service=None)
    if mode != "custom":
        raise ValueError(f"{role_label} 绑定模式不合法")
    service_raw = raw.get("service")
    if isinstance(service_raw, dict) and not str(service_raw.get("api_key") or "").strip():
        if existing is None:
            raise ValueError(f"{role_label} 尚未保存过 API Key，请先完整填写")
        # Key 与目的地绑定：服务地址或服务类型变更时必须重新输入 Key，已保存
        # 凭据绝不沿用到新目的地（防"换地址窃用旧 Key"）。先按新表单完整校验，
        # 再比较目的地，保证错误原因与校验顺序一致。
        candidate = parse_service_spec(
            {**service_raw, "api_key": existing.api_key},
            role_label=role_label,
            allow_private=allow_private,
        )
        if candidate.base_url != existing.base_url:
            raise ValueError(
                f"{role_label} 的服务地址已变更，凭据不会沿用到新地址，请重新填写 API Key"
            )
        if candidate.provider_kind != existing.provider_kind:
            raise ValueError(
                f"{role_label} 的服务类型已变更，凭据不会沿用，请重新填写 API Key"
            )
        return RoleBinding(mode="custom", service=candidate)
    return RoleBinding(
        mode="custom",
        service=parse_service_spec(service_raw, role_label=role_label, allow_private=allow_private),
    )


# ---------------------------------------------------------------- 路由解析

_default_client: OpenAI | None = None
_default_client_lock = threading.Lock()


def _server_default_client() -> OpenAI:
    global _default_client
    if _default_client is None:
        with _default_client_lock:
            if _default_client is None:
                # 缺 key 时不应在构造期崩溃；真实调用才会得到可读的认证失败。
                _default_client = OpenAI(
                    api_key=API_KEY or "sk-unconfigured",
                    base_url=BASE_URL,
                    timeout=model_timeout_seconds(),
                )
    return _default_client


_clients: OrderedDict[str, OpenAI] = OrderedDict()
_clients_lock = threading.Lock()
_CLIENT_CACHE_MAX = 16


def _custom_client(base_url: str, api_key: str, addresses: tuple[str, ...] = ()) -> OpenAI:
    # 缓存键含已校验地址集合：DNS 结果变化时自动淘汰旧 client（钉扎随动）。
    key = hashlib.sha256(f"{base_url}\n{api_key}\n{','.join(addresses)}".encode()).hexdigest()
    with _clients_lock:
        cached = _clients.get(key)
        if cached is not None:
            _clients.move_to_end(key)
            return cached
    timeout = model_timeout_seconds()
    # 用户显式配置的端点直连，不读系统代理（无校验网络代理不在本轮范围）；
    # 凭据绝不随重定向转发到其他域名（SSRF 防线的一部分）。有已校验地址时
    # 拨号钉扎到这些地址，消除校验与连接之间的 DNS 重绑定窗口。
    http_client = (
        pinned_http_client(addresses, timeout=timeout)
        if addresses
        else httpx.Client(follow_redirects=False, timeout=timeout, trust_env=False)
    )
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        http_client=http_client,
    )
    with _clients_lock:
        _clients[key] = client
        while len(_clients) > _CLIENT_CACHE_MAX:
            _clients.popitem(last=False)
    return client


def _resolve_role(binding: RoleBinding, *, slot: str, revision: int) -> ResolvedRole:
    if binding.mode != "custom" or binding.service is None:
        return ResolvedRole(
            client=_server_default_client(),
            model_id=NARRATIVE_MODEL if slot == "narrative" else JUDGEMENT_MODEL,
            window_tokens=context_window_tokens(),
            window_source="legacy_default",
            max_output_tokens=max_output_tokens(),
            provider_kind="server_default",
            binding_id="server_default",
            base_url_host="",
            config_revision=revision,
        )
    service = binding.service
    # 回合解析时复核出站目标（DNS 可能已变化）；失败即阻断，不静默回落。
    # allow_private 是保存时由作用域烧入的（本地 True / 云端 False）。
    endpoint = validate_outbound_url(service.base_url, allow_private=service.allow_private)
    return ResolvedRole(
        client=_custom_client(service.base_url, service.api_key, endpoint.addresses),
        model_id=service.model_id,
        window_tokens=service.window_tokens,
        window_source="manual" if service.window_tokens else "unknown",
        max_output_tokens=service.max_output_tokens or max_output_tokens(),
        provider_kind=service.provider_kind,
        binding_id=service.binding_id,
        base_url_host=endpoint.host,
        config_revision=revision,
    )


def readiness_error(settings: EffectiveSettings) -> str | None:
    """BYOK-only 作用域的开局就绪检查；None 表示所需角色均已绑定自定义服务。"""
    if not settings.byok_required:
        return None
    missing = [
        label
        for slot, label in (("narrative", "叙述模型"), ("judgement", "裁决模型"))
        if getattr(settings, slot).mode != "custom"
    ]
    if not missing:
        return None
    return (
        f"尚未配置{'、'.join(missing)}：云端为自带 Key（BYOK）模式，"
        "请在模型设置中绑定你自己的模型服务，平台不提供兜底"
    )


def resolve_routes(settings: EffectiveSettings) -> ModelRoutes:
    # BYOK-only：缺绑定时 fail-closed，绝不回落平台凭据（费用归属防线）。
    blocked = readiness_error(settings)
    if blocked:
        raise RouteNotConfiguredError(blocked)
    return ModelRoutes(
        narrative=_resolve_role(settings.narrative, slot="narrative", revision=settings.revision),
        judgement=_resolve_role(settings.judgement, slot="judgement", revision=settings.revision),
        revision=settings.revision,
    )


def host_of(base_url: str) -> str:
    """公开投影用的主机名（不携带路径/端口之外的任何信息）。"""
    from urllib.parse import urlsplit

    return (urlsplit(base_url).hostname or "").lower()


def pooled_client(base_url: str, api_key: str, addresses: tuple[str, ...] = ()) -> OpenAI:
    """测试连接等临时调用复用同一缓存 client（密钥轮换自动淘汰）。"""
    return _custom_client(base_url, api_key, addresses)


def server_default_client() -> OpenAI:
    return _server_default_client()


def with_capabilities(service: ServiceSpec, capabilities: dict) -> ServiceSpec:
    from dataclasses import replace

    return replace(service, capabilities=dict(capabilities))


# ---------------------------------------------------------------- engine 冻结

RouteResolver = Callable[[], EffectiveSettings]


class _BlockedClient:
    """占位 client：BYOK 未配置时替换 engine 上的真实 client。

    任何属性访问（client.chat…）立即抛 RouteNotConfiguredError，保证
    缺配置时不可能穿透到构造期的平台默认 client（fail-closed）。
    """

    def __init__(self, message: str) -> None:
        self._message = message

    def __getattr__(self, name: str):
        raise RouteNotConfiguredError(self._message)


def apply_pending_routes(engine: object) -> None:
    """回合边界刷新冻结路由；无 resolver 时保持 env 默认行为不变。

    BYOK-only 作用域下配置缺失不打断回合入口：记录阻断原因并把 client
    换成立即失败的占位，首个模型调用点以可读错误终止本回合。
    """
    resolver = getattr(engine, "route_resolver", None)
    if resolver is None:
        return
    settings = resolver()
    current = getattr(engine, "_model_routes", None)
    if current is not None and current.revision == settings.revision:
        return
    try:
        routes = resolve_routes(settings)
    except RouteNotConfiguredError as exc:
        blocked = _BlockedClient(str(exc))
        engine._routes_blocked_error = str(exc)
        engine._model_routes = None
        engine.client = blocked
        engine.judgement_client = blocked
        return
    engine._routes_blocked_error = None
    engine._model_routes = routes
    engine.client = routes.narrative.client
    engine.judgement_client = routes.judgement.client
    engine.narrative_model = routes.narrative.model_id
    engine.judgement_model = routes.judgement.model_id


def engine_byok_only(host: object) -> bool:
    """该引擎是否处于 BYOK-only 作用域（云端房间/单人）；本地部署恒为 False。"""
    return bool(getattr(getattr(host, "route_resolver", None), "byok_only", False))


def _routes_of(host: object) -> ModelRoutes | None:
    return getattr(host, "_model_routes", None)


def activate_route_slot(engine: object, slot: str) -> None:
    """流式阶段的 client 指针切换；routes 集合本身整回合冻结不变。"""
    routes = _routes_of(engine)
    if routes is None:
        return
    engine.client = routes.role(slot).client


def client_for_role(host: object, call_role: str) -> OpenAI:
    """按调用角色取冻结路由的 client；无路由时回退 engine 默认 client。"""
    blocked = getattr(host, "_routes_blocked_error", None)
    if blocked:
        raise RouteNotConfiguredError(blocked)
    routes = _routes_of(host)
    if routes is None:
        return host.client
    slot = ROLE_SLOT.get(call_role, "narrative")
    return routes.role(slot).client


def model_for_role(host: object, call_role: str) -> str:
    blocked = getattr(host, "_routes_blocked_error", None)
    if blocked:
        raise RouteNotConfiguredError(blocked)
    routes = _routes_of(host)
    if routes is None:
        fallback = getattr(host, "judgement_model", JUDGEMENT_MODEL)
        return (
            fallback
            if ROLE_SLOT.get(call_role) == "judgement"
            else getattr(host, "narrative_model", NARRATIVE_MODEL)
        )
    slot = ROLE_SLOT.get(call_role, "narrative")
    return routes.role(slot).model_id


def route_public_info(host: object, call_role: str) -> dict:
    """诊断 enrich 用的公开路由元数据（不含任何凭据）。"""
    routes = _routes_of(host)
    if routes is None:
        return {}
    slot = ROLE_SLOT.get(call_role, "narrative")
    role = routes.role(slot)
    return {
        "config_revision": role.config_revision,
        "binding_id": role.binding_id,
        "provider_kind": role.provider_kind,
        "window_tokens": role.window_tokens,
        "window_source": role.window_source,
        "reserved_output_tokens": role.max_output_tokens,
    }


def with_route_info(host: object, call_role: str, entry: dict) -> dict:
    """给直接落账的诊断条目并入路由元数据（非流式调用点使用）。"""
    info = route_public_info(host, call_role)
    return {**entry, **info} if info else entry


# ---------------------------------------------------------------- 本地存储


def default_settings() -> EffectiveSettings:
    default = RoleBinding(mode="default", service=None)
    return EffectiveSettings(narrative=default, judgement=default, revision=0)


def settings_to_json(settings: EffectiveSettings, *, include_secrets: bool) -> dict:
    def role_json(binding: RoleBinding) -> dict:
        if binding.mode != "custom" or binding.service is None:
            return {"mode": "default"}
        service = binding.service
        data = {
            "mode": "custom",
            "service": {
                "label": service.label,
                "provider_kind": service.provider_kind,
                "base_url": service.base_url,
                "model_id": service.model_id,
                "window_tokens": service.window_tokens,
                "max_output_tokens": service.max_output_tokens,
                "capabilities": service.capabilities,
            },
        }
        if include_secrets:
            data["service"]["api_key"] = service.api_key
        return data

    return {
        "revision": settings.revision,
        "narrative": role_json(settings.narrative),
        "judgement": role_json(settings.judgement),
    }


def settings_from_json(raw: dict, *, allow_private: bool) -> EffectiveSettings:
    def role(raw_role: object, role_label: str) -> RoleBinding:
        if not isinstance(raw_role, dict):
            return RoleBinding(mode="default", service=None)
        return parse_role_binding(raw_role, role_label=role_label, allow_private=allow_private)

    return EffectiveSettings(
        narrative=role(raw.get("narrative"), "叙述模型"),
        judgement=role(raw.get("judgement"), "裁决模型"),
        revision=int(raw.get("revision") or 0),
    )


def load_local_settings(path) -> EffectiveSettings:
    """读取本地配置文件；缺失回退平台默认，损坏抛可读错误（不动 .env.json）。"""
    file = Path(path)
    if not file.exists():
        return default_settings()
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("根节点必须是对象")
        return settings_from_json(raw, allow_private=True)
    except (OSError, ValueError) as exc:
        raise ValueError(f"本地模型配置损坏: {exc}") from exc


def save_local_settings(path, settings: EffectiveSettings) -> None:
    """原子写入 0600 本地配置文件（与 .env.json 同级信任边界）。"""
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{file.name}.", suffix=".tmp", dir=file.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                settings_to_json(settings, include_secrets=True),
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, file)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


class LocalRouteResolver:
    """本地作用域 resolver：按文件 mtime 缓存，避免每回合重复解析与 DNS。"""

    # 本地部署允许平台默认（部署者自己的凭据），非 BYOK-only。
    byok_only = False

    def __init__(self, path) -> None:
        self._path = Path(path)
        self._mtime_ns: int | None = -1
        self._cached = default_settings()

    def __call__(self) -> EffectiveSettings:
        try:
            mtime: int | None = self._path.stat().st_mtime_ns
        except OSError:
            mtime = None
        if mtime != self._mtime_ns:
            self._mtime_ns = mtime
            self._cached = default_settings() if mtime is None else load_local_settings(self._path)
        return self._cached
