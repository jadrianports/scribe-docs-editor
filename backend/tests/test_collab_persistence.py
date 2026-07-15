import anyio
from pycrdt import Doc, Text, XmlElement, XmlFragment, XmlText
from app.collab.rooms import RoomManager
from app.models import Document


def test_updates_persist_across_room_restart(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # yjs.db is created under ./data
    async def scenario():
        mgr = RoomManager()
        room = await mgr.get("doc-1")
        room.ydoc.get("t", type=Text).insert(0, "hello")
        await anyio.sleep(0.2)          # let the room persist the update
        await mgr.release("doc-1")      # stops the room
        room2 = await mgr.get("doc-1")  # fresh room, must rehydrate
        assert str(room2.ydoc.get("t", type=Text)) == "hello"
    anyio.run(scenario)


def test_snapshot_written_when_room_empties(tmp_path, monkeypatch, db_session, seed_users):
    """Task 7: when the last client releases a room (the room empties),
    RoomManager.release() must derive a sanitized HTML snapshot from the
    room's ydoc and persist it to documents.content_html -- so Markdown/PDF
    export stop showing stale content once collaborative editing sessions
    end. `db_session`/`seed_users` come from conftest.py, which also (per
    this task) redirects `app.collab.snapshot.SessionLocal` -- the name
    `write_snapshot` actually calls -- to this same isolated test engine,
    the same bypass-the-test-DB trap Task 4 hit with `collab_router.SessionLocal`.
    """
    monkeypatch.chdir(tmp_path)  # yjs.db (and, pre-fix, a stray data/ dir) under ./data
    doc = Document(id="snap-1", title="S", content_html="", owner_id=seed_users["alice"].id)
    db_session.add(doc)
    db_session.commit()

    async def scenario():
        mgr = RoomManager()
        room = await mgr.get("snap-1")
        frag = room.ydoc.get("default", type=XmlFragment)
        para = XmlElement("paragraph")
        frag.children.append(para)
        para.children.append(XmlText("edited"))
        await mgr.release("snap-1")  # last (only) client -> room empties -> snapshot written

    anyio.run(scenario)

    db_session.expire_all()  # the snapshot was written by a *different* Session
    saved = db_session.get(Document, "snap-1")
    assert saved.content_html == "<p>edited</p>"


def test_write_snapshot_sanitizes_before_storing(tmp_path, monkeypatch, db_session, seed_users):
    """The sanitization invariant is mandatory (task description): write_snapshot
    must run ydoc_to_html's output through app.content.sanitize_html before it
    touches content_html, not just trust the output is already safe. ydoc_to_html
    is already proven sanitizer-stable for every real node shape it can produce
    (Task 6's test suite), so demonstrating write_snapshot itself sanitizes --
    rather than merely happening to receive safe input from its one real caller
    -- means monkeypatching it to return something a real Yjs doc could never
    produce, and confirming write_snapshot strips it anyway.
    """
    from app.collab import snapshot as collab_snapshot

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        collab_snapshot, "ydoc_to_html", lambda ydoc: "<p>ok</p><script>alert(1)</script>"
    )
    doc = Document(id="snap-2", title="S", content_html="", owner_id=seed_users["alice"].id)
    db_session.add(doc)
    db_session.commit()

    collab_snapshot.write_snapshot("snap-2", Doc())

    db_session.expire_all()
    saved = db_session.get(Document, "snap-2")
    # bleach.clean(strip=True) removes a disallowed *tag* but keeps its inner
    # text (confirmed empirically -- an initial version of this test wrongly
    # expected the text gone too, and failed with the assertion showing the
    # real output): "<script>alert(1)</script>" -> "alert(1)", tag gone, text
    # kept as plain content. The invariant this test actually protects is "no
    # executable <script> tag survives into content_html," not "all trace of
    # the disallowed element's text is gone" -- same behavior sanitize_html
    # already has everywhere else it's used (uploads, markdown import).
    assert saved.content_html == "<p>ok</p>alert(1)"
    assert "<script>" not in saved.content_html


def test_write_snapshot_noops_for_unknown_doc_id(tmp_path, monkeypatch, db_session):
    """A document can be deleted out from under a live collaborative room;
    release() still runs unconditionally, with nothing left to write to.
    write_snapshot must not raise -- otherwise release() would never reach
    `await room.stop()`, leaking a running room. `db_session` isn't touched
    directly; it's here only for its side effect (conftest.py redirects
    app.collab.snapshot.SessionLocal to the isolated test engine).
    """
    from app.collab import snapshot as collab_snapshot

    monkeypatch.chdir(tmp_path)
    collab_snapshot.write_snapshot("does-not-exist", Doc())  # must not raise
