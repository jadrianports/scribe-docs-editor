from anyio import Lock
from pycrdt import Channel, YMessageType, YSyncMessageType


class StarletteChannel(Channel):
    """Adapts a Starlette/FastAPI WebSocket to the pycrdt Channel interface."""

    def __init__(self, websocket, path: str):
        self._websocket = websocket
        self._path = path
        self._send_lock = Lock()

    @property
    def path(self) -> str:
        return self._path

    async def send(self, message: bytes) -> None:
        async with self._send_lock:
            await self._websocket.send_bytes(message)

    async def recv(self) -> bytes:
        return bytes(await self._websocket.receive_bytes())

    async def __anext__(self) -> bytes:
        try:
            return await self.recv()
        except Exception:
            raise StopAsyncIteration()


class ReadOnlyChannel(StarletteChannel):
    """A channel that drops client->server document mutations (viewer role).

    Sync STEP1 (a state *request*) and awareness (cursors) pass through, so the
    viewer still receives the document and sees peers; STEP2/UPDATE (the client
    pushing doc changes) are silently dropped, so a viewer can never mutate.
    """

    async def __anext__(self) -> bytes:
        while True:
            try:
                msg = await self.recv()
            except Exception:
                raise StopAsyncIteration()
            if len(msg) < 2:
                return msg  # too short to carry a type+subtype -- pass through unfiltered, safe
            if (
                msg[0] == YMessageType.SYNC
                and msg[1] in (YSyncMessageType.SYNC_STEP2, YSyncMessageType.SYNC_UPDATE)
            ):
                continue
            return msg
