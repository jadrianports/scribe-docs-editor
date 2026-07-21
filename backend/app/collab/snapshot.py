"""Persist a sanitized HTML snapshot of a document's live Yjs state.

Keeps `documents.content_html` -- the column Markdown/PDF export and the
plain read view all read from -- fresh across collaborative editing
sessions. The Yjs document itself (via `ScribeYStore`, in `data/yjs.db`) is
the durable source of truth for collaborative edits; `content_html` is a
derived, best-effort snapshot taken on three triggers -- a room emptying
(`RoomManager.release`), a periodic dirty-flag tick, and a graceful shutdown
flush -- so non-collaborative readers (export, the plain document view)
never see content that's stale relative to the last live editing session.

Split into a derive half (`derive_snapshot_html`) and a persist half
(`persist_snapshot_html`): the derive half must run on the event-loop thread
that owns the pycrdt `Doc` (pycrdt objects are thread-affine and crash if
touched off-thread), while the persist half is a plain SQLite write that is
safe to run anywhere -- only the finished HTML string, never the `Doc`,
crosses into a worker thread. `write_snapshot` composes the two for callers
that don't need to split them across threads themselves.
"""

from pycrdt import Doc, Map

from ..content import sanitize_html
from ..db import SessionLocal
from ..models import Document
from .html import ydoc_to_html
from .seeding import CONFIG_MAP_NAME, SEEDED_KEY


def derive_snapshot_html(ydoc: Doc) -> str | None:
    """Derive sanitized HTML from `ydoc`, or None if it should not be written.

    Mandatory invariant: the HTML returned here has always passed through
    `sanitize_html` first -- `ydoc_to_html`'s output is documented/tested as
    already stable under that sanitizer (Task 6), but this function does not
    trust that claim silently; it re-applies the same allow-list every
    document write already goes through (uploads, edits), so this is never a
    second, divergent sanitizer.

    Must run on the event-loop thread that owns `ydoc`: `ydoc_to_html` walks
    live pycrdt objects, which are thread-affine and crash if touched off the
    thread that created/mutated them.

    CRITICAL guard (final whole-branch review): also silently no-ops if
    `ydoc` was never *seeded*. A brand-new/never-collaborated document's Y.Doc
    starts with an empty "default" XmlFragment, and it STAYS empty until
    `RoomManager._create_room` calls `seed_room` (`backend/app/collab/
    seeding.py`) exactly once, server-side, before any client connects,
    flipping `ydoc.getMap(CONFIG_MAP_NAME).set(SEEDED_KEY, True)` -- a
    one-time action guarded by that same flag so N simultaneous openers
    never double-insert (`seed_room`'s own D-09 gate). The client has no
    seeding logic of its own (Phase 10 removed `EditorPage.tsx`'s seed
    effect entirely; it is a pure Y.Doc consumer now). A room can empty --
    triggering this function via `RoomManager.release()` -- before that
    guard is ever flipped: e.g. the document row was deleted out from under
    a live room (`seed_room` no-ops if the row is gone) or a seed
    conversion failure left `config.seeded` false (D-12/D-20) (both
    exercised in `backend/tests/test_collab_persistence.py` and
    `backend/tests/test_collab_seeding.py`). Without this guard,
    `ydoc_to_html(ydoc)` returns "" for such a ydoc, and it was written
    unconditionally -- silently overwriting real, previously-saved content
    with nothing the instant such a room emptied. Reads the same map name/
    key `seed_room` writes (imported from `.seeding` as `CONFIG_MAP_NAME`/
    `SEEDED_KEY`, D-23) rather than e.g. inferring "seeded" from whether
    "default" has children, so this stays in sync with whatever "seeded"
    means by construction, not by separately guessing/reimplementing that
    contract.
    """
    if not ydoc.get(CONFIG_MAP_NAME, type=Map).get(SEEDED_KEY):
        return None
    return sanitize_html(ydoc_to_html(ydoc))  # the sanitization invariant holds here


def persist_snapshot_html(doc_id: str, html: str) -> None:
    """Store `html` on `documents.content_html` for `doc_id`.

    Receives only two strings -- never `ydoc` or any other pycrdt object --
    so this half never crosses the thread-affinity boundary the derive half
    is bound by; it is safe to call from a worker thread.

    Opens its own `SessionLocal()` rather than accepting a `Session`: none of
    this function's callers (a room emptying, a periodic tick, a shutdown
    flush) have a request-scoped session to pass down -- none of them is a
    single DI-scoped HTTP request the way `Depends(get_db)` routes are.
    Silently no-ops if the document was deleted out from under a live room
    (`document is None`) rather than raising, since that is not the place to
    surface that as an error.
    """
    db = SessionLocal()
    try:
        document = db.get(Document, doc_id)
        if document is not None:
            document.content_html = html
            db.commit()  # onupdate bumps updated_at
    finally:
        db.close()


def write_snapshot(doc_id: str, ydoc: Doc) -> None:
    """Derive sanitized HTML from `ydoc` and store it on `documents.content_html`.

    A thin synchronous composition of `derive_snapshot_html` and
    `persist_snapshot_html`, kept for callers that run entirely on the
    event-loop thread and don't need to split the derive/persist halves
    across a thread boundary themselves.
    """
    html = derive_snapshot_html(ydoc)
    if html is not None:
        persist_snapshot_html(doc_id, html)
