import json
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from src.app import diagnostic_log as log
from tools.diagnose_turn import read_timeline


@pytest.fixture
def receipts(tmp_path, monkeypatch):
    monkeypatch.setattr(log, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(log, "_HANDLER", None)
    monkeypatch.setattr(log, "_WARNED", False)
    monkeypatch.delenv("TRPG_DIAGNOSTICS", raising=False)
    yield log.LOG_DIR
    if log._HANDLER is not None:
        log._HANDLER.close()


def host():
    return SimpleNamespace(context=SimpleNamespace(world_id="world-1", module_name="demo@1"),
                           _active_turn_id="turn-1", _turn_request_id="request-1")


def test_receipts_correlate_and_exclude_content_and_credentials(receipts):
    log.emit("model.completed", host(), model="test-model", input_tokens=123,
             prompt="NPC secret", api_key="private-key", messages=[{"content": "private-note"}],
             request_id="sk-fake-123456789", status="completed", elapsed_ms=12)
    text = (receipts / "diagnostics.jsonl").read_text()
    row = json.loads(text)
    assert row["world_id"] == "world-1" and row["turn_id"] == "turn-1"
    assert row["input_tokens"] == 123 and row["request_id"] == "***"
    assert all(secret not in text for secret in ("NPC secret", "private-key", "private-note", "sk-fake"))
    if os.name != "nt":
        assert (receipts.stat().st_mode & 0o777) == 0o700
        assert ((receipts / "diagnostics.jsonl").stat().st_mode & 0o777) == 0o600


def test_tool_failure_and_success_are_observed_without_changing_results(receipts):
    failure = ValueError("private exception text sk-fake-123456789")

    @log.trace_tool
    def execute(engine, name, args):
        if name == "fail":
            raise failure
        return json.dumps({"ok": False, "code": "blocked", "text": "private result", "elapsed_ms": 999})

    result = execute(host(), "inspect", {"path": "flags.pc_named", "value": "secret"})
    assert json.loads(result)["text"] == "private result"
    with pytest.raises(ValueError) as caught:
        execute(host(), "fail", {})
    assert caught.value is failure
    rows, _ = read_timeline(receipts, turn="turn-1")
    assert [r["event"] for r in rows] == ["tool.started", "tool.completed", "tool.started", "tool.failed"]
    assert rows[0]["call_id"] == rows[1]["call_id"]
    assert rows[-1]["frames"][-1]["function"] == "execute"
    assert rows[1]["code"] == "blocked" and rows[1]["ok"] is False
    text = (receipts / "diagnostics.jsonl").read_text()
    assert "private" not in text and "secret" not in text


def test_failed_log_write_does_not_break_tool(receipts, monkeypatch):
    def broken():
        raise OSError("disk full")
    monkeypatch.setattr(log, "_handler", broken)

    @log.trace_tool
    def execute(engine, name, args):
        return "ok"

    assert execute(host(), "test", {}) == "ok"


def test_parallel_writes_are_valid_json_and_rotation_is_bounded(receipts):
    handler = log._handler()
    handler.maxBytes = 1000
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: log.emit("test.event", host(), input_chars=n), range(50)))
    files = list(receipts.glob("diagnostics.jsonl*"))
    assert 1 < len(files) <= 4
    rows, skipped = read_timeline(receipts, turn="turn-1")
    assert rows and skipped == 0
    assert all(row["world_id"] == "world-1" for row in rows)


def test_stage_records_are_written_and_disable_switch_works(receipts, monkeypatch):
    from src.gameplay.turn_diagnostics import record_stage

    engine = host()
    record_stage(engine, "dialogue_validation", "rejected", code="speaker_absent")
    rows, _ = read_timeline(receipts)
    assert rows[-1]["code"] == "speaker_absent"
    monkeypatch.setenv("TRPG_DIAGNOSTICS", "0")
    log.emit("hidden", engine)
    assert len(read_timeline(receipts)[0]) == 1


def test_reader_filters_rotations_and_tolerates_partial_lines(receipts):
    log.emit("turn.started", host(), kind="action")
    handler = log._handler()
    handler.doRollover()
    log.emit("turn.committed", host())
    with (receipts / "diagnostics.jsonl").open("a") as stream:
        stream.write('{"partial":')
    rows, skipped = read_timeline(receipts, world="world-1", turn="turn-1", limit=1)
    assert [r["event"] for r in rows] == ["turn.committed"]
    assert skipped == 1
    assert read_timeline(receipts, world="other")[0] == []


def test_output_content_is_redacted_bounded_and_can_be_disabled(receipts, monkeypatch):
    raw = '模型原文 sk-test-123456789\n' + '甲' * 70000
    log.emit("dialogue.raw", host(), output_id="output-1", output_text=raw)
    log.emit("unrelated.event", host(), output_text="must not be recorded")
    row = read_timeline(receipts)[0][0]
    assert row["content_recorded"] and row["content_truncated"]
    assert len(row["output_text"]) == 65536
    assert "sk-test" not in row["output_text"]
    assert len(row["output_sha256"]) == 64
    assert "output_text" not in read_timeline(receipts)[0][1]
    monkeypatch.setenv("TRPG_DIAGNOSTIC_CONTENT", "0")
    log.emit("dialogue.raw", host(), output_text=raw)
    disabled = read_timeline(receipts)[0][-1]
    assert not disabled["content_recorded"] and "output_text" not in disabled
    assert disabled["output_sha256"] == row["output_sha256"]


def test_second_turn_preserves_searchable_first_turn_across_rotation(receipts):
    from tools.diagnose_turn import list_turns

    engine = host()
    log.emit("turn.started", engine)
    log.emit("dialogue.raw", engine, output_text="拿上钥匙，先去寓所看看。")
    log.emit("turn.committed", engine)
    log._handler().doRollover()
    engine._active_turn_id = "turn-2"
    log.emit("turn.started", engine)
    log.emit("dialogue.raw", engine, output_text="第二回合回复")
    # A large number of events in a later turn must not hide the earlier turn index.
    for _ in range(110):
        log.emit("turn.stage", engine)
    turns, _ = list_turns(receipts)
    assert [t["turn_id"] for t in turns] == ["turn-1", "turn-2"]
    assert turns[0]["saw_start"] and turns[0]["saw_end"]
    assert not turns[1]["saw_end"]
    matches, _ = read_timeline(receipts, search="拿上钥匙")
    assert len(matches) == 1 and matches[0]["turn_id"] == "turn-1"
    assert len(read_timeline(receipts, turn="turn-1")[0]) == 3


def test_repair_trace_links_raw_rejection_and_emitted_content(receipts):
    from tests.model_fixtures import ReplayClient, run_stream
    from tests.test_dialogue_contract import payload
    from tests.test_dialogue_output import make_host

    engine, _ = make_host("")
    engine._active_turn_id = "turn-repair"
    bad = payload("你可以——\n1. 翻看剪报\n2. 询问细节")
    engine.client = ReplayClient([
        [{"content": bad}, {"finish_reason": "stop"}],
        [{"content": payload()}, {"finish_reason": "stop"}],
    ])
    text, _ = run_stream(engine, retry_on_empty=True)
    rows, _ = read_timeline(receipts, turn="turn-repair")
    rows = [r for r in rows if r["event"].startswith("dialogue.")]
    assert [r["event"] for r in rows] == ["dialogue.raw", "dialogue.rejected", "dialogue.raw", "dialogue.accepted", "dialogue.emitted"]
    assert rows[0]["output_text"] == bad
    assert rows[0]["output_id"] == rows[1]["output_id"]
    assert rows[1]["code"] == "embedded_choice_menu"
    assert rows[2]["output_id"] == rows[3]["output_id"] == rows[4]["output_id"]
    assert rows[0]["output_id"] != rows[2]["output_id"]
    assert rows[3]["output_text"] == text


def test_cli_hides_content_until_explicitly_requested(receipts, monkeypatch, capsys):
    from tools.diagnose_turn import main

    log.emit("dialogue.raw", host(), output_text="剧情内容")
    command = ["diagnose_turn", "--log-dir", str(receipts), "--json"]
    monkeypatch.setattr("sys.argv", command)
    assert main() == 0
    assert "剧情内容" not in capsys.readouterr().out
    monkeypatch.setattr("sys.argv", [*command, "--show-content"])
    assert main() == 0
    assert "剧情内容" in capsys.readouterr().out
