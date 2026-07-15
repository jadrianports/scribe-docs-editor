"""Focused tests for the RoomManager behavior required by the Task 2 review:
exception observability (a crashed room's background task must not be
fire-and-forget) and full eviction on the last client's release.
"""

import asyncio
import gc

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
