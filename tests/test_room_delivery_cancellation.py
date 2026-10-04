"""A departing caller must not poison another member's ordered send queue."""

import asyncio

import pytest

from src.multiplayer.room_runtime import RoomConnection, RoomEventHub


def test_cancelled_broadcast_does_not_cancel_other_members_delivery_chain():
    async def scenario():
        class Socket:
            def __init__(self):
                self.started = asyncio.Event()
                self.release = asyncio.Event()
                self.messages = []

            async def send_json(self, payload):
                if payload.get("type") == "first":
                    self.started.set()
                    await self.release.wait()
                self.messages.append(payload)

        hub = RoomEventHub("cancellation-test")
        owner = Socket()
        player = Socket()
        await hub.attach(RoomConnection("owner", "owner", "owner", owner))
        await hub.attach(RoomConnection("player", "player", "player", player))
        broadcast = asyncio.create_task(hub.broadcast({"type": "first"}))
        await asyncio.wait_for(owner.started.wait(), 1)
        await asyncio.wait_for(player.started.wait(), 1)
        broadcast.cancel()
        try:
            await broadcast
        except asyncio.CancelledError:
            pass
        owner.release.set()
        player.release.set()
        delivered = await asyncio.wait_for(hub.send_direct("owner", {"type": "next"}), 1)
        assert delivered, "caller cancellation poisoned the still-connected owner's queue"
        assert [message["type"] for message in owner.messages] == ["first", "next"]
        assert await hub.send_direct("player", {"type": "next"})
        assert [message["type"] for message in player.messages] == ["first", "next"]

    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["direct", "batch", "snapshot"])
def test_cancelled_reply_caller_keeps_committed_queue_usable(path):
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()
        messages = []

        class Socket:
            async def send_json(self, payload):
                if payload["type"] == "first":
                    started.set()
                    await release.wait()
                messages.append(payload["type"])

        hub = RoomEventHub("reply-cancellation")
        await hub.attach(RoomConnection("member", "member", "player", Socket()))
        if path == "direct":
            operation = hub.send_direct("member", {"type": "first"})
        elif path == "batch":
            operation = hub.send_batch("member", lambda *_: [{"type": "first"}])
        else:
            operation = hub.send_snapshot_with_replay(
                "member", lambda *_: [{"type": "first", "latest_event_id": 0}]
            )
        caller = asyncio.create_task(operation)
        await asyncio.wait_for(started.wait(), 1)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        release.set()
        assert await asyncio.wait_for(hub.send_direct("member", {"type": "next"}), 1)
        assert messages == ["first", "next"]

    asyncio.run(scenario())


def test_preserved_delivery_queue_still_checks_revocation_before_next_send():
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()
        messages = []
        authorized = True

        class Socket:
            async def send_json(self, payload):
                if payload["type"] == "first":
                    started.set()
                    await release.wait()
                messages.append(payload["type"])

        hub = RoomEventHub("reply-revocation")
        await hub.attach(
            RoomConnection(
                "member",
                "member",
                "player",
                Socket(),
                authorization_check=lambda: authorized,
            )
        )
        caller = asyncio.create_task(hub.send_direct("member", {"type": "first"}))
        await asyncio.wait_for(started.wait(), 1)
        first_tail = hub._connections["member"].send_tail
        queued = asyncio.create_task(hub.send_direct("member", {"type": "secret"}))

        # Synchronize on actual queue insertion rather than an arbitrary sleep.
        async def wait_until_queued():
            while hub._connections["member"].send_tail is first_tail:
                await asyncio.sleep(0)

        await asyncio.wait_for(wait_until_queued(), 1)
        authorized = False
        release.set()
        assert await caller
        assert not await queued
        assert messages == ["first"]

    asyncio.run(scenario())
