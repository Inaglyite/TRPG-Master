import threading
import unittest

from src.ws_session import PendingReply, SessionTurnGate, TurnRejection


class SessionTurnGateTests(unittest.TestCase):
    def test_lease_blocks_same_session_and_releases_idempotently(self):
        gate = SessionTurnGate(threading.Lock())

        lease, rejection = gate.try_acquire()
        second, second_rejection = gate.try_acquire()

        self.assertIsNotNone(lease)
        self.assertIsNone(rejection)
        self.assertIsNone(second)
        self.assertEqual(TurnRejection.SESSION_BUSY, second_rejection)
        lease.release()
        lease.release()
        replacement, rejection = gate.try_acquire()
        self.assertIsNotNone(replacement)
        self.assertIsNone(rejection)
        replacement.release()

    def test_world_contention_does_not_leak_session_lock(self):
        world_lock = threading.Lock()
        world_lock.acquire()
        gate = SessionTurnGate(world_lock)

        lease, rejection = gate.try_acquire()

        self.assertIsNone(lease)
        self.assertEqual(TurnRejection.WORLD_BUSY, rejection)
        world_lock.release()
        lease, rejection = gate.try_acquire()
        self.assertIsNotNone(lease)
        self.assertIsNone(rejection)
        lease.release()

    def test_existing_lease_releases_original_world_after_rebind(self):
        original = threading.Lock()
        replacement = threading.Lock()
        gate = SessionTurnGate(original)
        lease, _ = gate.try_acquire()

        gate.rebind_world(replacement)
        lease.release()

        self.assertFalse(original.locked())
        self.assertFalse(replacement.locked())


class PendingReplyTests(unittest.TestCase):
    def test_correlated_reply_wakes_waiter(self):
        pending = PendingReply[str | None](None)
        result: list[str | None] = []
        worker = threading.Thread(
            target=lambda: result.append(
                pending.wait(request_id="decision-1", timeout=1)
            )
        )
        worker.start()
        self.assertTrue(self._wait_until(lambda: pending.active))

        self.assertFalse(pending.resolve("wrong", request_id="decision-2"))
        self.assertTrue(pending.resolve("dodge", request_id="decision-1"))
        worker.join(timeout=1)

        self.assertEqual(["dodge"], result)

    def test_cancel_uses_safe_default(self):
        pending = PendingReply(False)
        result: list[bool] = []
        worker = threading.Thread(target=lambda: result.append(pending.wait(timeout=1)))
        worker.start()
        self.assertTrue(self._wait_until(lambda: pending.active))

        self.assertTrue(pending.cancel())
        worker.join(timeout=1)

        self.assertEqual([False], result)

    @staticmethod
    def _wait_until(predicate) -> bool:
        for _ in range(1000):
            if predicate():
                return True
        return False


if __name__ == "__main__":
    unittest.main()
