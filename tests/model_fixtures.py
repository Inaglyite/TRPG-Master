"""H4 离线 replay 共享设施：归一化 chunk 记录 ↔ 可重放 client。

fixture 格式即 ``src.provider_adapter.normalize_chunk_record`` 的输出
（JSONL，每行一个 chunk 记录）。回放 client 把记录还原成流式 chunk，
喂给真实的 ModelStreamer，验证投影/工具序列/错误分级的确定性。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "model_streams"


def load_fixture(name: str) -> list[dict]:
    path = FIXTURE_DIR / name
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def build_chunk(record: dict) -> SimpleNamespace:
    """normalize_chunk_record 的逆变换（SimpleNamespace 形态的 SDK chunk）。"""
    tool_deltas = None
    if record.get("tool_deltas"):
        tool_deltas = [
            SimpleNamespace(
                index=delta.get("index", 0),
                id=delta.get("id"),
                function=SimpleNamespace(
                    name=delta.get("name"),
                    arguments=delta.get("arguments"),
                ),
            )
            for delta in record["tool_deltas"]
        ]
    has_delta = any(key in record for key in ("content", "reasoning", "tool_deltas"))
    delta = (
        SimpleNamespace(
            content=record.get("content"),
            reasoning_content=record.get("reasoning"),
            tool_calls=tool_deltas,
        )
        if has_delta
        else None
    )
    choices = []
    if delta is not None or record.get("finish_reason"):
        choices = [
            SimpleNamespace(delta=delta, finish_reason=record.get("finish_reason"))
        ]
    usage = SimpleNamespace(**record["usage"]) if record.get("usage") else None
    return SimpleNamespace(choices=choices, usage=usage)


class ReplayClient:
    """按录制记录依次产出流的可重放 client；create 调用一次消费一段流。"""

    def __init__(self, streams: list[list[dict]]):
        self._streams = list(streams)
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **_kwargs):
        self.calls += 1
        if not self._streams:
            raise AssertionError("ReplayClient 的录制流已耗尽")
        records = self._streams.pop(0)
        return iter(build_chunk(record) for record in records)


def replay_client(stream: list[dict]) -> ReplayClient:
    return ReplayClient([stream])


def streamer_host(client, *, world_id: str = "w-replay"):
    """ModelStreamer 的最小 host：真实 prepare/issue/diagnostics 链路。"""
    from src.model_request import StreamPolicy

    diagnostics: list[dict] = []
    errors: list[str] = []
    narratives: list[str] = []
    host = SimpleNamespace(
        client=client,
        messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        context=SimpleNamespace(world_id=world_id, database_url=None),
        cb=SimpleNamespace(
            on_error=errors.append,
            on_narrative=lambda text, *_a: narratives.append(text),
            on_speaker_segment=lambda *_a, **_k: None,
        ),
        _tool_request_step=0,
        _active_turn_id=None,
        _append_model_diagnostic=diagnostics.append,
        _clear_active_stream=lambda _s: None,
        _set_active_stream=lambda _s: None,
        turn_cancellation_requested=lambda: False,
        turn_cancelled_error=RuntimeError,
        raise_if_turn_cancelled=lambda: None,
    )
    host.diagnostics = diagnostics
    host.errors = errors
    host.narratives = narratives
    host.policy = StreamPolicy(
        dynamic_tools=False,
        stream_usage=False,
        prompt_profile="test",
        thinking_type=None,
    )
    return host


def run_stream(host, *, retry_on_empty: bool = False):
    from src.model_streamer import ModelStreamer

    streamer = ModelStreamer(
        host,
        log_error=lambda _m: None,
        log_model_call=lambda *_a, **_k: None,
        sleep=lambda _s: None,
    )
    return streamer.stream(
        "test-model",
        policy=host.policy,
        enable_tools=True,
        retry_on_empty=retry_on_empty,
    )
