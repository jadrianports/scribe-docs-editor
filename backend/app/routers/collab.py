from fastapi import APIRouter, WebSocket
from pycrdt.websocket import YRoom

from ..collab.channel import StarletteChannel

router = APIRouter(tags=["collab"])

# Spike only: a single in-memory room map, no auth, no persistence.
_rooms: dict[str, YRoom] = {}


@router.websocket("/collab/{doc_id}")
async def collab_ws(websocket: WebSocket, doc_id: str):
    await websocket.accept()
    room = _rooms.get(doc_id)
    if room is None:
        room = _rooms[doc_id] = YRoom(ready=True)
    async with room:
        channel = StarletteChannel(websocket, doc_id)
        await room.serve(channel)
