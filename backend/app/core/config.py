"""Application configuration loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL


def normalize_database_url(url: str) -> str:
    """Map plain driver-less URLs onto the async drivers this application uses.

    ``postgresql://`` and ``postgres://`` become ``postgresql+psycopg://`` (psycopg 3 supports
    asyncio); ``sqlite://`` becomes ``sqlite+aiosqlite://``. URLs that already name a driver
    are returned unchanged.
    """
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    if url.startswith("sqlite://"):
        return "sqlite+aiosqlite://" + url[len("sqlite://") :]
    return url


def build_postgres_url(
    *, host: str, port: int, user: str, password: str | None, database: str
) -> str:
    """Build an async PostgreSQL URL with every component correctly percent-escaped."""
    url = URL.create(
        "postgresql+psycopg",
        username=user,
        password=password,
        host=host,
        port=port,
        database=database,
    )
    return url.render_as_string(hide_password=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite+aiosqlite:///./openllm_eval.db"
    # Alternative to DATABASE_URL: discrete PostgreSQL settings. When POSTGRES_HOST is set and
    # DATABASE_URL is not, the URL is built from these with proper escaping, so passwords may
    # contain any characters (@ : / # % $ ...).
    postgres_host: str | None = None
    postgres_port: int = Field(default=5432, gt=0, lt=65536)
    postgres_user: str = "openllm"
    postgres_password: SecretStr | None = None
    postgres_db: str = "openllm_eval"
    redis_url: str = "redis://localhost:6379/0"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    db_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    redis_connect_timeout_seconds: float = Field(default=2.0, gt=0)
    worker_heartbeat_interval_seconds: float = Field(default=5.0, gt=0)
    worker_heartbeat_ttl_seconds: int = Field(default=15, gt=0)

    # --- Model endpoint network policy (SSRF protection, spec §12.3) ---
    # Private/loopback/CGNAT destinations (typical for self-hosted vLLM) need explicit opt-in.
    endpoint_allow_private_networks: bool = False
    # If non-empty, ONLY these destinations are allowed: hostnames, "*.suffix" wildcards, IPs,
    # or CIDRs. Entries here may be private even when private networks are otherwise disabled.
    endpoint_allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Hostnames that are never valid model endpoints (this deployment's own services).
    endpoint_blocked_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["postgres", "redis"]
    )
    endpoint_max_response_bytes: int = Field(default=16 * 1024 * 1024, gt=0)

    # --- Secrets ---
    # Endpoints reference secrets as "env:<NAME>"; only variables with this prefix can be read,
    # so a reference can never exfiltrate unrelated server environment (e.g. POSTGRES_PASSWORD).
    secret_env_prefix: str = Field(default="OPENLLM_SECRET_", min_length=1)
    # Directory that custom CA bundles (endpoint ca_cert_path) must live in. Unset = feature off.
    ca_cert_dir: str | None = None

    # --- Datasets (spec §12.4: file size, row and field limits) ---
    dataset_max_upload_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    dataset_max_records: int = Field(default=20_000, gt=0)
    # One record, serialized; long-context cases are the large ones (~1M chars ≈ 250k tokens).
    dataset_max_record_bytes: int = Field(default=4 * 1024 * 1024, gt=0)

    # --- Benchmark runs (spec §5.4, §13.2) ---
    run_max_requests: int = Field(default=100_000, gt=0)
    run_max_concurrency: int = Field(default=256, gt=0, le=256)  # adapter pool size is 256
    run_max_duration_seconds: int = Field(default=24 * 3600, gt=0)
    run_max_endpoints: int = Field(default=10, gt=0)
    # A worker renews its lease on a run while executing it; an expired lease means the worker
    # died, and another worker resumes the run.
    run_lease_seconds: float = Field(default=30.0, gt=0)
    # How often results are flushed to the database, progress is published, and cancellation
    # is checked.
    run_progress_interval_seconds: float = Field(default=1.0, gt=0)
    worker_poll_interval_seconds: float = Field(default=1.0, gt=0)
    # Privacy (spec §8.2): store generated text with results, truncated to this many characters.
    run_store_responses: bool = True
    run_response_max_chars: int = Field(default=20_000, ge=0)
    # Reports: at most this many recorded attempts are included in a report or export.
    report_max_results: int = Field(default=50_000, ge=1, le=1_000_000)

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, value: str) -> str:
        return normalize_database_url(value)

    @model_validator(mode="after")
    def _build_database_url_from_parts(self) -> Settings:
        if self.postgres_host and "database_url" not in self.model_fields_set:
            self.database_url = build_postgres_url(
                host=self.postgres_host,
                port=self.postgres_port,
                user=self.postgres_user,
                password=self.postgres_password.get_secret_value()
                if self.postgres_password
                else None,
                database=self.postgres_db,
            )
        return self

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator(
        "cors_origins", "endpoint_allowed_hosts", "endpoint_blocked_hosts", mode="before"
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
