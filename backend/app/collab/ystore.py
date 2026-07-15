import os
from pycrdt.store import SQLiteYStore


class ScribeYStore(SQLiteYStore):
    # All documents share one SQLite file, keyed internally by the store `path`.
    db_path = "data/yjs.db"

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
