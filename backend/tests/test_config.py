"""Startup/config hardening tests for Phase 8 (D-13): each criterion is
checked directly, not merely exercised through a happy-path assertion.
"""

import os

import pytest
from fastapi import FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware
from starlette.testclient import TestClient

from app import config
from app.collab.ystore import ScribeYStore


def test_is_production_false_when_scribe_env_unset(monkeypatch):
    monkeypatch.delenv("SCRIBE_ENV", raising=False)
    assert config.is_production() is False


def test_is_production_false_for_non_production_value(monkeypatch):
    monkeypatch.setenv("SCRIBE_ENV", "dev")
    assert config.is_production() is False


def test_is_production_false_for_wrong_case(monkeypatch):
    monkeypatch.setenv("SCRIBE_ENV", "PRODUCTION")
    assert config.is_production() is False


def test_is_production_true_for_production(monkeypatch):
    monkeypatch.setenv("SCRIBE_ENV", "production")
    assert config.is_production() is True


def test_resolve_secret_key_raises_in_production_without_key(monkeypatch):
    monkeypatch.setenv("SCRIBE_ENV", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        config.resolve_secret_key()


def test_resolve_secret_key_raises_message_names_scribe_env(monkeypatch):
    monkeypatch.setenv("SCRIBE_ENV", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SCRIBE_ENV=production"):
        config.resolve_secret_key()


def test_resolve_secret_key_returns_set_value_in_production(monkeypatch):
    monkeypatch.setenv("SCRIBE_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "a-real-secret")
    assert config.resolve_secret_key() == "a-real-secret"


def test_resolve_secret_key_dev_fallback_when_both_unset(monkeypatch):
    monkeypatch.delenv("SCRIBE_ENV", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    assert config.resolve_secret_key() == "dev-secret-change-me"


def test_resolve_secret_key_dev_uses_set_value(monkeypatch):
    monkeypatch.delenv("SCRIBE_ENV", raising=False)
    monkeypatch.setenv("SECRET_KEY", "dev-custom-key")
    assert config.resolve_secret_key() == "dev-custom-key"


def _session_cookie_app():
    """A minimal in-process app, built the same way main.py builds its
    SessionMiddleware, so the Set-Cookie header can be inspected without a
    live_server subprocess (D-14).
    """
    app = FastAPI()
    app.add_middleware(
        SessionMiddleware,
        secret_key="x",
        same_site="lax",
        https_only=config.is_production(),
    )

    @app.get("/set")
    def set_session(request: Request):
        request.session["k"] = "v"
        return {"ok": True}

    return app


def test_session_cookie_is_secure_and_lax_in_production(monkeypatch):
    monkeypatch.setenv("SCRIBE_ENV", "production")
    client = TestClient(_session_cookie_app())
    response = client.get("/set")
    set_cookie = response.headers["set-cookie"]
    assert "secure" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()


def test_session_cookie_omits_secure_in_dev(monkeypatch):
    monkeypatch.delenv("SCRIBE_ENV", raising=False)
    client = TestClient(_session_cookie_app())
    response = client.get("/set")
    set_cookie = response.headers["set-cookie"]
    assert "secure" not in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()


# --- validate_data_dir() (D-11/D-12): data-dir preflight matrix ---


def test_validate_data_dir_logs_info_absolute_path_always(tmp_path, monkeypatch, caplog):
    """Every branch must log the resolved absolute path at INFO, naming
    SCRIBE_DATA_DIR -- proven here with a writable absolute path in dev."""
    data_dir = tmp_path / "yjsdata"
    abs_db_path = str(data_dir / "yjs.db")
    monkeypatch.setattr(ScribeYStore, "db_path", abs_db_path)
    monkeypatch.delenv("SCRIBE_ENV", raising=False)

    with caplog.at_level("INFO"):
        config.validate_data_dir()

    assert "SCRIBE_DATA_DIR" in caplog.text
    assert os.path.abspath(abs_db_path) in caplog.text


def test_validate_data_dir_unwritable_raises_in_dev(tmp_path, monkeypatch):
    """An existing FILE at the would-be directory location makes os.makedirs
    fail cross-platform -- simulates an unwritable data dir without relying
    on platform-specific permission bits (Windows os.access is unreliable
    for directories)."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    monkeypatch.setattr(ScribeYStore, "db_path", str(blocker / "yjs.db"))
    monkeypatch.delenv("SCRIBE_ENV", raising=False)

    with pytest.raises(RuntimeError, match="not writable"):
        config.validate_data_dir()


def test_validate_data_dir_unwritable_raises_in_production(tmp_path, monkeypatch):
    """The unwritable-dir raise is fatal in BOTH modes (D-11) -- not just dev."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    monkeypatch.setattr(ScribeYStore, "db_path", str(blocker / "yjs.db"))
    monkeypatch.setenv("SCRIBE_ENV", "production")

    with pytest.raises(RuntimeError, match="not writable"):
        config.validate_data_dir()


def test_validate_data_dir_warns_in_production_when_relative(tmp_path, monkeypatch, caplog):
    """Production + a relative (unset/relative SCRIBE_DATA_DIR) db_path is the
    ephemeral-loss heuristic -- must WARN naming the var, the absolute path,
    and the history-loss consequence, and must NOT raise."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ScribeYStore, "db_path", os.path.join("data", "yjs.db"))
    monkeypatch.setenv("SCRIBE_ENV", "production")

    with caplog.at_level("INFO"):
        config.validate_data_dir()  # must not raise

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    warning_text = warnings[0].getMessage()
    assert "SCRIBE_DATA_DIR" in warning_text
    assert os.path.abspath(os.path.join("data", "yjs.db")) in warning_text
    assert "lost" in warning_text.lower() or "restart" in warning_text.lower()


def test_validate_data_dir_dev_relative_logs_only_no_warning_no_raise(
    tmp_path, monkeypatch, caplog
):
    """Dev with a relative path (the normal single-command local run, no
    SCRIBE_DATA_DIR set) must never warn or raise -- criterion 4."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ScribeYStore, "db_path", os.path.join("data", "yjs.db"))
    monkeypatch.delenv("SCRIBE_ENV", raising=False)

    with caplog.at_level("INFO"):
        config.validate_data_dir()  # must not raise

    assert not any(r.levelname == "WARNING" for r in caplog.records)
    assert "SCRIBE_DATA_DIR" in caplog.text


def test_validate_data_dir_production_absolute_no_warning(tmp_path, monkeypatch, caplog):
    """Production with an absolute, writable db_path (mirrors Render's /data)
    is the acknowledged non-warning case -- INFO only."""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(ScribeYStore, "db_path", str(data_dir / "yjs.db"))
    monkeypatch.setenv("SCRIBE_ENV", "production")

    with caplog.at_level("INFO"):
        config.validate_data_dir()

    assert not any(r.levelname == "WARNING" for r in caplog.records)
    assert "SCRIBE_DATA_DIR" in caplog.text


def test_validate_data_dir_references_ystore_db_path_not_rederived():
    """D-12: the preflight must reference ScribeYStore.db_path directly and
    must not re-derive the SCRIBE_DATA_DIR join itself, so the logged path
    can never drift from the real store path."""
    import inspect

    source = inspect.getsource(config.validate_data_dir)
    assert "ScribeYStore.db_path" in source
    assert 'os.environ.get("SCRIBE_DATA_DIR"' not in source
