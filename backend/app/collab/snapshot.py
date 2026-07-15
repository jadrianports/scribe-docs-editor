"""Persist a sanitized HTML snapshot of a document's live Yjs state.

Keeps `documents.content_html` -- the column Markdown/PDF export and the
plain read view all read from -- fresh across collaborative editing
sessions. The Yjs document itself (via `ScribeYStore`, in `data/yjs.db`) is
the durable source of truth for collaborative edits; `content_html` is a
derived, best-effort snapshot taken when a document's last editor leaves
(see `RoomManager.release`), so non-collaborative readers (export, the
plain document view) never see content that's stale relative to the last
live editing session.
"""

from pycrdt import Doc

from ..content import sanitize_html
from ..db import SessionLocal
from ..models import Document
from .html import ydoc_to_html


def write_snapshot(doc_id: str, ydoc: Doc) -> None:
    """Derive sanitized HTML from `ydoc` and store it on `documents.content_html`.

    Mandatory invariant: the HTML written here has always passed through
    `sanitize_html` first -- `ydoc_to_html`'s output is documented/tested as
    already stable under that sanitizer (Task 6), but this function does not
    trust that claim silently; it re-applies the same allow-list every
    document write already goes through (uploads, edits), so this is never a
    second, divergent sanitizer.

    Opens its own `SessionLocal()` rather than accepting a `Session`:
    `RoomManager.release()` (the only caller) has no request-scoped session
    to pass down -- a WebSocket disconnect isn't a single DI-scoped HTTP
    request the way `Depends(get_db)` routes are. Silently no-ops if the
    document was deleted out from under a live room (`document is None`)
    rather than raising, since a room emptying is not the place to surface
    that as an error.
    """
    html = sanitize_html(ydoc_to_html(ydoc))  # the sanitization invariant holds here
    db = SessionLocal()
    try:
        document = db.get(Document, doc_id)
        if document is not None:
            document.content_html = html
            db.commit()  # onupdate bumps updated_at
    finally:
        db.close()
