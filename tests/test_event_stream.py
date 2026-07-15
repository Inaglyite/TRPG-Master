import asyncio
import unittest

from src.event_stream import OrderedTurnEventStream, TurnAlreadyActiveError


class FakeWebSocket:
    def __init__(self):
        self.messages: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        await asyncio.sleep(0)
        self.messages.append(payload)


class OrderedTurnEventStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_a_second_active_turn_without_retagging_first(self):
        websocket = FakeWebSocket()
        stream = OrderedTurnEventStream(websocket, asyncio.get_running_loop())

        turn_id = await stream.begin_turn()
        with self.assertRaises(TurnAlreadyActiveError):
            await stream.begin_turn()
        stream.emit({"type": "narrative_chunk", "text": "第一轮仍在继续。"})
        stream.end_turn()
        await stream.flush()
        await stream.close()

        self.assertEqual(len(websocket.messages), 3)
        self.assertTrue(all(message["turn_id"] == turn_id for message in websocket.messages))
        self.assertEqual([message["seq"] for message in websocket.messages], [1, 2, 3])

    async def test_serializes_turn_callbacks_before_done_and_session_state(self):
        websocket = FakeWebSocket()
        stream = OrderedTurnEventStream(websocket, asyncio.get_running_loop())

        turn_id = await stream.begin_turn()

        def worker_callbacks() -> None:
            stream.emit({"type": "narrative_chunk", "text": "你抵达停尸房。"})
            stream.emit({"type": "handout", "asset_id": "doctor"})
            stream.end_turn()

        await asyncio.to_thread(worker_callbacks)
        await stream.send({"type": "state_data"})
        await stream.flush()
        await stream.close()

        self.assertEqual(
            [message["type"] for message in websocket.messages],
            [
                "gm_turn_start",
                "narrative_chunk",
                "handout",
                "done",
                "state_data",
            ],
        )
        turn_messages = websocket.messages[:4]
        self.assertEqual(
            [message["seq"] for message in turn_messages],
            [1, 2, 3, 4],
        )
        self.assertTrue(all(
            message["turn_id"] == turn_id for message in turn_messages
        ))
        self.assertNotIn("turn_id", websocket.messages[-1])

    async def test_uses_persistent_turn_id_when_supplied(self):
        websocket = FakeWebSocket()
        stream = OrderedTurnEventStream(websocket, asyncio.get_running_loop())
        turn_id = "turn_20260715T120000000000Z_1234abcd"

        returned = await stream.begin_turn(turn_id)
        stream.end_turn()
        await stream.flush()
        await stream.close()

        self.assertEqual(turn_id, returned)
        self.assertEqual(turn_id, websocket.messages[0]["turn_id"])
        self.assertEqual(turn_id, websocket.messages[1]["turn_id"])


if __name__ == "__main__":
    unittest.main()
