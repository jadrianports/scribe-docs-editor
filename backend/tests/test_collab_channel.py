import anyio
from pycrdt import YMessageType, YSyncMessageType

from app.collab.channel import ReadOnlyChannel

TOO_SHORT_EMPTY = b""
TOO_SHORT_TYPE_ONLY = bytes([YMessageType.SYNC])
WRONG_LEADING_BYTE = bytes([99, 1, 0])
SYNC_STEP1_PASSTHROUGH = bytes([YMessageType.SYNC, YSyncMessageType.SYNC_STEP1, 0])
SYNC_UPDATE_MUST_BE_DROPPED = bytes([YMessageType.SYNC, YSyncMessageType.SYNC_UPDATE, 0])
SYNC_STEP2_MUST_BE_DROPPED = bytes([YMessageType.SYNC, YSyncMessageType.SYNC_STEP2, 0])


class _FakeWebSocket:
    def __init__(self, frames: list[bytes]):
        self._frames = iter(frames)

    async def receive_bytes(self) -> bytes:
        return next(self._frames)  # StopIteration -> caught by StarletteChannel.__anext__'s
                                    # `except Exception: raise StopAsyncIteration()`


def test_short_and_wrong_type_messages_pass_through_without_crashing():
    async def scenario():
        ws = _FakeWebSocket([TOO_SHORT_EMPTY, TOO_SHORT_TYPE_ONLY, WRONG_LEADING_BYTE])
        channel = ReadOnlyChannel(ws, "doc-1")
        received = [msg async for msg in channel]
        assert received == [TOO_SHORT_EMPTY, TOO_SHORT_TYPE_ONLY, WRONG_LEADING_BYTE]

    anyio.run(scenario)


def test_sync_update_and_sync_step2_are_dropped_step1_passes():
    async def scenario():
        ws = _FakeWebSocket(
            [SYNC_STEP1_PASSTHROUGH, SYNC_UPDATE_MUST_BE_DROPPED, SYNC_STEP2_MUST_BE_DROPPED]
        )
        channel = ReadOnlyChannel(ws, "doc-1")
        received = [msg async for msg in channel]
        assert received == [SYNC_STEP1_PASSTHROUGH]

    anyio.run(scenario)
