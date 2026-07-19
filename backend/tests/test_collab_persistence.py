import gc

import anyio
from pycrdt import Doc, Map, Text, XmlElement, XmlFragment, XmlText
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

    Seeds `config`/`seeded` before editing -- required since the final
    whole-branch review's CRITICAL fix: `write_snapshot` now skips entirely
    for an un-seeded ydoc (see snapshot.py's guard and
    `test_release_does_not_overwrite_content_when_room_never_seeded` /
    `test_release_writes_snapshot_when_room_was_seeded` below, which cover
    that guard directly). This test's own job is the derivation+persist
    behavior for the ordinary seeded case, unchanged since Task 7.
    """
    monkeypatch.chdir(tmp_path)  # yjs.db (and, pre-fix, a stray data/ dir) under ./data
    doc = Document(id="snap-1", title="S", content_html="", owner_id=seed_users["alice"].id)
    db_session.add(doc)
    db_session.commit()

    async def scenario():
        mgr = RoomManager()
        room = await mgr.get("snap-1")
        room.ydoc.get("config", type=Map)["seeded"] = True  # mirror EditorPage.tsx's seed effect
        frag = room.ydoc.get("default", type=XmlFragment)
        para = XmlElement("paragraph")
        frag.children.append(para)
        para.children.append(XmlText("edited"))
        await mgr.release("snap-1")  # last (only) client -> room empties -> snapshot written

    anyio.run(scenario)

    db_session.expire_all()  # the snapshot was written by a *different* Session
    saved = db_session.get(Document, "snap-1")
    assert saved.content_html == "<p>edited</p>"


def test_release_does_not_overwrite_content_when_room_never_seeded(
    tmp_path, monkeypatch, db_session, seed_users
):
    """CRITICAL (final whole-branch review): `write_snapshot` must not derive
    HTML from a Y.Doc whose "default" fragment is empty only because no
    TipTap client ever seeded it -- as opposed to a genuinely-edited-to-empty
    document. `EditorPage.tsx`'s seed effect inserts the document's
    last-saved `content_html` into the shared "default" XmlFragment exactly
    once, guarded by `ydoc.getMap('config').get('seeded')`; a room can empty
    (last client disconnects -> RoomManager.release() -> write_snapshot)
    before that guard is ever flipped -- e.g. a viewer opens a
    never-collaborated document and leaves before any editor connects, or
    (Task 9) a raw-pycrdt test client connects/disconnects while only ever
    touching an unrelated scratch root key, exactly like this test does.
    Before the fix, `ydoc_to_html` on that empty ydoc returned "", and
    `write_snapshot` wrote it unconditionally -- silently wiping real,
    previously-saved content the instant such a room emptied.
    """
    monkeypatch.chdir(tmp_path)
    doc = Document(
        id="unseeded-1",
        title="D",
        content_html="<p>real saved content</p>",
        owner_id=seed_users["alice"].id,
    )
    db_session.add(doc)
    db_session.commit()

    async def scenario():
        mgr = RoomManager()
        room = await mgr.get("unseeded-1")
        # Touches only a scratch root -- "default"/"config" are never
        # touched, so this room is never seeded, mirroring exactly how
        # test_collab_access.py's live_server-based concurrency tests use a
        # scratch root key against the real "Project Roadmap" document.
        room.ydoc.get("scratch", type=Text).insert(0, "not the document body")
        await mgr.release("unseeded-1")  # room empties -> snapshot considered, must be skipped

    anyio.run(scenario)

    db_session.expire_all()
    saved = db_session.get(Document, "unseeded-1")
    assert saved.content_html == "<p>real saved content</p>"  # UNCHANGED


def test_release_writes_snapshot_when_room_was_seeded(tmp_path, monkeypatch, db_session, seed_users):
    """The seeded counterpart to the test above: once a client has actually
    seeded the room (flips the same `config`/`seeded` flag EditorPage.tsx's
    seed effect sets), release() DOES derive and persist the live HTML --
    proving the guard only skips the specific "never seeded" case, not
    every snapshot write.
    """
    monkeypatch.chdir(tmp_path)
    doc = Document(
        id="seeded-1", title="D", content_html="<p>stale</p>", owner_id=seed_users["alice"].id
    )
    db_session.add(doc)
    db_session.commit()

    async def scenario():
        mgr = RoomManager()
        room = await mgr.get("seeded-1")
        room.ydoc.get("config", type=Map)["seeded"] = True  # mirror EditorPage.tsx's seed effect
        frag = room.ydoc.get("default", type=XmlFragment)
        para = XmlElement("paragraph")
        frag.children.append(para)
        para.children.append(XmlText("live edit"))
        await mgr.release("seeded-1")

    anyio.run(scenario)

    db_session.expire_all()
    saved = db_session.get(Document, "seeded-1")
    assert saved.content_html == "<p>live edit</p>"


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

    ydoc = Doc()
    ydoc.get("config", type=Map)["seeded"] = True  # must be seeded, or write_snapshot's
    # guard (see snapshot.py) skips before ever reaching the monkeypatched ydoc_to_html below.
    collab_snapshot.write_snapshot("snap-2", ydoc)

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

    Seeds the ydoc so this exercises the "document not found" branch
    specifically -- an un-seeded ydoc would already no-op via the separate
    seeded-guard (see snapshot.py / the un-seeded tests above) for a
    different reason, which would make this test pass without ever
    reaching the code path it's meant to cover.
    """
    from app.collab import snapshot as collab_snapshot

    monkeypatch.chdir(tmp_path)
    ydoc = Doc()
    ydoc.get("config", type=Map)["seeded"] = True
    collab_snapshot.write_snapshot("does-not-exist", ydoc)  # must not raise


def test_tick_refreshes_content_html_for_export_mid_session(
    tmp_path, monkeypatch, db_session, seed_users
):
    """Plan 06-04 (D-26): proves criteria 1+2 in-process -- a seeded+mutated room, given a
    tiny SCRIBE_SNAPSHOT_INTERVAL, has content_html reflecting the edit after one tick, with
    NO release() call before the assertion runs. The column read here (`documents.content_html`)
    is exactly what Markdown/PDF export and the plain document view already query (D-07,
    unchanged read path) -- so this is also the criterion-2 proof: an editor never has to
    disconnect for export to see their latest edit.
    """
    monkeypatch.setenv("SCRIBE_SNAPSHOT_INTERVAL", "0.05")
    monkeypatch.chdir(tmp_path)
    doc = Document(id="tick-1", title="T", content_html="", owner_id=seed_users["alice"].id)
    db_session.add(doc)
    db_session.commit()

    async def scenario():
        mgr = RoomManager()
        room = await mgr.get("tick-1")
        room.ydoc.get("config", type=Map)["seeded"] = True  # mirror EditorPage.tsx's seed effect
        frag = room.ydoc.get("default", type=XmlFragment)
        para = XmlElement("paragraph")
        frag.children.append(para)
        para.children.append(XmlText("mid-session edit"))

        await anyio.sleep(0.3)  # past one 0.05s tick interval -- no release() call before this

        db_session.expire_all()  # the tick wrote via a different Session
        saved = db_session.get(Document, "tick-1")
        assert saved.content_html == "<p>mid-session edit</p>"

        await mgr.release("tick-1")

    anyio.run(scenario)
    gc.collect()
