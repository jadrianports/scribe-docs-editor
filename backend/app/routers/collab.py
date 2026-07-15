import asyncio

from fastapi import APIRouter, WebSocket
from pycrdt.websocket import YRoom

from ..collab.channel import StarletteChannel

router = APIRouter(tags=["collab"])

# Spike only: in-memory rooms, no auth, no persistence. Task 4 replaces this file.
_rooms: dict[str, YRoom] = {}
_room_tasks: dict[str, asyncio.Task] = {}
_lock = asyncio.Lock()


@router.websocket("/collab/{doc_id}")
async def collab_ws(websocket: WebSocket, doc_id: str):
    await websocket.accept()
    async with _lock:
        room = _rooms.get(doc_id)
        if room is None:
            room = _rooms[doc_id] = YRoom(ready=True)
            _room_tasks[doc_id] = asyncio.create_task(room.start())
            await room.started.wait()
    channel = StarletteChannel(websocket, doc_id)
    await room.serve(channel)
