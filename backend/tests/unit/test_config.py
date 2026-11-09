from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings, normalize_database_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgresql://u:p@db:5432/x", "postgresql+psycopg://u:p@db:5432/x"),
        ("postgres://u:p@db/x", "postgresql+psycopg://u:p@db/x"),
        ("postgresql+psycopg://u@db/x", "postgresql+psycopg://u@db/x"),
        ("sqlite:///./a.db", "sqlite+aiosqlite:///./a.db"),
        ("sqlite+aiosqlite:///./a.db", "sqlite+aiosqlite:///./a.db"),
    ],
)
def test_normalize_database_url(raw, expected):
    assert normalize_database_url(raw) == expected


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://openllm:pw@postgres:5432/openllm_eval")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173, http://example.test")
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+psycopg://openllm:pw@postgres:5432/openllm_eval"
    assert settings.redis_url == "redis://redis:6379/0"
    assert settings.log_level == "DEBUG"
    assert settings.cors_origins == ["http://localhost:5173", "http://example.test"]


def test_defaults_need_no_external_services(monkeypatch):
    for var in (
        "DATABASE_URL",
        "REDIS_URL",
        "ENVIRONMENT",
        "LOG_LEVEL",
        "CORS_ORIGINS",
        "POSTGRES_HOST",
    ):
        monkeypatch.delenv(var, raising=False)
    settings = Settings(_env_file=None)
    assert settings.database_url.startswith("sqlite+aiosqlite://")
    assert settings.environment == "development"
    assert settings.cors_origins == []


def test_invalid_values_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, log_format="xml")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, worker_heartbeat_interval_seconds=0)


NASTY_PASSWORD = "P@ss:w/rd#$ecUr3P%?& 'x\""


def test_database_url_built_from_parts_escapes_special_characters(monkeypatch):
    from sqlalchemy.engine import make_url

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_USER", "openllm")
    monkeypatch.setenv("POSTGRES_PASSWORD", NASTY_PASSWORD)
    monkeypatch.setenv("POSTGRES_DB", "openllm_eval")
    settings = Settings(_env_file=None)

    url = make_url(settings.database_url)
    assert url.drivername == "postgresql+psycopg"
    assert url.host == "postgres"
    assert url.port == 5432
    assert url.username == "openllm"
    assert url.password == NASTY_PASSWORD  # round-trips exactly
    assert url.database == "openllm_eval"
    # The secret is not exposed through the settings repr.
    assert NASTY_PASSWORD not in repr(settings.postgres_password)


def test_explicit_database_url_wins_over_parts(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://a:b@explicit:5432/x")
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+psycopg://a:b@explicit:5432/x"


def test_parts_ignored_without_host(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.setenv("POSTGRES_PASSWORD", "whatever")
    assert Settings(_env_file=None).database_url.startswith("sqlite+aiosqlite://")
