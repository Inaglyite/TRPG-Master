import unittest

from src.ws_router import DuplicateMessageHandlerError, WsMessageRouter


class WsMessageRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatches_registered_handler(self):
        router = WsMessageRouter()
        received = []

        @router.handler("ping")
        async def ping(payload):
            received.append(payload)

        result = await router.dispatch({"type": "ping", "nonce": 7})

        self.assertTrue(result.handled)
        self.assertEqual("ping", result.message_type)
        self.assertEqual([{"type": "ping", "nonce": 7}], received)

    async def test_unknown_and_invalid_messages_are_not_handled(self):
        router = WsMessageRouter()

        unknown = await router.dispatch({"type": "future_message"})
        invalid = await router.dispatch({"type": 42})

        self.assertFalse(unknown.handled)
        self.assertEqual("future_message", unknown.message_type)
        self.assertFalse(invalid.handled)
        self.assertEqual("", invalid.message_type)

    def test_duplicate_registration_is_rejected(self):
        router = WsMessageRouter()

        async def handler(_payload):
            return None

        router.add("action", handler)
        with self.assertRaises(DuplicateMessageHandlerError):
            router.add("action", handler)


if __name__ == "__main__":
    unittest.main()
