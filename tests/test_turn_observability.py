"""H4c 观测指标：TurnPerformance.counters 的统一埋点。"""

from __future__ import annotations

from types import SimpleNamespace

from src.provider_adapter import note_model_retry
from src.turn_performance import TurnPerformance, increment_counter


def test_increment_counter_noop_without_active_turn():
    host = SimpleNamespace()
    increment_counter(host, "model_retry_count")  # 无 _turn_performance：静默跳过
    host._turn_performance = None
    increment_counter(host, "model_retry_count")


def test_increment_counter_accumulates_into_snapshot():
    host = SimpleNamespace(_turn_performance=TurnPerformance())
    increment_counter(host, "skill_injection_tokens", 120)
    increment_counter(host, "skill_injection_tokens", 30)
    increment_counter(host, "lore_hit_count", 2)
    counters = host._turn_performance.snapshot()["counters"]
    assert counters["skill_injection_tokens"] == 150
    assert counters["lore_hit_count"] == 2


def test_model_retry_note_records_log_and_counter():
    host = SimpleNamespace(_turn_performance=TurnPerformance())
    note_model_retry(host, "connect_failed", "server", 400)
    note_model_retry(host, "empty_stream", "transport", 400)
    assert host.__dict__["_turn_model_retries"] == [
        {"reason": "connect_failed", "error_class": "server", "backoff_ms": 400},
        {"reason": "empty_stream", "error_class": "transport", "backoff_ms": 400},
    ]
    assert host._turn_performance.snapshot()["counters"]["model_retry_count"] == 2


def test_lore_hit_count_wired_into_engine_retrieval():
    """_retrieve_lore_context 命中条目数进入 lore_hit_count。"""
    from src.engine import GameEngine
    from src.lorebook import LorebookEnvelope

    lorebook = LorebookEnvelope.model_validate(
        {
            "spec": "lorebook_v3",
            "data": {
                "extensions": {},
                "entries": [
                    {
                        "keys": ["台灯"],
                        "content": "台灯忽明忽暗，像在给谁打信号。",
                        "extensions": {},
                        "enabled": True,
                        "insertion_order": 0,
                        "use_regex": False,
                    }
                ],
            },
        }
    )
    engine = GameEngine.__new__(GameEngine)
    engine._lorebook = lorebook
    engine.messages = [{"role": "user", "content": "我检查书桌上的台灯"}]
    engine.context = SimpleNamespace(
        world_store=SimpleNamespace(load=lambda: {"clues_found": {}})
    )
    engine._turn_performance = TurnPerformance()
    engine._turn_lore_diagnostics = None

    selection = engine._retrieve_lore_context("检查台灯")
    assert selection is not None and len(selection.entries) == 1
    counters = engine._turn_performance.snapshot()["counters"]
    assert counters["lore_hit_count"] == 1
