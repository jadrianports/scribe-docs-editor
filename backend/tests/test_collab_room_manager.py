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
from pycrdt.websocket import YRoom

from app.collab.rooms import RoomManager

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
        assert mgr._rooms["doc-crash"] is room
        assert isinstance(mgr._room_tasks["doc-crash"], asyncio.Task)

        await anyio.sleep(0.3)  # let the background task crash and the done-callback run

        assert "doc-crash" not in mgr._rooms
        assert "doc-crash" not in mgr._room_tasks
        assert "doc-crash" not in mgr._counts

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
        assert mgr._counts["doc-refcount"] == 2

        with anyio.fail_after(5):
            evicted = await mgr.release("doc-refcount")
        assert evicted is False  # one client remains
        assert "doc-refcount" in mgr._rooms
        assert mgr._counts["doc-refcount"] == 1

        with anyio.fail_after(5):
            evicted = await mgr.release("doc-refcount")
        assert evicted is True  # last client
        assert "doc-refcount" not in mgr._rooms
        assert "doc-refcount" not in mgr._room_tasks
        assert "doc-refcount" not in mgr._counts

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
        # this hardening fixes -- not a stale entry in _room_tasks either.
        assert "doc-hang" not in mgr._rooms
        assert "doc-hang" not in mgr._counts
        assert "doc-hang" not in mgr._room_tasks

        # The healthy, unrelated doc_id is cached normally and cleans up fine.
        assert "doc-other" in mgr._rooms
        with anyio.fail_after(5):
            await mgr.release("doc-other")

    anyio.run(scenario)
    gc.collect()
