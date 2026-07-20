"""Startup/config hardening tests for Phase 8 (D-13): each criterion is
checked directly, not merely exercised through a happy-path assertion.
"""

import pytest
from fastapi import FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware
from starlette.testclient import TestClient

from app import config


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
