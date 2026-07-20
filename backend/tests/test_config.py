"""Startup/config hardening tests for Phase 8 (D-13): each criterion is
checked directly, not merely exercised through a happy-path assertion.
"""

import pytest

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
