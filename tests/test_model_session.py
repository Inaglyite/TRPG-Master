import unittest

from src.model_session import ModelSession


class _Stream:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class ModelSessionTests(unittest.TestCase):
    def test_reset_replaces_history_diagnostics_and_cancellation(self):
        session = ModelSession(messages=[{"role": "user", "content": "old"}])
        session.append_diagnostic({"status": "old"})
        session.cancel()

        session.reset({"role": "system", "content": "new"})

        self.assertEqual([{"role": "system", "content": "new"}], session.messages)
        self.assertEqual([], session.diagnostics)
        self.assertFalse(session.cancellation_requested)

    def test_cancel_closes_only_current_stream(self):
        session = ModelSession()
        old_stream = _Stream()
        current_stream = _Stream()
        session.set_active_stream(old_stream)
        session.set_active_stream(current_stream)

        session.cancel()

        self.assertFalse(old_stream.closed)
        self.assertTrue(current_stream.closed)
        self.assertTrue(session.cancellation_requested)

    def test_stale_clear_cannot_detach_new_stream(self):
        session = ModelSession()
        old_stream = _Stream()
        current_stream = _Stream()
        session.set_active_stream(old_stream)
        session.set_active_stream(current_stream)

        session.clear_active_stream(old_stream)
        session.cancel()

        self.assertTrue(current_stream.closed)


if __name__ == "__main__":
    unittest.main()
