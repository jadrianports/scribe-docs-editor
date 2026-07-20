"""Focused tests for the RoomManager behavior required by the Task 2 review:
exception observability (a crashed room's background task must not be
fire-and-forget) and full eviction on the last client's release. Also covers
the Task 3 review's "Important" finding: get()/release() share ONE global
lock across every doc_id, and (before the fix below) held it across the
startup awaits with no timeout -- see test_hung_room_startup_is_bounded... .
"""

import asyncio
import gc
import sys
import time
import weakref

import anyio
from pycrdt import Map, Text, XmlElement, XmlFragment, XmlText
from pycrdt.websocket import YRoom

from app.collab.rooms import RoomManager, RoomRecord, _make_dirty_marker
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

    Retargeted (plan 06-04, D-24/D-25): release() no longer calls
    write_snapshot directly -- plan 03 split it into derive_snapshot_html
    (event-loop thread) + persist_snapshot_html (worker thread) -- so the
    failure injection point moves to persist_snapshot_html. Unlike the old
    unconditional write_snapshot(doc_id, ydoc) call this used to intercept,
    persist only runs when the room is both seeded and dirty, so this test
    now seeds+edits the room first to actually exercise that path. The two
    other REQ-snapshot-recovery-tests modes (network disconnect during
    autosave; WS reconnect with pending title change) are frontend paths
    handed to Phase 9's criterion 4, not this phase (D-24).
    """
    monkeypatch.chdir(tmp_path)

    from app.collab import snapshot as collab_snapshot

    def raising_persist(doc_id, html):
        raise RuntimeError("boom: simulated locked DB")

    monkeypatch.setattr(collab_snapshot, "persist_snapshot_html", raising_persist)

    async def scenario():
        mgr = RoomManager()

        with anyio.fail_after(5):
            room = await mgr.get("doc-snapshot-fail")

        room.ydoc.get("config", type=Map)["seeded"] = True  # so release() reaches persist
        frag = room.ydoc.get("default", type=XmlFragment)
        para = XmlElement("paragraph")
        frag.children.append(para)
        para.children.append(XmlText("edited"))

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


def test_ticker_cancelled_before_teardown_write(tmp_path, monkeypatch, db_session, seed_users):
    """Plan 06-04 (D-13): release() must cancel-and-await the ticker task BEFORE
    deriving/persisting the final teardown snapshot -- guaranteeing exactly one writer ever
    runs at teardown, so a stale in-flight tick can never land after (or race) the final
    write. Instruments persist_snapshot_html itself to assert the ticker task is already
    done() at the moment the teardown write actually happens, not merely after release()
    has returned (which would be a much weaker ordering guarantee).
    """
    from app.collab import snapshot as collab_snapshot

    monkeypatch.chdir(tmp_path)
    doc = Document(id="order-1", title="O", content_html="", owner_id=seed_users["alice"].id)
    db_session.add(doc)
    db_session.commit()

    ticker_task_ref: dict[str, asyncio.Task] = {}
    observed_done_at_persist: dict[str, bool] = {}
    real_persist = collab_snapshot.persist_snapshot_html

    def spying_persist(doc_id, html):
        observed_done_at_persist["value"] = ticker_task_ref["task"].done()
        real_persist(doc_id, html)

    monkeypatch.setattr(collab_snapshot, "persist_snapshot_html", spying_persist)

    async def scenario():
        mgr = RoomManager()
        room = await mgr.get("order-1")
        record = mgr._rooms_by_id["order-1"]
        ticker_task_ref["task"] = record.ticker_task

        room.ydoc.get("config", type=Map)["seeded"] = True
        frag = room.ydoc.get("default", type=XmlFragment)
        para = XmlElement("paragraph")
        frag.children.append(para)
        para.children.append(XmlText("edited"))

        with anyio.fail_after(5):
            evicted = await mgr.release("order-1")

        assert evicted is True
        assert "order-1" not in mgr._rooms_by_id
        assert record.ticker_task.cancelled() or record.ticker_task.done()

    anyio.run(scenario)
    gc.collect()

    assert observed_done_at_persist["value"] is True  # ticker was already done when persist ran


def test_dirty_marker_holds_no_strong_reference_to_record():
    """Plan 06-06 (Task 1): pins the exact property the weakref-based fix exists to
    guarantee -- `_make_dirty_marker`'s returned callback closes over ONLY a weak
    reference to its `RoomRecord`, never the record itself. A pure unit test: no
    RoomManager, no event loop, no threads, no timing -- `RoomRecord` performs no
    runtime validation, so placeholder `None`s for `room`/`start_task` are fine here.

    Two halves, both required:

    1. Behavioral: the record starts clean and invoking the marker sets `dirty`
       True. This guards against a "fix" that silently stops marking rooms dirty
       -- that would regress D-12 into rooms that never write, i.e. silent data
       loss dressed up as a passing test suite.
    2. Structural: a `weakref.ref` to the record goes dead the instant the only
       local strong reference is dropped -- proving refcounting ALONE reclaims
       the record, with no cyclic-GC assist. Deliberately NO `gc.collect()` call
       appears anywhere before this assertion (and must never be added): a
       collect() here would make the assertion pass even if the cycle were fully
       restored (e.g. reverting to the old default-argument-capture marker),
       converting a real regression test into one that can never go red again.
    """
    record = RoomRecord(doc_id="x", room=None, start_task=None)
    marker = _make_dirty_marker(record)

    assert record.dirty is False
    marker(None)
    assert record.dirty is True

    ref = weakref.ref(record)
    del record
    assert ref() is None, "record survived refcounting -- the reference cycle is still present"


def test_abandoned_room_subscription_is_not_finalized_on_worker_thread(tmp_path, monkeypatch):
    """Plan 06-06 (Task 2/VERIFICATION truth #9): the direct encoding of the
    reproduced panic -- `pycrdt::subscription::Subscription is unsendable, but is
    being dropped on another thread`. Builds a genuinely seeded+dirty room (a live
    `dirty_subscription` Subscription exists), releases it (running the Task 2
    teardown path: unobserve + clear + gc.collect on the event-loop thread), drops
    every local strong reference, then deliberately schedules a cyclic-GC pass on a
    REAL worker thread via `asyncio.to_thread(gc.collect)` -- exactly the condition
    that crashes the unfixed marker.

    Installs a capturing replacement for `sys.unraisablehook` via
    `monkeypatch.setattr` (monkeypatch restores the original automatically).
    pytest's own unraisable plugin hooks the same attribute, so this deliberately
    takes over for the duration of the test. The hook fires on whichever thread
    finalizes the offending object, so the list is appended to from the worker
    thread spawned by `asyncio.to_thread` above -- a plain list append is safe
    under the GIL, so no lock is needed around it.

    Asserts on the captured contents (not merely on the list being empty) so a
    failure names the offending object/exception directly, rather than just
    reporting a count.
    """
    monkeypatch.chdir(tmp_path)

    captured: list[object] = []

    def capturing_hook(args):
        captured.append(args)

    monkeypatch.setattr(sys, "unraisablehook", capturing_hook)

    async def scenario():
        mgr = RoomManager()
        room = await mgr.get("abandoned-1")
        record = mgr._rooms_by_id["abandoned-1"]

        room.ydoc.get("config", type=Map)["seeded"] = True  # mirror EditorPage.tsx's seed effect
        frag = room.ydoc.get("default", type=XmlFragment)
        para = XmlElement("paragraph")
        frag.children.append(para)
        para.children.append(XmlText("edited"))
        assert record.dirty is True  # genuinely dirty, not just seeded

        with anyio.fail_after(5):
            evicted = await mgr.release("abandoned-1")
        assert evicted is True

        del room, record, mgr  # drop every local strong reference

        # A cyclic-GC pass deliberately scheduled on a real worker thread --
        # exactly the condition that panics the unfixed default-argument marker.
        await asyncio.to_thread(gc.collect)

    anyio.run(scenario)
    gc.collect()  # file convention -- main-thread collect after anyio.run

    offending = [
        args
        for args in captured
        if isinstance(args.exc_value, RuntimeError)
        and ("unsendable" in str(args.exc_value) or "dropped on another thread" in str(args.exc_value))
    ]
    assert not offending, f"pycrdt Subscription finalized off its creating thread: {offending!r}"


def test_release_clears_the_yroom_internal_subscription(tmp_path, monkeypatch):
    """`release()` must drop YRoom's own strong edge to its internal pycrdt
    `Subscription`, not just the record's `dirty_subscription`.

    `YRoom.stop()` unobserves that handle but never sets `self._subscription =
    None`, so the Subscription stays reachable from the YRoom -- and the YRoom
    outlives `stop()` via its cancelled `start()`/`_broadcast_updates` task
    frames. The graph then becomes cyclic garbage collected on whatever thread
    gets there first, which panics because pycrdt Subscriptions are
    thread-affine (D-08, D-30). Under `TestClient`'s per-connection portal
    thread the creating thread is already dead by then, so there is no safe
    thread left at all -- this is what made the four WebSocket tests in
    test_collab_access.py error once the record leak that had been masking it
    was fixed.

    Asserting the handle is non-None *before* release is load-bearing: without
    it this test would still pass if pycrdt-websocket stopped registering a
    subscription at all, quietly becoming vacuous.
    """
    monkeypatch.chdir(tmp_path)

    async def scenario():
        mgr = RoomManager()

        with anyio.fail_after(5):
            room = await mgr.get("doc-yroom-sub")

        # Precondition: the library really does hold a subscription here.
        assert getattr(room, "_subscription", None) is not None

        with anyio.fail_after(5):
            evicted = await mgr.release("doc-yroom-sub")
        assert evicted is True

        assert getattr(room, "_subscription", "missing") is None, (
            "YRoom._subscription survived release() -- the Subscription's lifetime "
            "is still bound to the YRoom's and can be finalized off-thread"
        )

    anyio.run(scenario)
    gc.collect()
