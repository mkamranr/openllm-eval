"""CLI: API tokens on every request, ``auth login/logout/whoami``, and the server admin CLI."""

from __future__ import annotations

import os
import stat

import httpx
import pytest
from typer.testing import CliRunner

from app.cli import main as cli

runner = CliRunner(mix_stderr=False)


@pytest.fixture(autouse=True)
def token_file(tmp_path, monkeypatch):
    path = tmp_path / "token"
    monkeypatch.setenv("OPENLLM_TOKEN_FILE", str(path))
    monkeypatch.delenv("OPENLLM_API_TOKEN", raising=False)
    return path


def test_token_sent_from_env_or_file(monkeypatch, token_file):
    seen = []

    def fake_request(method, url, timeout, **kwargs):
        seen.append(kwargs.get("headers"))
        return httpx.Response(200, json={"items": []})

    monkeypatch.setattr(cli.httpx, "request", fake_request)
    assert runner.invoke(cli.app, ["endpoint", "list"]).exit_code == 0
    assert seen[-1] is None  # no token configured: no header at all

    token_file.write_text("oel_from_file\n")
    runner.invoke(cli.app, ["endpoint", "list"])
    assert seen[-1] == {"Authorization": "Bearer oel_from_file"}

    monkeypatch.setenv("OPENLLM_API_TOKEN", "oel_from_env")
    runner.invoke(cli.app, ["endpoint", "list"])
    assert seen[-1] == {"Authorization": "Bearer oel_from_env"}


def test_401_prints_sign_in_hint(monkeypatch):
    monkeypatch.setattr(
        cli.httpx,
        "request",
        lambda method, url, timeout, **kw: httpx.Response(
            401, json={"error": {"code": "not_authenticated", "message": "Sign in required."}}
        ),
    )
    result = runner.invoke(cli.app, ["endpoint", "list"])
    assert result.exit_code == 1
    assert "openllm-eval auth login" in result.stderr


def _fake_server(requests):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == "/api/v1/auth/login":
            assert request.headers["X-OpenLLM-CSRF"] == "1"
            return httpx.Response(
                200,
                json={"username": "ada", "must_change_password": False},
                headers={"Set-Cookie": "oel_session=abc; Path=/api; HttpOnly"},
            )
        if path == "/api/v1/auth/tokens" and request.method == "POST":
            assert "oel_session=abc" in request.headers.get("cookie", "")
            return httpx.Response(
                201, json={"token": "oel_new_token_value", "prefix": "oel_new_to"}
            )
        if path == "/api/v1/auth/logout":
            return httpx.Response(204)
        return httpx.Response(404, json={"error": {"message": "no route"}})

    return handler


def test_auth_login_stores_token_privately(monkeypatch, token_file):
    requests: list[httpx.Request] = []
    real_client = httpx.Client
    monkeypatch.setattr(
        cli.httpx,
        "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(_fake_server(requests)), **kw),
    )
    result = runner.invoke(
        cli.app, ["auth", "login", "-u", "ada", "--password", "pw", "--api-url", "http://api"]
    )
    assert result.exit_code == 0, result.stderr
    assert token_file.read_text().strip() == "oel_new_token_value"
    if os.name != "nt":
        assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    assert [r.url.path for r in requests] == [
        "/api/v1/auth/login",
        "/api/v1/auth/tokens",
        "/api/v1/auth/logout",
    ]
    assert "oel_new_token_value" not in result.stdout


def test_auth_logout_revokes_and_deletes(monkeypatch, token_file):
    token_file.write_text("oel_new_token_value")
    deleted = []
    monkeypatch.setattr(
        cli.httpx,
        "get",
        lambda url, headers, timeout: httpx.Response(
            200, json=[{"id": "t1", "prefix": "oel_new_to", "revoked_at": None}]
        ),
    )
    monkeypatch.setattr(
        cli.httpx,
        "delete",
        lambda url, headers, timeout: deleted.append(url) or httpx.Response(204),
    )
    result = runner.invoke(cli.app, ["auth", "logout"])
    assert result.exit_code == 0
    assert deleted == ["http://localhost:8000/api/v1/auth/tokens/t1"]
    assert not token_file.exists()


# ------------------------------------------------------------------------- server admin CLI
@pytest.fixture
def admin_cli(tmp_path, monkeypatch):
    from alembic import command

    from app.core.config import get_settings
    from tests.integration.test_migrations import _alembic_config

    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{tmp_path / 'admin.db'}"
    command.upgrade(_alembic_config(url), "head")
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-auth-secret-key-0123456789")
    get_settings.cache_clear()
    from app.cli import admin

    yield admin
    get_settings.cache_clear()
    if os.environ.get("TEST_DATABASE_URL"):
        command.downgrade(_alembic_config(url), "base")


def test_admin_cli_users_and_tokens(admin_cli):
    app = admin_cli.app
    created = runner.invoke(app, ["create-user", "ops", "--role", "admin"])
    assert created.exit_code == 0, created.stderr
    assert "password: " in created.stdout

    listed = runner.invoke(app, ["list-users"])
    assert "ops" in listed.stdout and "must-change-password" in listed.stdout

    token = runner.invoke(app, ["create-token", "--service-user", "smoke", "--ttl", "1h"])
    assert token.exit_code == 0, token.stderr
    lines = token.stdout.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("oel_")  # stdout is only the token

    reset = runner.invoke(app, ["reset-password", "ops"])
    assert reset.exit_code == 0 and "password: " in reset.stdout
    assert runner.invoke(app, ["reset-password", "smoke"]).exit_code == 1  # service account

    assert runner.invoke(app, ["set-role", "ops", "viewer"]).exit_code == 0
    assert "viewer" in runner.invoke(app, ["list-users"]).stdout
    assert runner.invoke(app, ["delete-user", "smoke"]).exit_code == 0
    assert "smoke" not in runner.invoke(app, ["list-users"]).stdout
    assert runner.invoke(app, ["create-token", "--user", "nobody"]).exit_code == 1
    assert runner.invoke(app, ["disable-ldap"]).exit_code == 0


def test_admin_cli_password_from_stdin_checks_policy(admin_cli):
    weak = runner.invoke(
        admin_cli.app, ["create-user", "weak", "--password-stdin"], input="short\n"
    )
    assert weak.exit_code == 1 and "policy" in weak.stderr
    ok = runner.invoke(
        admin_cli.app,
        ["create-user", "strong", "--password-stdin", "--no-must-change"],
        input="Blue-Kettle-Morning-7\n",
    )
    assert ok.exit_code == 0 and "password:" not in ok.stdout


def test_admin_cli_check_password(admin_cli):
    app = admin_cli.app
    pw = "Blue-Kettle-Morning-7"
    assert (
        runner.invoke(app, ["create-user", "chk", "--password-stdin"], input=pw + "\n").exit_code
        == 0
    )
    good = runner.invoke(app, ["check-password", "chk"], input=pw + "\n")
    assert good.exit_code == 0 and pw not in good.stdout + good.stderr
    bad = runner.invoke(app, ["check-password", "chk"], input="Wrong-Kettle-Morning-7\n")
    assert bad.exit_code == 1 and "does not match" in bad.stderr
    assert runner.invoke(app, ["check-password", "nobody"], input=pw + "\n").exit_code == 1
