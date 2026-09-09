"""egress_guard：BYOK 出站目标校验（SSRF 防线）。"""

from __future__ import annotations

import socket

import pytest

from src.ai.model.egress_guard import EgressRejected, validate_outbound_url


def fake_dns(*ips: str):
    def _getaddrinfo(host, port, type=socket.SOCK_STREAM):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

    return _getaddrinfo


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns("93.184.216.34"))


def test_https_public_ok_normalizes():
    endpoint = validate_outbound_url("HTTPS://API.DeepSeek.com/v1/?x=1", allow_private=False)
    assert endpoint.base_url == "https://api.deepseek.com/v1"
    assert endpoint.scheme == "https"
    assert endpoint.port == 443
    assert endpoint.addresses == ("93.184.216.34",)


def test_cloud_rejects_http_with_readable_reason():
    with pytest.raises(EgressRejected, match="仅允许 https"):
        validate_outbound_url("http://api.example.com/v1", allow_private=False)


def test_cloud_rejects_non_standard_port():
    with pytest.raises(EgressRejected, match="端口"):
        validate_outbound_url("https://api.example.com:3000/v1", allow_private=False)


def test_cloud_rejects_userinfo():
    with pytest.raises(EgressRejected, match="用户名/密码"):
        validate_outbound_url("https://key@api.example.com/v1", allow_private=False)


@pytest.mark.parametrize(
    "ip",
    ["127.0.0.1", "10.0.0.8", "192.168.1.10", "169.254.169.254", "100.64.1.1", "::1"],
)
def test_cloud_rejects_non_global_addresses(monkeypatch, ip):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns(ip))
    with pytest.raises(EgressRejected, match="非公网"):
        validate_outbound_url("https://api.example.com/v1", allow_private=False)


def test_cloud_rejects_when_any_dns_answer_is_private(monkeypatch):
    # 一个答案公网、一个私网：任一非公网即拒（DNS 重绑定防线）。
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns("93.184.216.34", "192.168.0.1"))
    with pytest.raises(EgressRejected, match="非公网"):
        validate_outbound_url("https://api.example.com/v1", allow_private=False)


def test_dns_failure_is_readable(monkeypatch):
    def boom(host, port, type=socket.SOCK_STREAM):
        raise socket.gaierror("name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(EgressRejected, match="无法解析服务地址"):
        validate_outbound_url("https://no-such-host.invalid/v1", allow_private=False)


def test_local_private_allows_http_loopback_any_port():
    endpoint = validate_outbound_url("http://127.0.0.1:11434/v1", allow_private=True)
    assert endpoint.scheme == "http"
    assert endpoint.port == 11434
    # 本地模式不记录解析结果（无须云端审计口径）
    assert endpoint.addresses == ()


def test_rejects_blank_and_whitespace():
    with pytest.raises(EgressRejected):
        validate_outbound_url("   ", allow_private=True)
    with pytest.raises(EgressRejected):
        validate_outbound_url("https://api.example.com/v 1", allow_private=False)


# ---------------------------------------------------------------- 运营白名单与钉扎


def test_operator_allowlist_permits_private_host_but_still_resolves(monkeypatch):
    monkeypatch.setenv("TRPG_EGRESS_ALLOWED_PRIVATE_HOSTS", "llm.internal, 127.0.0.1")
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns("127.0.0.1"))
    endpoint = validate_outbound_url("http://127.0.0.1:11434/v1", allow_private=False)
    assert endpoint.scheme == "http"
    assert endpoint.addresses == ("127.0.0.1",)


def test_operator_allowlist_does_not_cover_unlisted_hosts(monkeypatch):
    monkeypatch.setenv("TRPG_EGRESS_ALLOWED_PRIVATE_HOSTS", "llm.internal")
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns("127.0.0.1"))
    with pytest.raises(EgressRejected, match="非公网|http"):
        validate_outbound_url("http://127.0.0.1:11434/v1", allow_private=False)


def test_allowlisted_host_addresses_feed_pinning(monkeypatch):
    """白名单主机也返回解析地址：连接钉扎不因白名单而失效。"""
    monkeypatch.setenv("TRPG_EGRESS_ALLOWED_PRIVATE_HOSTS", "llm.internal")
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns("10.1.2.3", "10.1.2.4"))
    endpoint = validate_outbound_url("http://llm.internal:8080/v1", allow_private=False)
    assert endpoint.addresses == ("10.1.2.3", "10.1.2.4")


def test_pinned_transport_dials_only_validated_addresses(monkeypatch):
    """钉扎传输：主机名任意，实际只连接已校验地址，Host 头保留原主机名。"""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from src.ai.model.pinned_transport import pinned_http_client

    monkeypatch.undo()  # 撤销 autouse 假 DNS：本测试需要真实 socket

    seen_hosts: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen_hosts.append(self.headers.get("Host", ""))
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = pinned_http_client(("127.0.0.1",), timeout=5)
        response = client.get(f"http://example.test:{port}/v1/models")
        assert response.status_code == 200
        assert seen_hosts == [f"example.test:{port}"]
        client.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_pinned_transport_refuses_unlisted_dial_target(monkeypatch):
    """已校验地址不可达时报连接错误，而不是回退去解析原主机名。"""
    import httpx

    from src.ai.model.pinned_transport import pinned_http_client

    monkeypatch.undo()  # 真实 socket：地址不可达应直接 ConnectError

    client = pinned_http_client(("127.0.0.2",), timeout=1)
    try:
        with pytest.raises(httpx.ConnectError):
            client.get("http://example.test:9/v1/models")
    finally:
        client.close()
