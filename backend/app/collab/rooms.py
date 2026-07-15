import asyncio
import logging

from anyio import Lock
from pycrdt import Doc
from pycrdt.store import YDocNotFound
from pycrdt.websocket import YRoom

from .ystore import ScribeYStore

log = logging.getLogger(__name__)


class RoomManager:
    def __init__(self) -> None:
        self._rooms: dict[str, YRoom] = {}
        self._room_tasks: dict[str, asyncio.Task] = {}
        self._counts: dict[str, int] = {}
        self._lock = Lock()

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
        in that case. The `self._rooms.get(doc_id) is room` identity check
        guards the rarer case where a fresh room was already created for
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
            if self._rooms.get(doc_id) is room:
                self._rooms.pop(doc_id, None)
                self._room_tasks.pop(doc_id, None)
                self._counts.pop(doc_id, None)

        return _on_done

    async def get(self, doc_id: str) -> YRoom:
        async with self._lock:
            room = self._rooms.get(doc_id)
            if room is None:
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
                self._room_tasks[doc_id] = task
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
                self._rooms[doc_id] = room
                self._counts[doc_id] = 0
            self._counts[doc_id] += 1
            return room

    async def release(self, doc_id: str) -> bool:
        async with self._lock:
            if doc_id not in self._counts:
                return False
            self._counts[doc_id] -= 1
            if self._counts[doc_id] <= 0:
                room = self._rooms.pop(doc_id)
                self._counts.pop(doc_id)
                self._room_tasks.pop(doc_id, None)
                await room.stop()
                return True
            return False


room_manager = RoomManager()
