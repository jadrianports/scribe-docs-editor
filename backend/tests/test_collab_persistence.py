import gc
import time

import anyio
from pycrdt import Doc, Map, Text, XmlElement, XmlFragment, XmlText
from app.collab.channel import StarletteChannel
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


def test_yjs_update_survives_a_failing_client_send(tmp_path, monkeypatch):
    """D-15/D-16: `pycrdt/websocket/yroom.py`'s `_broadcast_updates` schedules
    `start_soon(client.send, message)` (yroom.py:213) and
    `start_soon(self.ystore.write, update)` (yroom.py:218) into the SAME shared
    `self._task_group`, in the same loop iteration, for the same update -- confirmed by
    reading the installed library. A raising `client.send` could therefore cancel the
    in-flight durable `ystore.write` for that same update, meaning a durable-store loss
    would not be bounded by `SCRIBE_SNAPSHOT_INTERVAL` at all. A real lost write was never
    reproduced before this test existed -- `start_soon` only schedules, so the outcome is
    timing-dependent. Plan 11-01's guard on `StarletteChannel.send` (broad `except
    Exception`, latched `_closed`) means the send this test drives never raises, which
    removes the only reachable trigger; this test settles the question empirically with
    that guard in place and pins the post-guard behaviour so a future refactor cannot
    quietly reopen it.
    """
    monkeypatch.chdir(tmp_path)  # yjs.db is created under ./data

    class _FailingWebSocket:
        """Stands in for a transport that is already gone -- `send_bytes` always raises,
        simulating a send-after-close race at the exact moment `_broadcast_updates` fans
        an update out to `self.clients`. `calls` proves the fan-out actually reached this
        channel; a stub that's never invoked would make the test pass vacuously."""

        def __init__(self):
            self.calls = 0

        async def send_bytes(self, message):
            self.calls += 1
            raise RuntimeError("simulated send-after-close")

    async def scenario():
        mgr = RoomManager()
        room = await mgr.get("durable-1")

        # Register a failing client the way YRoom.serve does (self.clients.add(channel)),
        # without opening a real socket. Constructed here, inside scenario(), so the
        # channel's anyio.Lock() is created with a running event loop.
        failing_socket = _FailingWebSocket()
        channel = StarletteChannel(failing_socket, "durable-1")
        room.clients.add(channel)

        # Drives _broadcast_updates, which in one loop iteration schedules the failing
        # client.send AND self.ystore.write into the room's single shared task group.
        room.ydoc.get("t", type=Text).insert(0, "survives")

        await anyio.sleep(0.3)  # let both scheduled tasks run

        # The failure actually happened (fan-out reached the failing channel)...
        assert failing_socket.calls >= 1
        # ...and was contained: the room did not crash. This is what discriminates "the
        # update survived because the guard worked" from "the room died and something
        # else happened to save it".
        assert mgr._rooms_by_id["durable-1"].crashed is False

        await mgr.release("durable-1")      # stops the room
        room2 = await mgr.get("durable-1")  # fresh room -- can only rehydrate from yjs.db
        assert str(room2.ydoc.get("t", type=Text)) == "survives"

        await mgr.release("durable-1")

    anyio.run(scenario)
    gc.collect()


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
    """The seeded counterpart to the test above: once a room is seeded (now
    done server-side by `_create_room` itself -- plan 10-05, D-08 -- rather
    than by a client's seed effect), release() DOES derive and persist the
    live HTML, proving the guard only skips the specific "never seeded" case,
    not every snapshot write.

    Since `mgr.get("seeded-1")` now seeds the room from `content_html`
    ("<p>stale</p>") before this scenario's own code ever runs, the manual
    `config["seeded"] = True` line below is a no-op (already true) rather
    than the thing that flips seeded-ness -- and the live-edit paragraph is
    appended AFTER the already-seeded stale content, not in isolation, so the
    persisted snapshot reflects both.
    """
    monkeypatch.chdir(tmp_path)
    doc = Document(
        id="seeded-1", title="D", content_html="<p>stale</p>", owner_id=seed_users["alice"].id
    )
    db_session.add(doc)
    db_session.commit()

    async def scenario():
        mgr = RoomManager()
        room = await mgr.get("seeded-1")  # already seeded with "<p>stale</p>" by _create_room
        room.ydoc.get("config", type=Map)["seeded"] = True  # already True -- no-op
        frag = room.ydoc.get("default", type=XmlFragment)
        para = XmlElement("paragraph")
        frag.children.append(para)
        para.children.append(XmlText("live edit"))
        await mgr.release("seeded-1")

    anyio.run(scenario)

    db_session.expire_all()
    saved = db_session.get(Document, "seeded-1")
    assert saved.content_html == "<p>stale</p><p>live edit</p>"


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


def test_release_persist_timeout_does_not_wedge_teardown(
    tmp_path, monkeypatch, db_session, seed_users
):
    """Plan 06-04 (D-25, criterion 3 + D-24's in-scope DB-lock recovery slice): the persist
    half is monkeypatched to stall via a blocking `time.sleep` -- persist runs under
    `asyncio.to_thread`, so a real blocking call (like a locked-DB write) is what actually
    exercises the bound, not a plain `asyncio.sleep` -- well past RELEASE_SNAPSHOT_TIMEOUT
    (~2s). This proves the `fail_after` timeout CONTRACT itself, not real SQLite lock timing
    (RESEARCH Pitfall 3): `release()` must still return, and `room.stop()` must still run,
    rather than the whole teardown wedging on a stalled write. The other two
    REQ-snapshot-recovery-tests modes (network disconnect during autosave; WS reconnect with
    pending title change) are frontend paths handed to Phase 9's criterion 4, not this phase
    (D-24).
    """
    from app.collab import snapshot as collab_snapshot

    def stalling_persist(doc_id, html):
        time.sleep(5)  # exceeds RELEASE_SNAPSHOT_TIMEOUT (~2s)

    monkeypatch.setattr(collab_snapshot, "persist_snapshot_html", stalling_persist)
    monkeypatch.chdir(tmp_path)
    doc = Document(id="stall-1", title="S", content_html="", owner_id=seed_users["alice"].id)
    db_session.add(doc)
    db_session.commit()

    async def scenario():
        mgr = RoomManager()
        room = await mgr.get("stall-1")
        room.ydoc.get("config", type=Map)["seeded"] = True
        frag = room.ydoc.get("default", type=XmlFragment)
        para = XmlElement("paragraph")
        frag.children.append(para)
        para.children.append(XmlText("edited"))

        # Outer safety net for the test itself -- not the ~2s contract under test, which is
        # enforced inside release() by RELEASE_SNAPSHOT_TIMEOUT.
        with anyio.fail_after(10):
            evicted = await mgr.release("stall-1")

        assert evicted is True
        assert room._task_group is None  # room.stop() ran despite the stalled persist

    anyio.run(scenario)
    gc.collect()


def test_shutdown_flush_writes_only_dirty_rooms(tmp_path, monkeypatch, db_session, seed_users):
    """Plan 06-04 (D-29): builds two rooms via mgr.get() without releasing -- one
    seeded+edited (dirty), one seeded but with its dirty flag reset back to False
    afterward (simulating a room whose snapshot was already taken, i.e. genuinely clean,
    not merely "never touched") -- then calls `await mgr.shutdown_flush()` directly and
    asserts the dirty room's content_html updated while the clean room's updated_at did not
    move. Proves the flush loop itself respects `record.dirty` (D-12), independent of the
    separate un-seeded guard already covered by test_clean_room_ticker_writes_nothing.
    """
    monkeypatch.chdir(tmp_path)
    dirty_doc = Document(
        id="flush-dirty", title="D", content_html="", owner_id=seed_users["alice"].id
    )
    clean_doc = Document(
        id="flush-clean", title="C", content_html="<p>stays</p>", owner_id=seed_users["alice"].id
    )
    db_session.add_all([dirty_doc, clean_doc])
    db_session.commit()
    clean_updated_at = clean_doc.updated_at

    async def scenario():
        mgr = RoomManager()

        dirty_room = await mgr.get("flush-dirty")
        dirty_room.ydoc.get("config", type=Map)["seeded"] = True
        frag = dirty_room.ydoc.get("default", type=XmlFragment)
        para = XmlElement("paragraph")
        frag.children.append(para)
        para.children.append(XmlText("dirty edit"))

        clean_room = await mgr.get("flush-clean")
        clean_room.ydoc.get("config", type=Map)["seeded"] = True
        # The seed assignment above flips dirty True via the observe callback (any doc
        # mutation does) -- reset it directly to model a room that already had its snapshot
        # taken and genuinely has nothing new to flush.
        mgr._rooms_by_id["flush-clean"].dirty = False

        await mgr.shutdown_flush()

        await mgr.release("flush-dirty")
        await mgr.release("flush-clean")

    anyio.run(scenario)
    gc.collect()

    db_session.expire_all()
    saved_dirty = db_session.get(Document, "flush-dirty")
    saved_clean = db_session.get(Document, "flush-clean")
    assert saved_dirty.content_html == "<p>dirty edit</p>"
    assert saved_clean.content_html == "<p>stays</p>"
    assert saved_clean.updated_at == clean_updated_at


def test_persist_half_never_receives_ydoc(tmp_path, monkeypatch, db_session, seed_users):
    """Plan 06-04 (D-30): the thread-affinity boundary the derive/persist split exists to
    protect -- `persist_snapshot_html` must receive only `str` arguments (doc_id, html),
    never `room.ydoc` or any other pycrdt object, since pycrdt's Rust-backed types are
    thread-affine and crash if touched off the event-loop thread that owns them (CLAUDE.md
    gotcha). Checks this two ways: the function's own signature is exactly (doc_id, html),
    and every argument actually observed at runtime across a real tick write and a real
    teardown write is a plain str. `_gc_after_ws_test`'s discipline is honored manually
    here via the trailing gc.collect() (that autouse fixture is scoped to
    test_collab_access.py only).
    """
    import inspect

    from app.collab import snapshot as collab_snapshot

    assert list(inspect.signature(collab_snapshot.persist_snapshot_html).parameters) == [
        "doc_id",
        "html",
    ]

    monkeypatch.setenv("SCRIBE_SNAPSHOT_INTERVAL", "0.2")
    monkeypatch.chdir(tmp_path)
    doc = Document(id="thread-1", title="T", content_html="", owner_id=seed_users["alice"].id)
    db_session.add(doc)
    db_session.commit()

    captured_args = []
    real_persist = collab_snapshot.persist_snapshot_html

    def spying_persist(doc_id, html):
        captured_args.append((doc_id, html))
        real_persist(doc_id, html)

    monkeypatch.setattr(collab_snapshot, "persist_snapshot_html", spying_persist)

    async def scenario():
        mgr = RoomManager()
        room = await mgr.get("thread-1")
        room.ydoc.get("config", type=Map)["seeded"] = True
        frag = room.ydoc.get("default", type=XmlFragment)
        para = XmlElement("paragraph")
        frag.children.append(para)
        para.children.append(XmlText("edit one"))
        # 0.25s against a 0.2s interval: comfortably past the first tick (which fires and
        # clears dirty) but with a wide (0.15s) margin before the next tick at 0.4s, so the
        # edit below and release() below are never racing the ticker's own wakeup -- a
        # tighter margin here proved flaky under Windows timer-resolution jitter.
        await anyio.sleep(0.25)

        para2 = XmlElement("paragraph")
        frag.children.append(para2)
        para2.children.append(XmlText("edit two"))
        await mgr.release("thread-1")  # the teardown write also calls persist

    anyio.run(scenario)
    gc.collect()

    assert len(captured_args) >= 2  # at least one tick write and one teardown write
    for doc_id_arg, html_arg in captured_args:
        assert isinstance(doc_id_arg, str)
        assert isinstance(html_arg, str)
