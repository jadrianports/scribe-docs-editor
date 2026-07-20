"""Single source of the dev-vs-production mode gate.

`SCRIBE_ENV=production` is the one explicit signal the backend has for
"this is a hardened deploy." Unset (or any other value) means dev. Both the
SECRET_KEY resolution and the session cookie's `https_only` flag read the
mode from here so the two can never disagree (see main.py).

Like `SCRIBE_DATA_DIR`/`SCRIBE_SNAPSHOT_INTERVAL` elsewhere in this codebase,
the default lives in code, not in a repo-tracked `.env` file.
"""

import logging
import os

from .collab.ystore import ScribeYStore

logger = logging.getLogger(__name__)


def is_production() -> bool:
    """True iff SCRIBE_ENV is exactly "production". Read at call time (not a
    module-level constant) so tests can drive it with monkeypatch.setenv
    without importlib.reload.
    """
    return os.environ.get("SCRIBE_ENV") == "production"


def resolve_secret_key() -> str:
    """Resolve the session-signing key.

    In production, a missing/empty SECRET_KEY is fatal -- raises immediately
    rather than booting with a forgeable key. In dev, the existing
    "dev-secret-change-me" fallback is preserved byte-for-byte.
    """
    secret_key = os.environ.get("SECRET_KEY")
    if is_production() and not secret_key:
        raise RuntimeError(
            "SECRET_KEY must be set when SCRIBE_ENV=production -- refusing to "
            "start with a forgeable session-signing key."
        )
    return secret_key or "dev-secret-change-me"


def validate_data_dir() -> None:
    """Startup preflight for the Yjs collaboration store's data directory.

    Resolves the SAME path `ScribeYStore` uses (its `db_path` class
    attribute) rather than re-deriving the `SCRIBE_DATA_DIR` join, so the
    path this logs can never drift from the path the store actually opens
    (see ystore.py:31).

    Always logs the resolved absolute path at INFO. Raises RuntimeError, in
    both dev and production, if the containing directory cannot be created
    or written to -- a broken persistence path should fail loudly at
    startup, not silently at the first collaborative edit. In production,
    if SCRIBE_DATA_DIR is unset or relative (the only runtime-detectable
    signal of an ephemeral/misconfigured mount), emits a loud WARNING
    naming the variable, the resolved path, and the history-loss-on-restart
    consequence, then returns -- non-fatal. In dev, a relative path is
    expected (no SCRIBE_DATA_DIR is required for local work) and only the
    INFO line is logged.
    """
    abs_path = os.path.abspath(ScribeYStore.db_path)
    logger.info("SCRIBE_DATA_DIR resolves yjs.db to %s", abs_path)

    data_dir = os.path.dirname(abs_path)
    try:
        os.makedirs(data_dir, exist_ok=True)
        probe_path = os.path.join(data_dir, ".scribe-write-probe")
        with open(probe_path, "w") as probe_file:
            probe_file.write("")
        os.remove(probe_path)
    except OSError as exc:
        raise RuntimeError(
            f"SCRIBE_DATA_DIR is not writable -- cannot create or write to "
            f"{abs_path}: {exc}"
        ) from exc

    if is_production() and not os.path.isabs(ScribeYStore.db_path):
        logger.warning(
            "SCRIBE_DATA_DIR is unset or relative (resolved to %s) while "
            "SCRIBE_ENV=production -- if this is not a persistent, "
            "absolute mount, all collaboration history will be lost on "
            "the next container restart.",
            abs_path,
        )
