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

from pycrdt import Doc, Map

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

    CRITICAL guard (final whole-branch review): also silently no-ops if
    `ydoc` was never *seeded*. A brand-new/never-collaborated document's Y.Doc
    starts with an empty "default" XmlFragment, and it STAYS empty until the
    first TipTap client's seed effect (`frontend/src/pages/EditorPage.tsx`)
    inserts the document's last-saved `content_html` into it -- a one-time
    action guarded by a flag in a shared `config` map so N simultaneous
    openers never double-insert:
    `ydoc.getMap('config').set('seeded', true)` (JS/Yjs side). A room can
    empty -- triggering this function via `RoomManager.release()` -- before
    that guard is ever flipped: e.g. a viewer opens a never-collaborated
    document and leaves before any editor connects, or a client only ever
    touches an unrelated root key (both exercised in
    `backend/tests/test_collab_persistence.py` and
    `backend/tests/test_collab_access.py`). Without this guard,
    `ydoc_to_html(ydoc)` returns "" for such a ydoc, and it was written
    unconditionally -- silently overwriting real, previously-saved content
    with nothing the instant such a room emptied. Mirrors the client's own
    flag (same map name "config", same key "seeded") rather than e.g.
    inferring "seeded" from whether "default" has children, so this stays in
    sync with whatever "seeded" means client-side by construction, not by
    separately guessing/reimplementing that contract server-side.
    """
    if not ydoc.get("config", type=Map).get("seeded"):
        return
    html = sanitize_html(ydoc_to_html(ydoc))  # the sanitization invariant holds here
    db = SessionLocal()
    try:
        document = db.get(Document, doc_id)
        if document is not None:
            document.content_html = html
            db.commit()  # onupdate bumps updated_at
    finally:
        db.close()
