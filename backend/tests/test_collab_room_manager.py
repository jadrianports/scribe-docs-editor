"""Focused tests for the RoomManager behavior required by the Task 2 review:
exception observability (a crashed room's background task must not be
fire-and-forget) and full eviction on the last client's release. Also covers
the Task 3 review's "Important" finding: get()/release() share ONE global
lock across every doc_id, and (before the fix below) held it across the
startup awaits with no timeout -- see test_hung_room_startup_is_bounded... .
"""

import asyncio
import gc
import time

import anyio
from pycrdt import Map, Text, XmlElement, XmlFragment, XmlText
from pycrdt.websocket import YRoom

from app.collab.rooms import RoomManager
from app.models import Document

# pycrdt's Rust-backed Doc/Subscription objects are thread-affine (`!Send`)
# (see task-1-report.md's "Secondary finding"): if one gets garbage-collected
# on a different OS thread than it was created on, pyo3 raises during `__del__`,
# which surfaces as an unraisable-exception warning wherever pytest's GC happens
# to run next -- observed attributed to an unrelated, later test file (one that
# uses TestClient, which spins a new thread per connection) rather than to the
# collab test that actually created the object. Forcing collection here, still
# on this test's own single thread, reclaims these objects deterministically
# before control returns to pytest and any later test's TestClient thread.


def test_crashed_room_task_is_evicted(tmp_path, monkeypatch):
    """A room whose background `start()` task crashes *after* `started` (and
    `ydoc_observed`) are set must not linger in the cache: the done-callback
    must log the exception and evict the room, so the next `get()` for the
    same doc_id builds (and starts) a fresh room instead of being served by
    one whose broadcaster/persistence task has died.
    """
    monkeypatch.chdir(tmp_path)

    async def crashing_start(self, **kwargs):
        # Mimic just enough of the real `YRoom._start()` sequence for
        # `RoomManager.get()` to observe a normal startup -- it awaits both
        # `started` and `ydoc_observed` -- before the room "crashes". This is
        # exactly the scenario the done-callback exists for: a *late* crash,
        # after the room already looked healthy enough to be cached and used.
        self.started.set()
        self.ydoc_observed.set()
        await anyio.sleep(0.05)
        raise RuntimeError("boom: simulated room crash")

    monkeypatch.setattr(YRoom, "start", crashing_start)

    async def scenario():
        mgr = RoomManager()

        with anyio.fail_after(5):
            room = await mgr.get("doc-crash")
        assert mgr._rooms_by_id["doc-crash"].room is room
        assert isinstance(mgr._rooms_by_id["doc-crash"].start_task, asyncio.Task)

        await anyio.sleep(0.3)  # let the background task crash and the done-callback run

        assert "doc-crash" not in mgr._rooms_by_id

        # A subsequent connection must get a fresh room, not the dead one.
        with anyio.fail_after(5):
            room2 = await mgr.get("doc-crash")
        assert room2 is not room

    anyio.run(scenario)
    gc.collect()


def test_release_evicts_room_on_last_client(tmp_path, monkeypatch):
    """`get()` ref-counts shared access to the same doc_id (no duplicate
    room), and `release()` only fully evicts room+task+count once the last
    client releases -- returning False while clients remain, True (and a
    fully-cleared cache) on the last one.
    """
    monkeypatch.chdir(tmp_path)

    async def scenario():
        mgr = RoomManager()

        with anyio.fail_after(5):
            room_a = await mgr.get("doc-refcount")
        with anyio.fail_after(5):
            room_b = await mgr.get("doc-refcount")
        assert room_a is room_b  # same doc_id -> same cached room, not a duplicate
        assert mgr._rooms_by_id["doc-refcount"].count == 2

        with anyio.fail_after(5):
            evicted = await mgr.release("doc-refcount")
        assert evicted is False  # one client remains
        assert "doc-refcount" in mgr._rooms_by_id
        assert mgr._rooms_by_id["doc-refcount"].count == 1

        with anyio.fail_after(5):
            evicted = await mgr.release("doc-refcount")
        assert evicted is True  # last client
        assert "doc-refcount" not in mgr._rooms_by_id

    anyio.run(scenario)
    gc.collect()


def test_hung_room_startup_is_bounded_and_does_not_leak_room_tasks(tmp_path, monkeypatch):
    """Task 3 review, "Important" finding: `get()` awaits the rehydration read
    and both YRoom readiness events (`started`, `ydoc_observed`) while holding
    `self._lock` -- the ONE lock shared by get()/release() for EVERY doc_id.
    Before this fix, a room whose startup hangs (or whose background task
    crashes without ever completing that readiness handshake -- from get()'s
    point of view, awaiting an Event that will never be set looks identical
    either way) would hold that lock forever, wedging get()/release() for
    every other document too, with no way out.

    `RoomManager._create_room` now wraps the whole startup sequence in
    `anyio.fail_after(self._startup_timeout)`. This proves two things at
    once:

    1. The stall is now BOUNDED, not indefinite: a get() for the hung doc_id
       fails fast with TimeoutError (instead of hanging forever), which -- by
       ordinary `async with self._lock:` semantics -- releases the shared
       lock, so a concurrent get() for a second, completely unrelated doc_id
       still succeeds well within the same window rather than being wedged
       behind the first indefinitely. (The two calls are still internally
       serialized by the single global lock, by design -- see rooms.py's
       `_create_room` docstring -- so this demonstrates "bounded to a few
       seconds," not true lock-free concurrency between doc_ids.)
    2. The pre-cache `_room_tasks[doc_id]` leak is fixed: the failed doc_id's
       start() task -- registered in `_room_tasks` before the hang -- does not
       linger there once the timeout fires (previously nothing would ever
       evict it, since the crash-evictor's `self._rooms.get(doc_id) is room`
       guard never fires for a room that was never cached).
    """
    monkeypatch.chdir(tmp_path)

    real_start = YRoom.start  # capture before patching, so "doc-other" behaves normally

    async def hang_only_doc_hang(self, **kwargs):
        # `YRoom.start` is a class-level patch (applies to every room the test
        # creates), so distinguish "doc-hang" from "doc-other" via the room's
        # own ystore.path (== doc_id, since RoomManager always constructs
        # `ScribeYStore(path=doc_id)`). Only "doc-hang" hangs -- never setting
        # started/ydoc_observed, simulating a startup that hangs (or a
        # background task that silently died before finishing); "doc-other"
        # gets the real start() and starts up normally.
        if getattr(self.ystore, "path", None) == "doc-hang":
            await anyio.sleep_forever()
        else:
            await real_start(self, **kwargs)

    monkeypatch.setattr(YRoom, "start", hang_only_doc_hang)

    async def scenario():
        mgr = RoomManager(startup_timeout=0.2)
        results: dict[str, object] = {}

        async def get_hang():
            try:
                await mgr.get("doc-hang")
            except TimeoutError:
                results["hang"] = "timeout"

        async def get_other():
            results["other"] = await mgr.get("doc-other")

        start = time.monotonic()
        with anyio.fail_after(5):  # outer safety net for the test itself
            async with anyio.create_task_group() as tg:
                tg.start_soon(get_hang)
                tg.start_soon(get_other)
        elapsed = time.monotonic() - start

        assert results["hang"] == "timeout"
        assert results["other"] is not None  # the unrelated doc_id still got a real room

        # Bounded by ~startup_timeout (0.2s), not "hangs forever": before this
        # fix there was no timeout at all, so this could never have completed.
        assert elapsed < 2.0

        # The doc_id that failed to start must not leave anything cached or
        # leaked: not the room, not the ref count, and -- the specific leak
        # this hardening fixes -- not a stale start_task either (the record
        # holds all three, so absence of the record is absence of all of them).
        assert "doc-hang" not in mgr._rooms_by_id

        # The healthy, unrelated doc_id is cached normally and cleans up fine.
        assert "doc-other" in mgr._rooms_by_id
        with anyio.fail_after(5):
            await mgr.release("doc-other")

    anyio.run(scenario)
    gc.collect()


def test_release_stops_and_evicts_room_even_if_snapshot_write_fails(tmp_path, monkeypatch):
    """Task 9 fold-in (Task 7 review "Important" finding): `release()` used
    to call `write_snapshot(doc_id, room.ydoc)` with no try/except before
    `await room.stop()`. If the snapshot raised (e.g. a locked DB), the
    exception propagated straight out of release() and `room.stop()` was
    never reached -- but doc_id had *already* been popped from
    self._rooms/_counts/_room_tasks by that point (this method pops before
    snapshotting), so nothing would ever call stop() on this room again: its
    task group, its ydoc.observe() subscription, and its YStore connection
    would all leak for the life of the process, and the exception would blow
    up whatever called release() (the collab WS route's `finally:` block).

    release() must log-and-continue on a snapshot failure (mirroring
    `_make_crash_evictor`'s pattern above) and unconditionally reach
    `room.stop()`. Proven two ways: release() must return normally (not
    raise) with the room still evicted from bookkeeping, AND the room itself
    must actually have been stopped -- `room._task_group is None` is exactly
    what `YRoom.stop()` leaves behind (confirmed by reading its source), so
    it directly distinguishes "stopped" from merely "evicted from the
    manager's dict but still running in the background."
    """
    monkeypatch.chdir(tmp_path)

    from app.collab import snapshot as collab_snapshot

    def raising_write_snapshot(doc_id, ydoc):
        raise RuntimeError("boom: simulated locked DB")

    monkeypatch.setattr(collab_snapshot, "write_snapshot", raising_write_snapshot)

    async def scenario():
        mgr = RoomManager()

        with anyio.fail_after(5):
            room = await mgr.get("doc-snapshot-fail")

        # Must not raise: the old code let write_snapshot's RuntimeError
        # propagate straight out of release().
        with anyio.fail_after(5):
            evicted = await mgr.release("doc-snapshot-fail")

        assert evicted is True
        assert "doc-snapshot-fail" not in mgr._rooms_by_id
        assert room._task_group is None  # i.e. room.stop() actually ran

    anyio.run(scenario)
    gc.collect()


def test_clean_room_ticker_writes_nothing(tmp_path, monkeypatch, db_session, seed_users):
    """Plan 06-04 (D-12): an idle/never-seeded room's ticker must derive and write nothing --
    closing a read-only-viewer session (or a room that only ever touches a scratch root,
    mirroring test_release_does_not_overwrite_content_when_room_never_seeded in
    test_collab_persistence.py) costs zero SQLite writes and never bumps `updated_at` on an
    unrelated document, across at least one tick interval, with no release() call before the
    assertion.
    """
    monkeypatch.setenv("SCRIBE_SNAPSHOT_INTERVAL", "0.05")
    monkeypatch.chdir(tmp_path)
    doc = Document(
        id="clean-1", title="C", content_html="<p>original</p>", owner_id=seed_users["alice"].id
    )
    db_session.add(doc)
    db_session.commit()
    original_updated_at = doc.updated_at

    async def scenario():
        mgr = RoomManager()
        room = await mgr.get("clean-1")
        # Touches only a scratch root -- "default"/"config" are never touched, so this room
        # is never seeded (mirrors test_collab_persistence.py's un-seeded-guard tests).
        room.ydoc.get("scratch", type=Text).insert(0, "not the document body")
        await anyio.sleep(0.3)  # past a couple of 0.05s tick intervals
        await mgr.release("clean-1")

    anyio.run(scenario)
    gc.collect()

    db_session.expire_all()
    saved = db_session.get(Document, "clean-1")
    assert saved.content_html == "<p>original</p>"
    assert saved.updated_at == original_updated_at


def test_dirty_flag_lifecycle(tmp_path, monkeypatch, db_session, seed_users):
    """Plan 06-04 (D-28): drives `RoomRecord.dirty` through all four transitions the tick,
    teardown, and shutdown-flush writers all depend on: fresh=clean (right after get(),
    before any edit), seed->dirty (the ticker's own `ydoc.observe` callback flips it True
    the instant `config`/`seeded` is set), snapshot->clean (after one tick derives+persists,
    the capture-and-clear-before-persist step resets it), edit->dirty (a further edit
    re-arms it). Losing any one of these transitions silently regresses into a room that
    never writes (dirty stuck False -> lost edits) or writes every tick regardless of
    whether anything changed (dirty stuck True -> write amplification).
    """
    monkeypatch.setenv("SCRIBE_SNAPSHOT_INTERVAL", "0.05")
    monkeypatch.chdir(tmp_path)
    doc = Document(id="dirty-1", title="D", content_html="", owner_id=seed_users["alice"].id)
    db_session.add(doc)
    db_session.commit()

    async def scenario():
        mgr = RoomManager()
        room = await mgr.get("dirty-1")
        record = mgr._rooms_by_id["dirty-1"]

        assert record.dirty is False  # fresh=clean

        room.ydoc.get("config", type=Map)["seeded"] = True  # mirror EditorPage.tsx's seed effect
        assert record.dirty is True  # seed->dirty

        await anyio.sleep(0.3)  # let one tick derive+persist and clear the flag
        assert record.dirty is False  # snapshot->clean

        frag = room.ydoc.get("default", type=XmlFragment)
        para = XmlElement("paragraph")
        frag.children.append(para)
        para.children.append(XmlText("further edit"))
        assert record.dirty is True  # edit->dirty

        await mgr.release("dirty-1")

    anyio.run(scenario)
    gc.collect()
