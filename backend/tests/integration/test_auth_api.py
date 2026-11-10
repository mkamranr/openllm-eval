"""Authentication and authorization through the API."""

from __future__ import annotations

import asyncio
import base64
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.routing import APIRoute
from pydantic import SecretStr
from sqlalchemy import select, update

from app.auth.tokens import SESSION_COOKIE
from app.db.models.auth import ApiToken, AuditEvent, AuthSettings, User, UserSession
from tests import ldap_fixtures as fx
from tests.conftest import create_test_user

CSRF = {"X-OpenLLM-CSRF": "1"}
ADMIN_PASSWORD = "Bootstrap-Admin-Pass-1"


def fresh(api: httpx.AsyncClient) -> httpx.AsyncClient:
    """A client on the same app with no credentials (and its own cookie jar)."""
    return httpx.AsyncClient(transport=api._transport, base_url="http://test")


async def sign_in(client: httpx.AsyncClient, username: str, password: str) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}, headers=CSRF
    )


async def bearer(api: httpx.AsyncClient, username: str, role: str, **kw) -> dict[str, str]:
    _, token = await create_test_user(api._transport.app, username, role, **kw)  # type: ignore[attr-defined]
    return {"Authorization": f"Bearer {token}"}


async def db_scalar(api: httpx.AsyncClient, query):
    async with api._transport.app.state.session_factory() as s:  # type: ignore[attr-defined]
        return (await s.scalars(query)).all()


async def db_exec(api: httpx.AsyncClient, statement) -> None:
    async with api._transport.app.state.session_factory() as s:  # type: ignore[attr-defined]
        await s.execute(statement)
        await s.commit()


# ------------------------------------------------------------------------- route coverage
PUBLIC_PATHS = {
    "/api/v1/health",
    "/api/v1/health/ready",
    "/api/v1/version",
    "/api/v1/auth/providers",
    "/api/v1/auth/login",
    "/api/v1/auth/negotiate",
}


async def test_every_non_public_route_rejects_anonymous(anon, migrated_app):
    routes = [r for r in migrated_app.routes if isinstance(r, APIRoute)]
    checked = 0
    for route in routes:
        if route.path in PUBLIC_PATHS:
            continue
        path = re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000000", route.path)
        for method in route.methods:
            response = await anon.request(method, path)
            assert response.status_code == 401, (method, route.path, response.status_code)
            assert response.json()["error"]["code"] == "not_authenticated"
            checked += 1
    assert checked > 60


async def test_public_routes(anon):
    assert (await anon.get("/api/v1/health")).status_code == 200
    ready = (await anon.get("/api/v1/health/ready")).json()
    assert ready["auth_enabled"] is True
    providers = (await anon.get("/api/v1/auth/providers")).json()
    assert providers == {"auth_enabled": True, "local": True, "ldap": False, "kerberos": False}
    assert (await anon.get("/api/v1/auth/negotiate")).status_code == 404  # SSO not enabled


# -------------------------------------------------------------------------- bootstrap admin
@pytest.fixture
def bootstrap_settings(settings):
    settings.admin_initial_password = SecretStr(ADMIN_PASSWORD)
    return settings


async def test_bootstrap_admin_must_change_password(bootstrap_settings, anon):
    client = anon
    me_before = await client.get("/api/v1/auth/me")
    assert me_before.status_code == 401

    login = await sign_in(client, "admin", ADMIN_PASSWORD)
    assert login.status_code == 200, login.text
    assert login.json()["must_change_password"] is True
    cookie = login.headers["set-cookie"]
    assert "httponly" in cookie.lower() and "samesite=lax" in cookie.lower()
    assert "path=/api" in cookie.lower()

    blocked = await client.get("/api/v1/projects")
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "password_change_required"
    assert (await client.get("/api/v1/auth/me")).json()["must_change_password"] is True

    reuse = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": ADMIN_PASSWORD, "new_password": ADMIN_PASSWORD},
        headers=CSRF,
    )
    assert reuse.status_code == 422
    changed = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": ADMIN_PASSWORD, "new_password": "A-Brand-New-Pass-77"},
        headers=CSRF,
    )
    assert changed.status_code == 204, changed.text
    assert (await client.get("/api/v1/projects")).status_code == 200

    async with fresh(client) as other:
        assert (await sign_in(other, "admin", ADMIN_PASSWORD)).status_code == 401
        assert (await sign_in(other, "admin", "A-Brand-New-Pass-77")).status_code == 200


# ----------------------------------------------------------------------- sessions and CSRF
async def test_login_me_logout_and_csrf(api):
    await create_test_user(api._transport.app, "erin", "editor", password="Erin-Password-2026")
    async with fresh(api) as c:
        no_header = await c.post(
            "/api/v1/auth/login", json={"username": "erin", "password": "Erin-Password-2026"}
        )
        assert no_header.status_code == 403
        assert no_header.json()["error"]["code"] == "csrf_failed"
        assert (await sign_in(c, "Erin", "Erin-Password-2026")).status_code == 200
        me = (await c.get("/api/v1/auth/me")).json()
        assert (
            me["username"] == "erin" and me["role"] == "editor" and me["auth_method"] == "session"
        )

        body = {"name": "csrf-project"}
        assert (await c.post("/api/v1/projects", json=body)).status_code == 403
        foreign = await c.post(
            "/api/v1/projects", json=body, headers={**CSRF, "Origin": "https://evil.example"}
        )
        assert foreign.status_code == 403
        same = await c.post(
            "/api/v1/projects", json=body, headers={**CSRF, "Origin": "http://test"}
        )
        assert same.status_code == 201

        assert (await c.post("/api/v1/auth/logout", headers=CSRF)).status_code == 204
        assert (await c.get("/api/v1/auth/me")).status_code == 401


async def test_wrong_password_is_generic_and_audited(api):
    await create_test_user(api._transport.app, "frank", "viewer", password="Frank-Password-2026")
    async with fresh(api) as c:
        wrong = await sign_in(c, "frank", "nope")
        unknown = await sign_in(c, "nobody", "nope")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]
    events = await db_scalar(api, select(AuditEvent).where(AuditEvent.action == "login_failed"))
    assert {e.target for e in events} >= {"frank", "nobody"}
    assert all("nope" not in str(e.details) for e in events)


async def test_lockout_after_repeated_failures(api, settings):
    await create_test_user(api._transport.app, "gina", "viewer", password="Gina-Password-2026")
    async with fresh(api) as c:
        for _ in range(settings.auth_lockout_threshold):
            assert (await sign_in(c, "gina", "bad")).status_code == 401
        locked = await sign_in(c, "gina", "Gina-Password-2026")
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "account_locked"


async def test_session_idle_expiry(api, settings):
    await create_test_user(api._transport.app, "hank", "viewer", password="Hank-Password-2026")
    async with fresh(api) as c:
        assert (await sign_in(c, "hank", "Hank-Password-2026")).status_code == 200
        old = datetime.now(UTC) - timedelta(minutes=settings.auth_session_idle_minutes + 1)
        await db_exec(api, update(UserSession).values(last_seen_at=old))
        expired = await c.get("/api/v1/auth/me")
    assert expired.status_code == 401
    assert "expired" in expired.json()["error"]["message"]


async def test_disabled_user_is_rejected(api):
    headers = await bearer(api, "ivan", "editor")
    assert (await api.get("/api/v1/projects", headers=headers)).status_code == 200
    await db_exec(api, update(User).where(User.username == "ivan").values(disabled=True))
    assert (await api.get("/api/v1/projects", headers=headers)).status_code == 401


# ------------------------------------------------------------------------------- roles
async def test_role_matrix(api):
    viewer = await bearer(api, "vera", "viewer")
    editor = await bearer(api, "eddie", "editor")
    admin = await bearer(api, "ada", "admin")

    assert (await api.get("/api/v1/projects", headers=viewer)).status_code == 200
    denied = await api.post("/api/v1/projects", json={"name": "v"}, headers=viewer)
    assert denied.status_code == 403
    assert "editor role" in denied.json()["error"]["message"]
    assert (
        await api.post("/api/v1/projects", json={"name": "e"}, headers=editor)
    ).status_code == 201

    for headers in (viewer, editor):
        assert (await api.get("/api/v1/users", headers=headers)).status_code == 403
        assert (await api.get("/api/v1/settings/auth", headers=headers)).status_code == 403
        assert (await api.get("/api/v1/audit", headers=headers)).status_code == 403
    assert (await api.get("/api/v1/users", headers=admin)).status_code == 200
    # Everyone may manage their own tokens.
    assert (await api.get("/api/v1/auth/tokens", headers=viewer)).status_code == 200


# -------------------------------------------------------------------------------- tokens
async def test_api_token_lifecycle(api):
    await create_test_user(api._transport.app, "tara", "editor", password="Tara-Password-2026")
    async with fresh(api) as c:
        await sign_in(c, "tara", "Tara-Password-2026")
        created = await c.post(
            "/api/v1/auth/tokens", json={"name": "ci", "expires_in_days": 30}, headers=CSRF
        )
        assert created.status_code == 201
        token = created.json()["token"]
        assert token.startswith("oel_") and created.json()["prefix"] == token[:10]
        listed = (await c.get("/api/v1/auth/tokens")).json()
        assert all("token" not in t for t in listed)
    stored = await db_scalar(api, select(ApiToken).where(ApiToken.name == "ci"))
    assert stored[0].token_hash != token and token not in stored[0].token_hash

    async with fresh(api) as t:
        auth = {"Authorization": f"Bearer {token}"}
        assert (await t.get("/api/v1/projects", headers=auth)).status_code == 200
        # Bearer requests need no CSRF header.
        assert (
            await t.post("/api/v1/projects", json={"name": "tok"}, headers=auth)
        ).status_code == 201
        assert (
            await t.get("/api/v1/projects", headers={"Authorization": "Bearer oel_bogus"})
        ).status_code == 401
        token_id = created.json()["id"]
        assert (await t.delete(f"/api/v1/auth/tokens/{token_id}", headers=auth)).status_code == 204
        assert (await t.get("/api/v1/projects", headers=auth)).status_code == 401


async def test_expired_token_rejected(api):
    headers = await bearer(api, "xena", "viewer")
    await db_exec(api, update(ApiToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    response = await api.get("/api/v1/projects", headers=headers)
    assert response.status_code == 401
    assert "expired" in response.json()["error"]["message"]


# ------------------------------------------------------------------------- user admin
async def test_user_administration(api):
    created = await api.post("/api/v1/users", json={"username": "New.User", "role": "editor"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["username"] == "new.user" and body["must_change_password"] is True
    password = body["initial_password"]
    assert password and len(password) >= 12

    async with fresh(api) as c:
        assert (await sign_in(c, "new.user", password)).status_code == 200
    users = (await api.get("/api/v1/users", params={"q": "new"})).json()["items"]
    assert [u["username"] for u in users] == ["new.user"]
    assert "password_hash" not in users[0]

    uid = body["id"]
    weak = await api.post("/api/v1/users", json={"username": "weak", "password": "short"})
    assert weak.status_code == 422
    dup = await api.post("/api/v1/users", json={"username": "new.user"})
    assert dup.status_code == 409

    reset = await api.post(f"/api/v1/users/{uid}/reset-password")
    assert reset.status_code == 200 and reset.json()["temporary_password"]
    disabled = await api.patch(f"/api/v1/users/{uid}", json={"disabled": True, "role": "viewer"})
    assert disabled.json()["disabled"] is True and disabled.json()["role"] == "viewer"
    async with fresh(api) as c:
        refused = await sign_in(c, "new.user", reset.json()["temporary_password"])
        assert refused.status_code == 403
        assert refused.json()["error"]["code"] == "account_disabled"
    assert (await api.delete(f"/api/v1/users/{uid}")).status_code == 204


async def test_cannot_demote_disable_or_delete_yourself(api):
    me = (await api.get("/api/v1/auth/me")).json()["user"]
    assert (
        await api.patch(f"/api/v1/users/{me['id']}", json={"role": "viewer"})
    ).status_code == 403
    assert (
        await api.patch(f"/api/v1/users/{me['id']}", json={"disabled": True})
    ).status_code == 403
    assert (await api.delete(f"/api/v1/users/{me['id']}")).status_code == 403
    # Another administrator may.
    other = await bearer(api, "admin2", "admin")
    demoted = await api.patch(f"/api/v1/users/{me['id']}", json={"role": "editor"}, headers=other)
    assert demoted.status_code == 200
    assert (await api.get("/api/v1/users")).status_code == 403


# ------------------------------------------------------------------- directory settings
@pytest.fixture
def directory(migrated_app):
    migrated_app.state.ldap_connection_factory = fx.connection_factory(fx.build_server())
    return migrated_app


async def _save_ldap(api, **overrides) -> httpx.Response:
    return await api.put(
        "/api/v1/settings/auth",
        json={"ldap": {**fx.ldap_settings(**overrides), "bind_password": fx.SERVICE_PASSWORD}},
    )


async def test_auth_settings_secrets_are_write_only(directory, api, rendered_logs):
    saved = await _save_ldap(api)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["ldap"]["bind_password_set"] is True
    assert fx.SERVICE_PASSWORD not in saved.text
    assert fx.SERVICE_PASSWORD not in (await api.get("/api/v1/settings/auth")).text
    rows = await db_scalar(api, select(AuthSettings))
    assert (
        rows[0].ldap_bind_password_enc and fx.SERVICE_PASSWORD not in rows[0].ldap_bind_password_enc
    )
    assert fx.SERVICE_PASSWORD not in str(rows[0].ldap)

    # Saving again without a password keeps it.
    again = await api.put(
        "/api/v1/settings/auth", json={"ldap": fx.ldap_settings(timeout_seconds=7)}
    )
    assert again.json()["ldap"]["bind_password_set"] is True
    audit = (await api.get("/api/v1/audit", params={"action": "auth_settings_updated"})).json()[
        "items"
    ]
    assert "timeout_seconds" in audit[0]["details"]["changed"]
    assert fx.SERVICE_PASSWORD not in rendered_logs.getvalue()

    providers = (await api.get("/api/v1/auth/providers")).json()
    assert providers["ldap"] is True


async def test_auth_settings_validation(directory, api):
    no_urls = await api.put(
        "/api/v1/settings/auth", json={"ldap": fx.ldap_settings(server_urls=[])}
    )
    assert no_urls.status_code == 422
    sso_without_keytab = await api.put(
        "/api/v1/settings/auth",
        json={"ldap": fx.ldap_settings(), "kerberos": {"enabled": True}},
    )
    assert sso_without_keytab.status_code == 422


async def test_directory_test_buttons(directory, api):
    await _save_ldap(api)
    ok = (await api.post("/api/v1/settings/auth/test-connection")).json()
    assert ok["ok"] is True
    bad = (
        await api.post(
            "/api/v1/settings/auth/test-connection",
            json={"ldap": {**fx.ldap_settings(), "bind_password": "wrong"}},
        )
    ).json()
    assert bad["ok"] is False and "service account" in bad["message"]

    user = (
        await api.post(
            "/api/v1/settings/auth/test-user",
            json={"username": "CORP\\bob", "password": fx.PASSWORDS["bob"]},
        )
    ).json()
    assert user["ok"] is True and user["details"]["role"] == "editor"
    assert (await db_scalar(api, select(User).where(User.username == "bob"))) == []


async def test_ldap_login_maps_groups_to_roles(directory, api):
    await _save_ldap(api, allowed_groups=["All Staff", "LLM Admins"])
    async with fresh(api) as c:
        alice = await sign_in(c, "alice@corp.example", fx.PASSWORDS["alice"])
        assert alice.status_code == 200, alice.text
        assert alice.json()["role"] == "admin"
        assert alice.json()["user"]["source"] == "ldap"
        assert alice.json()["user"]["display_name"] == "Alice Admin"
    async with fresh(api) as c:
        assert (await sign_in(c, "carol", fx.PASSWORDS["carol"])).json()["role"] == "viewer"
        dave = await sign_in(c, "dave", fx.PASSWORDS["dave"])
        assert dave.status_code == 403 and dave.json()["error"]["code"] == "not_authorized"
        assert (await sign_in(c, "carol", "wrong")).status_code == 401
        # Directory users cannot change their password here.
        await sign_in(c, "carol", fx.PASSWORDS["carol"])
        change = await c.post(
            "/api/v1/auth/change-password",
            json={"current_password": fx.PASSWORDS["carol"], "new_password": "Whatever-Pass-99"},
            headers=CSRF,
        )
        assert change.status_code == 409

    # Mapping is re-evaluated at every sign-in, unless an administrator pinned the role.
    carol = (await db_scalar(api, select(User).where(User.username == "carol")))[0]
    await _save_ldap(api, role_mappings=[{"group": "All Staff", "role": "editor"}])
    async with fresh(api) as c:
        assert (await sign_in(c, "carol", fx.PASSWORDS["carol"])).json()["role"] == "editor"
    pinned = await api.patch(f"/api/v1/users/{carol.id}", json={"role": "viewer"})
    assert pinned.json()["role_pinned"] is True
    async with fresh(api) as c:
        assert (await sign_in(c, "carol", fx.PASSWORDS["carol"])).json()["role"] == "viewer"


async def test_local_account_wins_over_directory(directory, api):
    await _save_ldap(api)
    await create_test_user(api._transport.app, "alice", "viewer", password="Local-Alice-Pass-1")
    async with fresh(api) as c:
        assert (await sign_in(c, "alice", fx.PASSWORDS["alice"])).status_code == 401
        assert (await sign_in(c, "alice", "Local-Alice-Pass-1")).json()["user"]["source"] == "local"


# ----------------------------------------------------------------------------- Kerberos
class FakeAcceptor:
    def __init__(self, principal: str) -> None:
        self.principal = principal

    def accept(self, token: bytes):
        from app.auth.kerberos import KerberosError

        if token != b"good-ticket":
            raise KerberosError("The Kerberos ticket was not accepted (GSSError).")
        return self.principal, b"mutual"


async def test_kerberos_single_sign_on(directory, api):
    directory.state.kerberos_acceptor_factory = lambda cfg, keytab: FakeAcceptor("bob@CORP.EXAMPLE")
    keytab = base64.b64encode(b"\x05\x02fake-keytab").decode()
    saved = await api.put(
        "/api/v1/settings/auth",
        json={
            "ldap": {**fx.ldap_settings(), "bind_password": fx.SERVICE_PASSWORD},
            "kerberos": {
                "enabled": True,
                "service_principal": "HTTP/openllm.corp.example",
                "keytab_base64": keytab,
            },
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["kerberos"]["keytab_set"] is True and keytab not in saved.text
    async with fresh(api) as c:
        assert (await c.get("/api/v1/auth/providers")).json()["kerberos"] is True
        challenge = await c.get("/api/v1/auth/negotiate")
        assert challenge.status_code == 401
        assert challenge.headers["www-authenticate"] == "Negotiate"
        bad = await c.get(
            "/api/v1/auth/negotiate",
            headers={"Authorization": "Negotiate " + base64.b64encode(b"x").decode()},
        )
        assert bad.status_code == 401
        good = await c.get(
            "/api/v1/auth/negotiate",
            headers={"Authorization": "Negotiate " + base64.b64encode(b"good-ticket").decode()},
        )
        assert good.status_code == 200, good.text
        assert good.json()["username"] == "bob" and good.json()["role"] == "editor"
        assert (
            good.headers["www-authenticate"] == "Negotiate " + base64.b64encode(b"mutual").decode()
        )
        assert SESSION_COOKIE in good.headers["set-cookie"]
        assert (await c.get("/api/v1/auth/me")).json()["username"] == "bob"
    sessions = await db_scalar(
        api, select(UserSession).where(UserSession.auth_method == "kerberos")
    )
    assert len(sessions) == 1


# ---------------------------------------------------------------------------- run owner
async def test_runs_record_who_started_them(api, mock_server):
    from tests.integration.test_runs_api import make_dataset, make_endpoint

    endpoint = await make_endpoint(api, mock_server)
    dataset = await make_dataset(api)
    headers = await bearer(api, "runner", "editor")
    response = await api.post(
        "/api/v1/runs",
        json={
            "name": "owned",
            "endpoint_ids": [endpoint["id"]],
            "datasets": [{"dataset_id": dataset["id"]}],
            "load": {"profile": "sequential"},
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    assert response.json()["created_by"] == "runner"


# ------------------------------------------------------------------------- auth disabled
async def test_auth_disabled_treats_everyone_as_admin(
    settings, redis, adapter_context, client_for, database_url
):
    from tests.conftest import build_app

    settings.auth_enabled = False
    app = build_app(settings, redis, adapter_context)
    from alembic import command

    from tests.integration.test_migrations import _alembic_config

    await asyncio.to_thread(command.upgrade, _alembic_config(database_url), "head")
    async for c in client_for(app, auth=False):
        assert (await c.get("/api/v1/projects")).status_code == 200
        # The last active administrator is protected even from the anonymous administrator.
        await create_test_user(app, "only-admin", "admin")
        only = (await c.get("/api/v1/users")).json()["items"][0]
        last = await c.patch(f"/api/v1/users/{only['id']}", json={"disabled": True})
        assert last.status_code == 409 and last.json()["error"]["code"] == "last_admin"
        me = (await c.get("/api/v1/auth/me")).json()
        assert me["auth_enabled"] is False and me["role"] == "admin" and me["user"] is None
        assert (await c.get("/api/v1/health/ready")).json()["auth_enabled"] is False
        assert (await sign_in(c, "admin", "x")).status_code == 409
