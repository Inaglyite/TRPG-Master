import threading
import unittest
from types import SimpleNamespace

from src.engine import GameEngine, TurnCancelledError


class BlockingStream:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.closed = threading.Event()

    def __iter__(self):
        return self

    def __next__(self):
        self.entered.set()
        self.closed.wait(timeout=3)
        raise RuntimeError("stream closed")

    def close(self) -> None:
        self.closed.set()


class TurnCancellationTests(unittest.TestCase):
    def test_cancel_active_turn_closes_blocked_model_stream(self):
        stream = BlockingStream()
        engine = GameEngine.__new__(GameEngine)
        engine.messages = [{"role": "system", "content": "test"}]
        engine.client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_kwargs: stream)
            )
        )
        engine.cb = SimpleNamespace(
            on_narrative=lambda _text: None,
            on_error=lambda _text: None,
        )
        engine._turn_diagnostics = []
        engine.clear_turn_cancellation()

        failures: list[BaseException] = []

        def run_stream() -> None:
            try:
                engine._stream_llm("test-model", _retry_on_empty=False)
            except BaseException as exc:
                failures.append(exc)

        worker = threading.Thread(target=run_stream)
        worker.start()
        self.assertTrue(stream.entered.wait(timeout=1))

        engine.cancel_active_turn()
        worker.join(timeout=1)

        self.assertFalse(worker.is_alive())
        self.assertTrue(stream.closed.is_set())
        self.assertEqual(1, len(failures))
        self.assertIsInstance(failures[0], TurnCancelledError)


if __name__ == "__main__":
    unittest.main()
