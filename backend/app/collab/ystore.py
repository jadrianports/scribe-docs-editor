import os
from pycrdt.store import SQLiteYStore


class ScribeYStore(SQLiteYStore):
    # All documents share one SQLite file, keyed internally by the store `path`.
    #
    # `SCRIBE_DATA_DIR` lets a deploy colocate this with `scribe.db` on the
    # same persistent volume -- important #2 from the final whole-branch
    # review. Before this, `db_path` was the bare relative string
    # "data/yjs.db", which in Docker (WORKDIR /app/backend at CMD time)
    # resolved to /app/backend/data/yjs.db -- OUTSIDE the mounted /data
    # volume `scribe.db` uses via `DATABASE_URL=sqlite:////data/scribe.db`
    # (see Dockerfile/app/db.py), so live Yjs edit history was lost on every
    # container recreation even though `scribe.db` itself persisted.
    # `os.environ.get("SCRIBE_DATA_DIR", "data")` keeps the default (unset --
    # local/non-Docker dev, and every existing test that relies on
    # `monkeypatch.chdir(tmp_path)` to redirect this *relative* path)
    # functionally identical to before: still the relative two-segment path
    # "data" + "yjs.db", resolved against cwd at open time exactly as the old
    # hardcoded "data/yjs.db" was (`os.path.join` uses the OS-native
    # separator, so on Windows the literal string is "data\\yjs.db" rather
    # than "data/yjs.db" -- cosmetic only; `os.makedirs`/SQLite/every path
    # API involved accepts both, confirmed by the full collab suite staying
    # green). Only an explicit `SCRIBE_DATA_DIR` (set to "/data" in the
    # Dockerfile) changes this to an absolute, volume-colocated path. Read
    # once at import time -- same timing as `app.db`'s own
    # `DATABASE_URL = os.environ.get(...)` -- so it must be set in the
    # environment before this module is first imported (true for both the
    # Docker CMD and FIX 2's subprocess `env=`).
    db_path = os.path.join(os.environ.get("SCRIBE_DATA_DIR", "data"), "yjs.db")

    def __init__(self, *args, **kwargs) -> None:
        # Create the containing directory relative to the *current* working
        # directory at construction time, not import time. A module-level
        # `os.makedirs("data")` only ever runs once, under whatever cwd was
        # active the first time this module got imported -- fine in
        # production (always `backend/`), but tests isolate themselves via
        # `monkeypatch.chdir(tmp_path)` *after* import, so `tmp_path/data`
        # would never get created and SQLite would fail with
        # "unable to open database file". Constructing a store per doc_id
        # (as RoomManager does) makes this cheap and always correct.
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        super().__init__(*args, **kwargs)
