"""出站连接的 IP 钉扎：自定义模型服务只连接校验过的解析地址。

egress_guard 在保存/测试/回合解析时校验 DNS 结果，但校验与 httpx 实际
拨号之间存在 DNS 重绑定窗口（响应在两次查询间被换成内网地址）。本模块
替换 HTTP 层的拨号实现：只拨已校验的地址，请求行与 Host 头、TLS 的
SNI/证书校验仍针对原始域名，因此证书语义不变、重绑定无处落脚。

实现基于 httpcore 的 network_backend 扩展点；ResponseStream 与异常映射
复用 httpx 自带传输的私有助手（与 httpx 0.28 / httpcore 1.0 锁定版本
同步维护，升级依赖时需复核本模块）。
"""

from __future__ import annotations

import socket
import time
import typing

import httpcore
import httpx
from httpcore._backends.sync import SyncStream
from httpcore._exceptions import ConnectError
from httpx._transports.default import ResponseStream, map_httpcore_exceptions


class _PinnedNetworkBackend:
    """httpcore NetworkBackend  duck-type：忽略目标主机名，只拨已校验地址。"""

    def __init__(self, addresses: tuple[str, ...]) -> None:
        self._addresses = addresses

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[tuple] | None = None,
    ) -> SyncStream:
        # host 参数被刻意忽略：拨号目标只能是校验时固定的地址集合。
        source_address = None if local_address is None else (local_address, 0)
        last_error: OSError | None = None
        for address in self._addresses:
            try:
                sock = socket.create_connection(
                    (address, port), timeout, source_address=source_address
                )
            except OSError as exc:
                last_error = exc
                continue
            for option in socket_options or ():
                sock.setsockopt(*option)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return SyncStream(sock)
        raise ConnectError(
            f"无法连接到已校验地址 {', '.join(self._addresses) or '(空)'}"
        ) from last_error

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: typing.Iterable[tuple] | None = None,
    ) -> typing.NoReturn:
        raise ConnectError("Unix socket 目标不在自定义模型服务的允许范围")

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class PinnedHTTPTransport(httpx.BaseTransport):
    """只拨已校验 IP 的同步传输；连接池语义对齐 httpx.HTTPTransport。"""

    def __init__(self, addresses: tuple[str, ...]) -> None:
        if not addresses:
            raise ValueError("PinnedHTTPTransport 需要至少一个已校验地址")
        self._pool = httpcore.ConnectionPool(
            network_backend=_PinnedNetworkBackend(tuple(addresses)),
            max_connections=10,
            max_keepalive_connections=5,
            keepalive_expiry=30.0,
            retries=0,
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        req = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        with map_httpcore_exceptions():
            resp = self._pool.handle_request(req)
        return httpx.Response(
            status_code=resp.status,
            headers=resp.headers,
            stream=ResponseStream(resp.stream),
            extensions=resp.extensions,
        )

    def close(self) -> None:
        self._pool.close()


def pinned_http_client(addresses: tuple[str, ...], *, timeout: float) -> httpx.Client:
    """构造只拨已校验地址、不跟随重定向、不读环境代理的直连 client。"""
    return httpx.Client(
        transport=PinnedHTTPTransport(addresses),
        follow_redirects=False,
        timeout=timeout,
        trust_env=False,
    )
