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

    # --- Authentication (M8) ---
    # On by default. AUTH_ENABLED=false makes every request an anonymous administrator, for
    # single-user installs on a trusted machine only.
    auth_enabled: bool = True
    # Encrypts directory secrets at rest (LDAP bind password, Kerberos keytab). Any long random
    # string; deploy.sh generates one. Changing it makes stored secrets unreadable.
    auth_secret_key: SecretStr | None = None
    # The first administrator, created at startup when there are no users at all. It must
    # change this password at first sign-in.
    admin_username: str = Field(default="admin", pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    admin_initial_password: SecretStr | None = None
    auth_session_idle_minutes: int = Field(default=480, gt=0)
    auth_session_max_hours: int = Field(default=72, gt=0)
    # Session cookie "Secure" flag: auto = set when the request arrived over HTTPS (including
    # X-Forwarded-Proto: https from a TLS proxy in front of the UI); true / false force it.
    auth_cookie_secure: Literal["auto", "true", "false"] = "auto"
    # Failed sign-ins: lock the username after this many failures within the window; a client
    # address is locked after auth_lockout_ip_threshold failures (offices often share an IP).
    auth_lockout_threshold: int = Field(default=5, gt=0)
    auth_lockout_ip_threshold: int = Field(default=20, gt=0)
    auth_lockout_window_seconds: int = Field(default=900, gt=0)
    auth_lockout_seconds: int = Field(default=900, gt=0)
    # Extra origins allowed to make cookie-authenticated changes (CSRF check), besides the
    # request's own host and CORS_ORIGINS. Example: https://openllm.corp.example.com
    auth_trusted_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- Integration-mode tool execution (M9, spec §5.7 mode C, §12.6) ---
    # Off by default. When on, runs may use tool_mode "integration": tool calls are executed by
    # the separate tool-runner service (docker compose profile "tools").
    tool_execution_enabled: bool = False
    tool_runner_url: str = "http://tool-runner:8100"
    # Shared secret between the worker/API and the tool runner; deploy.sh generates it.
    tool_runner_token: SecretStr | None = None
    # Tool runner only: where tools.yaml and the scripts are (mounted read-only).
    tools_dir: str = "/tools"
    tools_file: str = Field(default="tools.yaml", pattern=r"^[A-Za-z0-9._-]+\.ya?ml$")
    # Tool runner only: destinations HTTP tools may call. Empty = HTTP tools are blocked.
    tool_allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)
    tool_runner_max_concurrency: int = Field(default=8, gt=0, le=64)
    tool_script_memory_mb: int = Field(default=256, ge=32, le=4096)

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

    @field_validator("auth_cookie_secure", mode="before")
    @classmethod
    def _cookie_secure(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return "auto"
        if isinstance(value, bool):
            return "true" if value else "false"
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator(
        "cors_origins",
        "endpoint_allowed_hosts",
        "endpoint_blocked_hosts",
        "auth_trusted_origins",
        "tool_allowed_hosts",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
