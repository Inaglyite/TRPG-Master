"""One HTTP boundary for all model calls, preserving validated transport/DNS.

The application continues consuming Chat-shaped results. No local proxy server,
SDK monkey patch, redirect, additional credential store, or retry loop is added.
"""

from __future__ import annotations

import json

import httpx

from . import anthropic_protocol, responses_protocol
from .pinned_transport import PinnedHTTPTransport
from .provider_profiles import normalize_chat_body, provider_for_url
from .provider_streams import ProviderStream
from .wire_history import WireHistory

API_FORMATS = frozenset({"chat_completions", "anthropic_messages", "responses"})


def default_api_format(base_url: str, provider_kind: str) -> str:
    return "anthropic_messages" if provider_for_url(base_url, provider_kind) == "anthropic" else "chat_completions"


class ProviderTransport(httpx.BaseTransport):
    def __init__(self, transport: httpx.BaseTransport, *, provider_kind: str = "openai_compatible", api_format: str = "chat_completions"):
        if api_format not in API_FORMATS:
            raise ValueError("不支持的模型接口格式")
        self.transport, self.provider_kind, self.api_format = transport, provider_kind, api_format
        self.history = WireHistory()

    def close(self) -> None:
        self.history.clear()
        self.transport.close()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        original = request
        native_anthropic = self.api_format == "anthropic_messages"
        if native_anthropic:
            headers = dict(request.headers)
            token = headers.pop("authorization", "").removeprefix("Bearer ")
            headers["x-api-key"] = token
            headers["anthropic-version"] = "2023-06-01"
            path = request.url.path
            if path.endswith("/models") and not path.removesuffix("/models").endswith("/v1"):
                path = path.removesuffix("/models") + "/v1/models"
            request = httpx.Request(request.method, request.url.copy_with(path=path), headers=headers,
                                    content=request.read(), extensions=request.extensions)
        if request.method != "POST" or not request.url.path.endswith("/chat/completions"):
            return self.transport.handle_request(request)
        body = json.loads(request.read())
        provider = provider_for_url(str(request.url), self.provider_kind)
        body = normalize_chat_body(body, provider)
        prefix = request.url.path.removesuffix("/chat/completions")
        if self.api_format == "anthropic_messages":
            body = anthropic_protocol.request_body(body, self.history)
            path = prefix + ("/messages" if prefix.endswith("/v1") else "/v1/messages")
        elif self.api_format == "responses":
            body = responses_protocol.request_body(body, self.history)
            path = prefix + "/responses"
        else:
            path = request.url.path
            body["messages"] = self._chat_history(body.get("messages", []))
        headers = {k: v for k, v in request.headers.items() if k not in {"content-length", "content-encoding"}}
        request = httpx.Request(request.method, request.url.copy_with(path=path), headers=headers,
                                json=body, extensions=request.extensions)
        response = self.transport.handle_request(request)
        if not response.is_success:
            return response
        response_headers = {key: value for key, value in response.headers.items()
                            if key not in {"content-type", "content-length", "content-encoding", "transfer-encoding"}}
        if body.get("stream"):
            return httpx.Response(response.status_code, headers={**response_headers, "content-type": "text/event-stream"},
                                  stream=ProviderStream(response, self.api_format, self.history, body["model"]), request=original)
        try:
            raw = json.loads(response.read())
            if self.api_format == "anthropic_messages":
                raw = anthropic_protocol.response_body(raw, self.history)
            elif self.api_format == "responses":
                raw = responses_protocol.response_body(raw, self.history)
            else:
                for choice in raw.get("choices") or []:
                    message = choice.get("message") or {}
                    self.history.remember(message.get("tool_calls") or [], message)
            return httpx.Response(response.status_code, headers=response_headers, json=raw, request=original)
        finally:
            response.close()

    def _chat_history(self, messages: list[dict]) -> list[dict]:
        result = []
        for message in messages:
            calls = message.get("tool_calls") or []
            saved = self.history.recall(calls) if calls else None
            if isinstance(saved, dict):
                message = dict(message)
                for key in ("reasoning_content", "reasoning_details"):
                    if key in saved:
                        message[key] = saved[key]
                original_calls = {call["id"]: call for call in saved.get("tool_calls", [])}
                message["tool_calls"] = [dict(call, **{"extra_content": original_calls[call["id"]]["extra_content"]})
                                         if original_calls.get(call["id"], {}).get("extra_content") else call for call in calls]
            result.append(message)
        return result


def model_http_client(*, timeout: float, addresses: tuple[str, ...] = (), provider_kind: str = "openai_compatible", api_format: str = "chat_completions") -> httpx.Client:
    underlying = PinnedHTTPTransport(addresses) if addresses else httpx.HTTPTransport(trust_env=False)
    return httpx.Client(transport=ProviderTransport(underlying, provider_kind=provider_kind, api_format=api_format),
                        timeout=timeout, follow_redirects=False, trust_env=False)
