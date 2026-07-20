import asyncio
import gc
import logging
import os
import weakref
from dataclasses import dataclass

from anyio import Lock, fail_after
from pycrdt import Doc
from pycrdt.store import YDocNotFound
from pycrdt.websocket import YRoom

from . import snapshot
from .ystore import ScribeYStore

log = logging.getLogger(__name__)

# A room's startup (SQLite rehydration + the two YRoom readiness events) runs
# while `get()` holds the single lock shared by get()/release() for EVERY
# doc_id (see `_create_room`'s docstring). Bounding it means a hung/crashed
# startup for one doc_id can only ever stall other doc_ids for "a few
# seconds," not forever (Task 3 review, "Important" finding).
DEFAULT_STARTUP_TIMEOUT = 5.0

# Bounds release()'s final, already-dirty snapshot persist -- kept separate
# from DEFAULT_STARTUP_TIMEOUT above: 5s of teardown stall reads to a
# reconnecting peer as unresponsive, so this is a tighter bound (D-10).
RELEASE_SNAPSHOT_TIMEOUT = 2.0

# Bounds the whole graceful-shutdown flush loop (all dirty rooms combined),
# not any single room's persist -- a controlled shutdown can afford a little
# longer than a single release() (D-11).
SHUTDOWN_FLUSH_TIMEOUT = 5.0


@dataclass
class RoomRecord:
    """All per-room state RoomManager tracks for a single doc_id.

    Consolidates what used to be three parallel dicts (_rooms, _room_tasks,
    _counts) into one record (D-14), so per-doc fields touch one record field
    instead of N dict call sites. `dirty` is a plain bool the ticker's own
    `ydoc.observe` callback flips True on any edit (never a value stored
    inside the Y.Doc itself -- Pitfall 4); `ticker_task` is the per-room
    `_snapshot_ticker` background task; `dirty_subscription` is the
    `Subscription` handle returned by that `observe` call. Unobserving and
    clearing this handle at every teardown site is mandatory, not
    discretionary hygiene: it drops the record's own strong edge to the
    Subscription so pycrdt's Rust-backed, thread-affine object is finalized
    by ordinary refcounting on the event-loop thread that created it, rather
    than surviving to be swept by CPython's cyclic GC on whatever thread
    crosses its allocation threshold (D-08, D-30).
    """

    doc_id: str
    room: YRoom
    start_task: asyncio.Task
    count: int = 0
    dirty: bool = False
    ticker_task: asyncio.Task | None = None
    dirty_subscription: object | None = None


def _make_dirty_marker(record: RoomRecord):
    """Build the `ydoc.observe` callback that flips `record.dirty` True.

    Closes over ONLY a weak reference to `record` -- never the record
    itself, and never via a parameter whose default value is the record.
    That default-argument capture form is exactly what created a reference
    cycle (record -> dirty_subscription -> this callback -> record): plain
    refcounting can never free a cycle, so only CPython's cyclic GC could
    reclaim it, and that collector runs on whichever thread crosses its
    allocation threshold -- including an `asyncio.to_thread` persist worker.
    pycrdt's Rust-backed `Subscription` panics if finalized off the thread
    that created it (D-08, D-30), so the cycle had to go, not just be swept
    fast enough.

    The record is strongly held for its entire live span by two independent
    owners -- `RoomManager._rooms_by_id[doc_id]` while the room is cached,
    and the `_snapshot_ticker(doc_id, room, record)` coroutine frame for as
    long as the ticker task runs. Both outlive any window in which an edit
    could arrive and need marking, so this weak reference can never be dead
    while the room is live, and the D-12 dirty-flag contract is preserved.
    """
    record_ref = weakref.ref(record)

    def _mark_dirty(event) -> None:
        rec = record_ref()
        if rec is not None:
            rec.dirty = True

    return _mark_dirty


async def _snapshot_ticker(doc_id: str, room: YRoom, record: RoomRecord) -> None:
    """Periodically persist a dirty room's live content to `content_html`.

    Runs for the lifetime of `room`: wakes every `SCRIBE_SNAPSHOT_INTERVAL`
    seconds (default 15) and, only if an edit landed since the last tick
    (`record.dirty`), derives sanitized HTML from `room.ydoc` on this
    (event-loop) thread and persists it in a worker thread. An idle or
    read-only room costs zero derives and zero SQLite writes (D-01, D-02,
    D-05, D-12) -- `record.dirty` is checked, not assumed.

    The interval is read once, at task start (not at import time), so tests
    that construct fresh `RoomManager`/room instances under a monkeypatched
    `SCRIBE_SNAPSHOT_INTERVAL` see the override (Assumption A1). An
    unparseable value falls back to 15.0 with a log line -- the ticker must
    never fail to start over a bad env var (D-06).

    Must never die: any exception raised while deriving or persisting is
    caught *inside* the loop body (never by a try wrapping the whole
    `while True`), logged, and `record.dirty` is re-set to True so the next
    tick retries the same work rather than silently going dark for the rest
    of the room's life (D-06). `record.dirty` is captured-and-cleared before
    the awaited persist, so an edit landing mid-persist (or a persist that
    fails) re-arms the flag for the following tick -- no edit is silently
    dropped (D-03).
    """
    try:
        interval = float(os.environ.get("SCRIBE_SNAPSHOT_INTERVAL", "15"))
    except ValueError:
        log.error("invalid SCRIBE_SNAPSHOT_INTERVAL, falling back to 15s")
        interval = 15.0

    while True:
        await asyncio.sleep(interval)  # always wait a full interval first (D-05)
        if not record.dirty:
            continue  # nothing changed since the last tick -- no derive, no write
        record.dirty = False  # capture-and-clear BEFORE the awaited persist (D-03)
        try:
            html = snapshot.derive_snapshot_html(room.ydoc)  # loop thread -- owns ydoc
            if html is not None:
                await asyncio.to_thread(snapshot.persist_snapshot_html, doc_id, html)
        except Exception:
            record.dirty = True  # re-arm so the next tick retries (D-06)
            log.error("snapshot tick failed for doc %r", doc_id, exc_info=True)


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

        Also cancels the record's `ticker_task`, if any: a crashed room's
        `ydoc` is of unknown validity, so no snapshot is ever derived from it
        here -- the ticker is stopped outright, not given one last tick
        (D-15).
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
                if rec.ticker_task is not None:
                    rec.ticker_task.cancel()
                if rec.dirty_subscription is not None:
                    room.ydoc.unobserve(rec.dirty_subscription)
                    rec.dirty_subscription = None
                    gc.collect()  # finalize the Subscription here, on the event-loop thread (D-08, D-30)

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
        ticker_task: asyncio.Task | None = None
        record: RoomRecord | None = None
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

                record = RoomRecord(doc_id=doc_id, room=room, start_task=task)
                # The ticker's own dirty-flag subscription is registered only
                # now -- after rehydration (`apply_updates`, above) has already
                # replayed any persisted history into `ydoc` -- so replaying
                # history does not itself mark a freshly rehydrated, unedited
                # room dirty (D-12): a session that only ever reads a
                # rehydrated room costs zero writes. `record.dirty` is a plain
                # bool on the record, never a value stored inside the Y.Doc
                # itself (Pitfall 4).
                record.dirty_subscription = room.ydoc.observe(_make_dirty_marker(record))
                # One ticker per room, spawned alongside room.start() above --
                # no global sweeper (D-04).
                ticker_task = asyncio.create_task(_snapshot_ticker(doc_id, room, record))
                record.ticker_task = ticker_task
            return record
        except BaseException:
            log.error("collab room %r failed to start", doc_id, exc_info=True)
            if ticker_task is not None:
                ticker_task.cancel()
            if task is not None:
                task.cancel()
            # The observe() registration above sits shortly before this try
            # block would otherwise succeed, so a fail_after timeout (or any
            # other exception) can fire in that narrow window and abandon a
            # registered Subscription that nothing else would ever unobserve
            # -- same finalize-on-the-event-loop-thread rationale as the
            # other three teardown sites (D-08, D-30).
            if record is not None and record.dirty_subscription is not None:
                record.room.ydoc.unobserve(record.dirty_subscription)
                record.dirty_subscription = None
                gc.collect()
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

                # Cancel the ticker and await its cancellation BEFORE deriving
                # the final snapshot below (D-13): this guarantees exactly one
                # writer ever runs at teardown -- a stale in-flight tick can
                # never land after (or race) this final write. Unobserving
                # the dirty subscription (and clearing the handle, and
                # collecting) here is mandatory, not discretionary hygiene:
                # it drops the record's own strong edge to the Subscription so
                # it's finalized by refcounting on THIS (event-loop) thread,
                # before the asyncio.to_thread persist below can schedule a
                # worker thread that might otherwise cross the cyclic-GC
                # threshold and panic on a still-cyclic, thread-affine
                # pycrdt Subscription (D-08, D-30). gc.collect() here is a
                # synchronous full collection inside release()'s single
                # shared lock (CLAUDE.md), so this adds collection time to
                # that critical section -- accepted because room teardown is
                # a rare event (the last client disconnecting) against a
                # small heap, versus the alternative of a reproducible pycrdt
                # panic; Phase 7's per-doc lock work should revisit this.
                # With Task 1's cycle already broken, the Subscription itself
                # is freed by refcounting the instant record's last strong
                # reference drops -- this collect is defense in depth, to
                # deterministically sweep any OTHER cyclic garbage YRoom or
                # pycrdt-websocket may own before a worker thread can cross
                # the threshold first.
                if rec.ticker_task is not None:
                    rec.ticker_task.cancel()
                    try:
                        await rec.ticker_task
                    except asyncio.CancelledError:
                        pass
                if rec.dirty_subscription is not None:
                    room.ydoc.unobserve(rec.dirty_subscription)
                    rec.dirty_subscription = None
                    gc.collect()

                # Only derive+persist a snapshot if an edit landed since the
                # last successful write (D-12) -- room.ydoc must still be a
                # live, valid Doc when derive_snapshot_html reads it, so this
                # runs before room.stop() below. A failing/timed-out snapshot
                # must never skip room.stop(): doc_id was already popped from
                # self._rooms_by_id above (evicted), so nothing would ever
                # call stop() on THIS room again -- its task group and its
                # YStore connection would leak for the life of the process.
                # Log-and-continue, same pattern as _make_crash_evictor above;
                # the persist half never has its worker thread cancelled
                # (asyncio.to_thread cannot interrupt a blocking commit --
                # only the await is bounded, not the underlying write).
                if rec.dirty:
                    try:
                        html = snapshot.derive_snapshot_html(room.ydoc)
                        if html is not None:
                            with fail_after(RELEASE_SNAPSHOT_TIMEOUT):
                                await asyncio.to_thread(snapshot.persist_snapshot_html, doc_id, html)
                    except TimeoutError:
                        log.error("snapshot persist timed out for doc %r", doc_id)
                    except Exception:
                        log.error("failed to write snapshot for doc %r", doc_id, exc_info=True)

                await room.stop()
                return True
            return False

    async def shutdown_flush(self) -> None:
        """Persist every dirty room's snapshot on graceful shutdown (D-11).

        Called once from the app lifespan after the ASGI server's `yield`
        (SIGTERM, `docker stop`, or a controlled `uvicorn --reload` restart),
        turning every controlled shutdown into zero collab-session data loss
        -- a SIGKILL still costs at most one `_snapshot_ticker` interval.

        Takes a shallow, lock-free snapshot of the current rooms
        (`list(self._rooms_by_id.values())`, D-16): this must never become a
        new caller on `self._lock` -- the shared lock every doc_id serializes
        through is Phase 7's problem, not this one's -- and a room
        appearing or vanishing mid-flush (a client connecting/disconnecting
        during shutdown) is harmless here; it just sees a point-in-time list.

        Bounded by one overall `SHUTDOWN_FLUSH_TIMEOUT`, not a per-room
        timeout (D-11): a slow shutdown log-and-moves-on rather than hanging
        the process exit. Within that bound, each dirty room is persisted in
        its own try/except -- one room's failure (or the ydoc no longer being
        seeded) does not stop the rest from flushing (D-06 pattern).
        """
        records = list(self._rooms_by_id.values())

        # Pre-pass: unobserve+clear every still-registered dirty subscription
        # on THIS (event-loop) thread, then collect once, before the flush
        # loop below can schedule its first asyncio.to_thread persist. Same
        # rationale as release() (D-08, D-30) -- drop the record's own strong
        # edge to the Subscription so it's finalized by refcounting here,
        # not swept by a worker thread's cyclic GC. Iterates the SAME
        # `records` shallow copy and does NOT touch record.dirty and takes
        # no lock (D-16): the set of rooms flushed, and the order they flush
        # in, is unchanged by this pass. Deliberately outside the
        # fail_after(SHUTDOWN_FLUSH_TIMEOUT) block below, so cleanup work
        # never consumes the flush budget.
        for record in records:
            if record.dirty_subscription is not None:
                record.room.ydoc.unobserve(record.dirty_subscription)
                record.dirty_subscription = None
        gc.collect()

        flushed = 0
        try:
            with fail_after(SHUTDOWN_FLUSH_TIMEOUT):
                for record in records:
                    if not record.dirty:
                        continue  # idle/read-only room -- zero writes (D-12)
                    try:
                        html = snapshot.derive_snapshot_html(record.room.ydoc)
                        if html is not None:
                            await asyncio.to_thread(
                                snapshot.persist_snapshot_html, record.doc_id, html
                            )
                        record.dirty = False
                        flushed += 1
                    except Exception:
                        log.error(
                            "shutdown flush failed for doc %r", record.doc_id, exc_info=True
                        )
        except TimeoutError:
            log.error("shutdown flush timed out -- some dirty rooms may be unflushed")
        log.info("shutdown flush persisted %d of %d room(s)", flushed, len(records))


room_manager = RoomManager()
