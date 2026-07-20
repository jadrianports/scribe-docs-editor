import os
import uuid
from urllib.parse import urlparse

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


def _origin_allowed(origin: str | None, host: str | None) -> bool:
    """True if a WS handshake presenting this `Origin` (browser-sent) against
    this request's own `Host` header should be allowed to proceed.

    - No `Origin` header at all: allowed, unchanged from before this fix. Only
      browsers send `Origin` on a WebSocket handshake, so a missing header
      means a non-browser client (a script, a native app, or this project's
      own subprocess-driven tests) -- there's nothing to compare it against.
    - `Origin` is in the explicit `ALLOWED_WS_ORIGINS` allow-list (env var,
      defaults to the local dev origins): allowed. This is what keeps a
      split-origin setup working, e.g. the Vite dev server on :5173 talking to
      the API on :8000, or a separately-hosted frontend in production.
    - Otherwise: allowed only if the Origin's `host[:port]` equals our own
      `Host` header, i.e. the request is same-origin. The single-service
      deploy (SPA and API served from one origin, e.g.
      `https://scribe-docs-editor.onrender.com`) is *always* same-origin, so
      this makes collaboration work there with zero configuration. A
      cross-site attacker's page has an `Origin` equal to *their* site, which
      never equals our `Host`, so cross-site WS hijacking is still blocked --
      this doesn't weaken the check, it just recognizes a case (same-origin)
      that was always safe but previously only allowed via the hardcoded
      localhost defaults.
    """
    if origin is None:
        return True
    if origin in _ALLOWED_ORIGINS:
        return True
    return urlparse(origin).netloc == host


def _is_valid_doc_id(doc_id: str) -> bool:
    """True if `doc_id` parses as a UUID (hyphenated or dashless 32-hex form).

    `Document.id` is always `str(uuid.uuid4())` (app/models.py); this rejects
    anything else -- empty string, None, path-like or SQL-ish garbage -- before
    it can be used as a DB lookup key or, further down `collab_ws`, as
    `ScribeYStore(path=doc_id)`'s room name / store path.
    """
    try:
        uuid.UUID(doc_id)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


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
    if not _origin_allowed(origin, websocket.headers.get("host")):
        await websocket.close(code=4403)
        return

    if not _is_valid_doc_id(doc_id):
        # Same 4404 _authorize's "unknown doc / no relationship" branch uses --
        # a malformed doc_id must never be distinguishable from an inaccessible
        # real one (404-not-403 non-leak rule, DEC-access-central). Rejected
        # here, before _authorize's DB lookup or room_manager.get() ever see it.
        await websocket.close(code=4404)
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
