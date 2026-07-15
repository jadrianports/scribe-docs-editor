import os

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from ..access import ROLE_RANK, effective_role
from ..collab.channel import ReadOnlyChannel, StarletteChannel
from ..collab.rooms import room_manager
from ..db import SessionLocal
from ..models import Document, User

router = APIRouter(tags=["collab"])

_ALLOWED_ORIGINS = set(
    filter(None, os.environ.get("ALLOWED_WS_ORIGINS", "http://localhost:5173,http://localhost:8000").split(","))
)


def _authorize(websocket: WebSocket, doc_id: str) -> str | None:
    """Return the effective role, or None if the connection must be closed."""
    user_id = websocket.session.get("user_id")  # SessionMiddleware populates ws scope
    if not user_id:
        return None
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        document = db.get(Document, doc_id)
        if user is None or document is None:
            return None
        return effective_role(db, user, document)  # "owner"|"editor"|"viewer"|None
    finally:
        db.close()


@router.websocket("/collab/{doc_id}")
async def collab_ws(websocket: WebSocket, doc_id: str):
    # Origin check first: WS bypasses CORS and the cookie rides along.
    origin = websocket.headers.get("origin")
    if origin is not None and origin not in _ALLOWED_ORIGINS:
        await websocket.close(code=4403)
        return

    role = _authorize(websocket, doc_id)
    if role is None:
        # No session, unknown doc, or no relationship -> close without confirming existence.
        await websocket.close(code=4404)
        return

    await websocket.accept()
    try:
        room = await room_manager.get(doc_id)
    except TimeoutError:
        # RoomManager.get() bounds room startup with anyio.fail_after() (Task 3
        # review, "Important" finding: an unbounded startup wait would stall
        # get()/release() for every doc_id, not just this one). A hung/crashed
        # startup must not leave this connection open -- close it instead of
        # letting the ASGI exception propagate. No room_manager.release() here:
        # get() never incremented the ref count for this doc_id on a failed
        # startup, so there is nothing to release.
        await websocket.close(code=1011)
        return
    try:
        is_editor = ROLE_RANK[role] >= ROLE_RANK["editor"]
        channel = (StarletteChannel if is_editor else ReadOnlyChannel)(websocket, doc_id)
        await room.serve(channel)
    except WebSocketDisconnect:
        pass
    finally:
        await room_manager.release(doc_id)
