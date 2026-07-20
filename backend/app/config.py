"""Single source of the dev-vs-production mode gate.

`SCRIBE_ENV=production` is the one explicit signal the backend has for
"this is a hardened deploy." Unset (or any other value) means dev. Both the
SECRET_KEY resolution and the session cookie's `https_only` flag read the
mode from here so the two can never disagree (see main.py).

Like `SCRIBE_DATA_DIR`/`SCRIBE_SNAPSHOT_INTERVAL` elsewhere in this codebase,
the default lives in code, not in a repo-tracked `.env` file.
"""

import os


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
