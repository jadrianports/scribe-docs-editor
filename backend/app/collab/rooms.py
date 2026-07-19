import asyncio
import logging
from dataclasses import dataclass

from anyio import Lock, fail_after
from pycrdt import Doc
from pycrdt.store import YDocNotFound
from pycrdt.websocket import YRoom

from .ystore import ScribeYStore

log = logging.getLogger(__name__)

# A room's startup (SQLite rehydration + the two YRoom readiness events) runs
# while `get()` holds the single lock shared by get()/release() for EVERY
# doc_id (see `_create_room`'s docstring). Bounding it means a hung/crashed
# startup for one doc_id can only ever stall other doc_ids for "a few
# seconds," not forever (Task 3 review, "Important" finding).
DEFAULT_STARTUP_TIMEOUT = 5.0


@dataclass
class RoomRecord:
    """All per-room state RoomManager tracks for a single doc_id.

    Consolidates what used to be three parallel dicts (_rooms, _room_tasks,
    _counts) into one record (D-14), so later work that adds per-doc fields
    (the tick machinery in plan 03: `dirty`, `ticker_task`,
    `dirty_subscription`) touches one record field instead of N dict call
    sites. `dirty` and `ticker_task` are unused this plan -- they exist here
    so plan 03 doesn't need to touch this dataclass's shape again.
    """

    doc_id: str
    room: YRoom
    start_task: asyncio.Task
    count: int = 0
    dirty: bool = False
    ticker_task: asyncio.Task | None = None
    dirty_subscription: object | None = None


class RoomManager:
    def __init__(self, startup_timeout: float = DEFAULT_STARTUP_TIMEOUT) -> None:
        self._rooms_by_id: dict[str, RoomRecord] = {}
        self._lock = Lock()
        self._startup_timeout = startup_timeout

    def _make_crash_evictor(self, doc_id: str, room: YRoom):
        """Build the done-callback for `room`'s background `start()` task.

        `asyncio.create_task(room.start())` must not be fire-and-forget: if the
        room crashes *after* `started.set()` (e.g. the YStore fails to start, or
        a broadcaster task raises), nothing else ever awaits this task, so an
        unretrieved exception would otherwise only surface as a "Task exception
        was never retrieved" warning at GC time -- and the dead room would stay
        cached, silently serving future connections with a broadcaster that's
        no longer running. This logs the exception right away and evicts the
        room so the next `get()` for `doc_id` builds (and starts) a fresh one.

        On a *normal* stop (via `release()` -> `room.stop()`), `room.start()`
        returns with no exception (cancellation is swallowed inside the task
        group, not raised out), and `release()` has already evicted the room
        synchronously before awaiting `stop()` -- so this callback is a no-op
        in that case. The `self._rooms_by_id.get(doc_id).room is room` identity
        check guards the rarer case where a fresh room was already created for
        `doc_id` by the time this (necessarily late) callback runs, so a stale
        callback can never evict a healthy, unrelated room.
        """

        def _on_done(task: asyncio.Task) -> None:
            if task.cancelled():
                return
            exc = task.exception()
            if exc is None:
                return
            log.error("collab room %r crashed", doc_id, exc_info=exc)
            # Plain sync pop, no lock/await needed -- see identity-check note above.
            rec = self._rooms_by_id.get(doc_id)
            if rec is not None and rec.room is room:
                self._rooms_by_id.pop(doc_id, None)

        return _on_done

    async def get(self, doc_id: str) -> YRoom:
        async with self._lock:
            rec = self._rooms_by_id.get(doc_id)
            if rec is None:
                rec = await self._create_room(doc_id)
                self._rooms_by_id[doc_id] = rec
            rec.count += 1
            return rec.room

    async def _create_room(self, doc_id: str) -> RoomRecord:
        """Build and start a fresh room for `doc_id`.

        Called from `get()` while `self._lock` -- the single lock shared by
        get()/release() for EVERY doc_id -- is held. Before this method
        existed, `get()` awaited the rehydration read and both YRoom readiness
        events (`started`, `ydoc_observed`) inline, with no timeout: a room
        that hung during startup (or whose background task crashed without
        ever completing that readiness handshake -- from here, an Event that
        will never be set looks identical either way) would hold the lock
        forever, wedging get()/release() for every OTHER document too (Task 3
        review, "Important" finding). `anyio.fail_after` bounds the whole
        sequence to `self._startup_timeout` seconds, so the worst case becomes
        "a few seconds," not "forever."

        On any failure -- timeout or a genuine exception (e.g. from SQLite) --
        `self._rooms_by_id` is untouched (this method's return value is only
        cached by `get()` on success), but the room's start() task may already
        have been created before the failure -- cancel it so a later get() for
        the same doc_id doesn't inherit a stale, still-running task (the
        crash-evictor's `self._rooms_by_id.get(doc_id).room is room` guard
        never fires for a room that was never cached).
        """
        task: asyncio.Task | None = None
        try:
            with fail_after(self._startup_timeout):
                ydoc = Doc()
                store = ScribeYStore(path=doc_id)
                async with store as s:              # start the store to read history
                    try:
                        await s.apply_updates(ydoc)  # rehydrate persisted state
                    except YDocNotFound:
                        pass
                room = YRoom(ready=True, ydoc=ydoc, ystore=ScribeYStore(path=doc_id))
                # start-once/serve-many (Task 2, Phase-0 gate): bare `await room.start()`
                # blocks forever (it runs until the room stops). Task/started.wait() is the
                # confirmed-working lower-level lifecycle from the YRoom docstring.
                task = asyncio.create_task(room.start())
                task.add_done_callback(self._make_crash_evictor(doc_id, room))
                await room.started.wait()
                # `started` only means the task group has launched; the room's
                # `_watch_ready` background task (which registers the
                # `ydoc.observe(...)` subscription that feeds both persistence
                # and client broadcast) is merely *scheduled* at that point, not
                # necessarily run yet. Without also waiting for `ydoc_observed`,
                # a caller that mutates `room.ydoc` immediately after `get()`
                # returns can race ahead of the subscription being registered,
                # silently losing that update (never streamed to the YStore or
                # to other clients). `ydoc_observed` is set by `_watch_ready`
                # right after the subscription is in place -- confirmed by
                # reading `pycrdt/websocket/yroom.py` after this race
                # reproduced deterministically (empty YStore after an insert).
                await room.ydoc_observed.wait()
            return RoomRecord(doc_id=doc_id, room=room, start_task=task)
        except BaseException:
            log.error("collab room %r failed to start", doc_id, exc_info=True)
            if task is not None:
                task.cancel()
            raise

    async def release(self, doc_id: str) -> bool:
        async with self._lock:
            rec = self._rooms_by_id.get(doc_id)
            if rec is None:
                return False
            rec.count -= 1
            if rec.count <= 0:
                room = rec.room
                self._rooms_by_id.pop(doc_id)
                # Snapshot before stop(): room.ydoc must still be a live,
                # valid Doc when write_snapshot reads it (Task 7). No actual
                # import-cycle risk (snapshot.py's own imports -- app.db,
                # app.models, app.content, .html -- never import rooms.py
                # back, confirmed by inspection); kept as a local import to
                # match the brief's given wiring, and it costs nothing since
                # release() is not a hot path.
                from .snapshot import write_snapshot

                # A failing snapshot (e.g. a locked DB) must never skip
                # room.stop() below: doc_id was already popped from
                # self._rooms_by_id above (evicted), so nothing would ever
                # call stop() on THIS room again -- its task group, its
                # ydoc.observe() subscription, and its YStore connection
                # would all leak for the life of the process. Log-and-continue,
                # same pattern as _make_crash_evictor above.
                try:
                    write_snapshot(doc_id, room.ydoc)
                except Exception:
                    log.error("failed to write snapshot for doc %r", doc_id, exc_info=True)
                await room.stop()
                return True
            return False


room_manager = RoomManager()
