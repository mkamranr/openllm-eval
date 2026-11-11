"""Auth building blocks: passwords, tokens, encryption, lockout, role mapping, LDAP client."""

from __future__ import annotations

import fakeredis
import pytest

from app.auth import passwords
from app.auth.crypto import SecretBox, SecretKeyMissingError
from app.auth.kerberos import principal_username
from app.auth.ldap import (
    DirectoryError,
    InvalidCredentialsError,
    LdapDirectory,
    map_role,
    normalize_username,
)
from app.auth.lockout import Lockout
from app.auth.tokens import hash_token, new_api_token, new_session_token
from app.core.config import Settings
from app.core.exceptions import AppError
from app.core.logging import redact_text
from app.schemas.auth import LdapSettings
from tests import ldap_fixtures as fx


# ----------------------------------------------------------------------------- passwords
def test_password_hash_and_verify():
    h = passwords.hash_password("Correct-Horse-9")
    assert h.startswith("$argon2id$")
    assert passwords.verify_password(h, "Correct-Horse-9")
    assert not passwords.verify_password(h, "wrong")
    assert not passwords.verify_password(None, "anything")
    assert not passwords.verify_password("not-a-hash", "anything")


@pytest.mark.parametrize(
    ("password", "fragment"),
    [
        ("short1!", "at least 12 characters"),
        ("aaaaaaaaaaaaaa", "5 different characters"),
        ("my-admin-password", "must not contain the username"),
        (" leading-space-pass", "no leading or trailing spaces"),
        ("Initial-Pass-123", "must differ"),
    ],
)
def test_password_policy_rejects(password, fragment):
    with pytest.raises(AppError) as err:
        passwords.check_policy(password, username="admin", forbidden=("Initial-Pass-123",))
    assert err.value.code == "weak_password"
    assert fragment in err.value.message
    assert password not in err.value.message


def test_password_policy_accepts():
    passwords.check_policy("Blue-Kettle-Morning-7", username="admin")


# -------------------------------------------------------------------------------- tokens
def test_tokens_are_random_and_hashed():
    a, b = new_api_token(), new_api_token()
    assert a.startswith("oel_") and a != b and len(a) > 40
    assert len(hash_token(a)) == 64 and hash_token(a) != a
    assert new_session_token() != new_session_token()


def test_api_tokens_redacted_from_logs():
    token = new_api_token()
    text = redact_text(f"failed with {token} and Authorization: Bearer {token}")
    assert token not in text
    assert "Negotiate abc" not in redact_text("WWW-Authenticate: Negotiate YIIabc123==")


# ---------------------------------------------------------------------------- encryption
def test_secret_box_round_trip_and_tamper():
    box = SecretBox("a-long-enough-secret-key")
    enc = box.encrypt(b"bind-password", purpose="ldap_bind_password")
    assert b"bind-password" not in enc.encode()
    assert box.decrypt(enc, purpose="ldap_bind_password") == b"bind-password"
    with pytest.raises(AppError):  # bound to its purpose
        box.decrypt(enc, purpose="kerberos_keytab")
    with pytest.raises(AppError):  # another key cannot read it
        SecretBox("a-different-secret-key-x").decrypt(enc, purpose="ldap_bind_password")


def test_secret_box_requires_key():
    box = SecretBox(None)
    assert not box.available
    with pytest.raises(SecretKeyMissingError):
        box.encrypt(b"x", purpose="p")
    assert not SecretBox("short").available


# ----------------------------------------------------------------------------- usernames
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("CORP\\Alice", "alice"), ("alice@corp.example.com", "alice"), ("  Bob ", "bob")],
)
def test_normalize_username(raw, expected):
    assert normalize_username(raw) == expected


def test_principal_username():
    assert principal_username("Alice@CORP.EXAMPLE.COM") == "alice"


# --------------------------------------------------------------------------- role mapping
def _cfg(**kw) -> LdapSettings:
    return LdapSettings.model_validate(fx.ldap_settings(**kw))


def test_map_role_highest_wins_and_cn_match():
    cfg = _cfg()
    assert map_role([fx.ADMINS, fx.EDITORS], cfg) == "admin"
    assert map_role([fx.EDITORS], cfg) == "editor"  # mapping by CN only
    assert map_role([fx.ALL_STAFF], cfg) == "viewer"  # default role


def test_map_role_allowed_groups_and_deny_default():
    cfg = _cfg(allowed_groups=["All Staff"], default_role="deny")
    assert map_role([fx.CONTRACTORS], cfg) is None  # not allowed
    assert map_role([fx.ALL_STAFF], cfg) is None  # allowed but unmapped and default deny
    assert map_role([fx.ALL_STAFF, fx.EDITORS], cfg) == "editor"


def test_ldap_settings_validation():
    with pytest.raises(ValueError, match="ldap://"):
        LdapSettings(server_urls=["http://dc"])
    with pytest.raises(ValueError, match="username"):
        LdapSettings(user_filter="(sAMAccountName=x)")
    with pytest.raises(ValueError, match="credentials"):
        LdapSettings(server_urls=["ldap://user:pw@dc"])


# ------------------------------------------------------------------------------- lockout
async def test_lockout_after_threshold():
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    settings = Settings(_env_file=None, auth_lockout_threshold=3, auth_lockout_ip_threshold=10)
    lock = Lockout(redis, settings)
    for _ in range(2):
        await lock.record_failure("alice", "10.0.0.1")
    assert await lock.locked_for("alice", "10.0.0.1") == 0
    await lock.record_failure("alice", "10.0.0.1")
    assert await lock.locked_for("alice", "10.0.0.2") > 0  # the username is locked everywhere
    assert await lock.locked_for("bob", "10.0.0.1") == 0  # the address is not (yet)
    await lock.unlock(username="alice")
    assert await lock.locked_for("alice", None) == 0


async def test_lockout_fails_open_without_redis():
    class Broken:
        async def ttl(self, *_):
            raise ConnectionError

        async def incr(self, *_):
            raise ConnectionError

    lock = Lockout(Broken(), Settings(_env_file=None))  # type: ignore[arg-type]
    assert await lock.locked_for("alice", "1.2.3.4") == 0
    await lock.record_failure("alice", "1.2.3.4")  # logged, not raised


# ---------------------------------------------------------------------------- LDAP client
@pytest.fixture(scope="module")
def mock_server():
    return fx.build_server()


def _directory(server, password=fx.SERVICE_PASSWORD, **kw) -> LdapDirectory:
    return LdapDirectory(_cfg(**kw), password, connection_factory=fx.connection_factory(server))


def test_ldap_authenticate_returns_groups(mock_server):
    user = _directory(mock_server).authenticate("alice", fx.PASSWORDS["alice"])
    assert user.username == "alice"
    assert user.display_name == "Alice Admin"
    assert user.email == "alice@corp.example"
    assert fx.ADMINS in user.groups


def test_ldap_wrong_or_empty_password(mock_server):
    directory = _directory(mock_server)
    with pytest.raises(InvalidCredentialsError):
        directory.authenticate("alice", "wrong")
    with pytest.raises(InvalidCredentialsError):
        directory.authenticate("alice", "")  # never an unauthenticated bind
    with pytest.raises(InvalidCredentialsError):
        directory.authenticate("nobody", "whatever")


def test_ldap_filter_injection_is_escaped(mock_server):
    with pytest.raises(InvalidCredentialsError):
        _directory(mock_server).authenticate("*", "whatever")


def test_ldap_nested_groups(mock_server):
    flat = _directory(mock_server).lookup("bob")
    assert fx.NESTED_PARENT not in flat.groups
    nested = _directory(mock_server, nested_groups=True).lookup("bob")
    assert fx.NESTED_PARENT in nested.groups


def test_ldap_bad_service_password(mock_server):
    with pytest.raises(DirectoryError, match="service account"):
        _directory(mock_server, password="wrong").authenticate("alice", fx.PASSWORDS["alice"])


def test_ldap_test_connection(mock_server):
    assert _directory(mock_server).test_connection()["bind"] == "service account"
    with pytest.raises(DirectoryError, match="search base"):
        _directory(mock_server, user_search_base="OU=Missing,DC=corp,DC=example").test_connection()


def test_ldap_blocked_host(mock_server):
    directory = LdapDirectory(
        _cfg(server_urls=["ldap://postgres:389"]),
        fx.SERVICE_PASSWORD,
        blocked_hosts=("postgres", "redis"),
        connection_factory=fx.connection_factory(mock_server),
    )
    with pytest.raises(DirectoryError, match="blocked"):
        directory.test_connection()


def test_ldap_receive_timeout_is_whole_seconds():
    # ldap3 on Linux packs SO_RCVTIMEO with struct.pack("LL", ...), which rejects floats.
    import struct

    from app.auth.ldap import default_connection

    conn = default_connection("ldap://dc1.corp.example", _cfg(timeout_seconds=2.5), None, None)
    assert conn.receive_timeout == 3 and isinstance(conn.receive_timeout, int)
    struct.pack("LL", conn.receive_timeout, 0)


def test_ldap_unreachable_reports_socket_reason():
    from ldap3.core.exceptions import LDAPSocketOpenError

    class Unreachable:
        closed = True

        def open(self):
            raise LDAPSocketOpenError(
                "socket connection error while opening: [Errno -2] Name or service not known"
            )

    directory = LdapDirectory(
        _cfg(server_urls=["ldap://dc1.corp.example", "ldap://dc2.corp.example"]),
        fx.SERVICE_PASSWORD,
        connection_factory=lambda *_: Unreachable(),
    )
    with pytest.raises(DirectoryError) as info:
        directory.test_connection()
    message = str(info.value)
    assert "dc2.corp.example: LDAPSocketOpenError" in message
    assert "Name or service not known" in message
    assert fx.SERVICE_PASSWORD not in message


# ----------------------------------------------------------------------- CSRF origin check
@pytest.mark.parametrize(
    ("origin", "headers", "allowed"),
    [
        (None, {"host": "srv"}, True),
        ("http://srv:3000", {"host": "srv:3000"}, True),
        # nginx "Host $host" drops the port: still the same site.
        ("http://srv:3000", {"host": "srv"}, True),
        ("http://srv:3000", {"host": "api:8000", "x-forwarded-host": "srv:3000"}, True),
        ("https://llm.corp.example", {"host": "llm.corp.example"}, True),
        ("http://[::1]:3000", {"host": "[::1]:3000"}, True),
        ("http://srv:4000", {"host": "srv:3000"}, False),
        ("https://evil.example", {"host": "srv"}, False),
        ("http://srv.evil.example", {"host": "srv"}, False),
        ("null", {"host": "srv"}, False),
    ],
)
def test_origin_check(origin, headers, allowed):
    from starlette.requests import Request

    from app.auth.dependencies import _origin_allowed
    from app.core.config import Settings

    raw = [(k.encode(), v.encode()) for k, v in headers.items()]
    if origin is not None:
        raw.append((b"origin", origin.encode()))
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": raw})
    assert _origin_allowed(request, Settings()) is allowed
