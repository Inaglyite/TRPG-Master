"""BYOK 出站目标校验：自定义模型服务的 SSRF 防线。

云端（账号/房间）只允许公网 HTTPS；本地部署在用户明确选择后才允许
本机/私网 HTTP 推理服务。保存、测试连接、回合解析三处都必须经过
``validate_outbound_url``；DNS 全部解析结果逐一校验，拒绝环回、私网、
链路本地（含 169.254.169.254 云元数据）、保留段与多播地址。

运营商白名单 ``TRPG_EGRESS_ALLOWED_PRIVATE_HOSTS``（逗号分隔主机名/IP）可
放行指定的私网推理端点（如内网推理网关、E2E 桩）；仅部署者可设，用户载荷
无法触及。白名单主机仍返回解析地址供 pinned_transport 钉住最终连接 IP。

DNS 校验与真实连接之间的重绑定窗口由 pinned_transport 消除：HTTP 层只拨
本模块校验过的地址，Host/SNI/TLS 校验仍针对原始域名。
"""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

# 云端只允许常见 HTTPS 端口；本地私网模式不限制端口（本机推理服务端口各异）。
CLOUD_ALLOWED_PORTS = frozenset({443, 8443})

_OPERATOR_ALLOWLIST_ENV = "TRPG_EGRESS_ALLOWED_PRIVATE_HOSTS"


class EgressRejected(ValueError):
    """出站目标被安全策略拒绝；message 必须是可读、可回显的原因。"""


@dataclass(frozen=True)
class ValidatedEndpoint:
    base_url: str  # 规范化（无 userinfo/query/fragment，无尾部 /）
    scheme: str
    host: str
    port: int
    addresses: tuple[str, ...]


def _operator_allowlist() -> frozenset[str]:
    """部署者放行的私网推理主机（env，逗号分隔）；每次调用重读，便于测试隔离。"""
    raw = os.environ.get(_OPERATOR_ALLOWLIST_ENV, "")
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def _check_addresses(host: str, port: int, *, require_global: bool) -> tuple[str, ...]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise EgressRejected(f"无法解析服务地址 {host}: {exc}") from exc
    addresses = tuple(sorted({str(info[4][0]) for info in infos}))
    if not addresses:
        raise EgressRejected(f"无法解析服务地址 {host}")
    if require_global:
        for raw in addresses:
            ip = ipaddress.ip_address(raw)
            # is_global 覆盖环回/私网/链路本地/保留段/CGNAT/多播等全部非公网情形。
            if not ip.is_global:
                raise EgressRejected(f"服务地址 {host} 解析到非公网地址 {raw}，已拒绝")
    return addresses


def validate_outbound_url(raw: object, *, allow_private: bool) -> ValidatedEndpoint:
    """校验并规范化自定义模型服务 Base URL；失败抛 EgressRejected（可读原因）。"""
    text = str(raw or "").strip()
    if not text:
        raise EgressRejected("Base URL 不能为空")
    if len(text) > 300 or any(ch.isspace() for ch in text):
        raise EgressRejected("Base URL 格式不合法")
    parts = urlsplit(text)
    if parts.username or parts.password:
        raise EgressRejected("Base URL 不允许内嵌用户名/密码")
    host = (parts.hostname or "").strip().lower()
    # 运营商白名单主机按私网处理（内网推理网关/E2E 桩），但仍解析地址供钉扎。
    allowlisted = host in _operator_allowlist()
    scheme = parts.scheme.lower()
    if scheme == "https":
        pass
    elif scheme == "http" and (allow_private or allowlisted):
        pass
    elif scheme == "http":
        raise EgressRejected("云端自定义服务仅允许 https://；本机 HTTP 推理仅本地模式可选")
    else:
        raise EgressRejected("Base URL 必须使用 https://")
    if not host:
        raise EgressRejected("Base URL 缺少主机名")
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise EgressRejected("Base URL 端口不合法") from exc
    if not allow_private and not allowlisted and port not in CLOUD_ALLOWED_PORTS:
        raise EgressRejected(f"云端自定义服务仅允许端口 {sorted(CLOUD_ALLOWED_PORTS)}")
    path = parts.path.rstrip("/")
    base_url = f"{scheme}://{host}" + (f":{port}" if parts.port else "") + path
    # 本地私网模式跳过解析与公网检查（用户明确选择本机/内网推理服务）；
    # 白名单主机解析但不做公网检查（地址用于连接钉扎）。
    if allow_private:
        addresses: tuple[str, ...] = ()
    else:
        addresses = _check_addresses(host, port, require_global=not allowlisted)
    return ValidatedEndpoint(
        base_url=base_url,
        scheme=scheme,
        host=host,
        port=port,
        addresses=addresses,
    )
