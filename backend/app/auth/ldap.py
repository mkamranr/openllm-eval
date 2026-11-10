"""Active Directory / LDAP sign-in: service-account search, user bind, groups, role mapping.

ldap3 is synchronous; callers run these methods in a worker thread (``asyncio.to_thread``).
Passwords are never logged or included in error messages.
"""

from __future__ import annotations

import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import ldap3
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import parse_dn

from app.db.models.auth import ROLE_RANK
from app.schemas.auth import LdapSettings

MAX_NESTED_DEPTH = 8
MAX_GROUPS = 1000


class DirectoryError(Exception):
    """The directory could not be used (unreachable, TLS, service bind, ambiguous user)."""


class InvalidCredentialsError(Exception):
    """Unknown user or wrong password (deliberately indistinguishable)."""


@dataclass
class DirectoryUser:
    dn: str
    username: str
    display_name: str | None
    email: str | None
    groups: list[str] = field(default_factory=list)


ConnectionFactory = Callable[[str, LdapSettings, str | None, str | None], ldap3.Connection]


def normalize_username(raw: str) -> str:
    """``CORP\\alice``, ``alice@corp.example.com`` and ``Alice`` all become ``alice``."""
    value = raw.strip()
    if "\\" in value:
        value = value.split("\\", 1)[1]
    if "@" in value:
        value = value.split("@", 1)[0]
    return value.strip().lower()


def group_cn(group: str) -> str:
    try:
        parts = parse_dn(group)
    except Exception:
        return group
    return parts[0][1] if parts else group


def _group_matches(entry: str, group: str) -> bool:
    entry_l = entry.strip().lower()
    return entry_l == group.lower() or entry_l == group_cn(group).lower()


def map_role(groups: list[str], cfg: LdapSettings) -> str | None:
    """The role for a directory user with these groups, or None when they may not sign in.

    Allowed groups (if any) gate access; the highest mapped role wins; otherwise the default.
    """
    if cfg.allowed_groups and not any(
        _group_matches(a, g) for a in cfg.allowed_groups for g in groups
    ):
        return None
    best: str | None = None
    for mapping in cfg.role_mappings:
        if any(_group_matches(mapping.group, g) for g in groups) and (
            best is None or ROLE_RANK[mapping.role] > ROLE_RANK[best]
        ):
            best = mapping.role
    if best is not None:
        return best
    return None if cfg.default_role == "deny" else cfg.default_role


def default_connection(
    url: str, cfg: LdapSettings, user: str | None, password: str | None
) -> ldap3.Connection:
    use_ssl = url.lower().startswith("ldaps://")
    tls = None
    if use_ssl or cfg.start_tls:
        tls = ldap3.Tls(
            validate=ssl.CERT_REQUIRED if cfg.verify_tls else ssl.CERT_NONE,
            ca_certs_data=cfg.ca_cert_pem,
            version=ssl.PROTOCOL_TLS_CLIENT,
        )
    server = ldap3.Server(
        url, use_ssl=use_ssl, tls=tls, connect_timeout=cfg.timeout_seconds, get_info=ldap3.NONE
    )
    return ldap3.Connection(
        server,
        user=user,
        password=password,
        auto_bind=ldap3.AUTO_BIND_NONE,
        receive_timeout=cfg.timeout_seconds,
        raise_exceptions=False,
        read_only=True,
        auto_referrals=False,
    )


def _values(attributes: dict[str, Any], name: str) -> list[str]:
    for key, value in attributes.items():
        if key.lower() == name.lower():
            if value is None:
                return []
            items = value if isinstance(value, list | tuple) else [value]
            return [str(v) for v in items if v not in (None, "")]
    return []


def _first(attributes: dict[str, Any], name: str) -> str | None:
    values = _values(attributes, name)
    return values[0] if values else None


class LdapDirectory:
    def __init__(
        self,
        cfg: LdapSettings,
        bind_password: str | None,
        *,
        blocked_hosts: tuple[str, ...] = (),
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        self.cfg = cfg
        self.bind_password = bind_password
        self.blocked_hosts = {h.lower() for h in blocked_hosts}
        self.factory = connection_factory or default_connection

    # ------------------------------------------------------------------ connections
    def _check_host(self, url: str) -> None:
        host = (urlsplit(url).hostname or "").lower()
        if not host:
            raise DirectoryError(f"Invalid directory URL: {url}")
        if host in self.blocked_hosts:
            raise DirectoryError(f"The directory host {host!r} is blocked by the network policy.")

    def _bind(self, user: str | None, password: str | None) -> ldap3.Connection | None:
        """A bound connection, None if the credentials were rejected; DirectoryError if no
        server could be used."""
        if not self.cfg.server_urls:
            raise DirectoryError("No directory server is configured.")
        last_error = "no server reachable"
        for url in self.cfg.server_urls:
            self._check_host(url)
            conn = self.factory(url, self.cfg, user, password)
            try:
                conn.open()
                if conn.closed:
                    last_error = f"{url}: connection failed"
                    continue
                if self.cfg.start_tls and not url.lower().startswith("ldaps://"):
                    if not conn.start_tls():
                        raise DirectoryError(f"{url}: StartTLS failed")
                if conn.bind():
                    return conn
                result = conn.result or {}
                # 49 = invalidCredentials: the server answered; do not try the next one.
                if result.get("result") == 49:
                    conn.unbind()
                    return None
                last_error = f"{url}: bind failed ({result.get('description', 'unknown')})"
                conn.unbind()
            except DirectoryError:
                raise
            except LDAPException as exc:
                last_error = f"{url}: {type(exc).__name__}"
            except OSError as exc:
                last_error = f"{url}: {type(exc).__name__}"
        raise DirectoryError(f"The directory could not be reached ({last_error}).")

    def _service(self) -> ldap3.Connection:
        conn = self._bind(self.cfg.bind_dn, self.bind_password if self.cfg.bind_dn else None)
        if conn is None:
            raise DirectoryError(
                "The service account bind was rejected; check the bind DN and password."
            )
        return conn

    # ---------------------------------------------------------------------- lookups
    def _search_user(self, conn: ldap3.Connection, username: str) -> DirectoryUser:
        cfg = self.cfg
        query = cfg.user_filter.replace("{username}", escape_filter_chars(username))
        attributes = [
            cfg.username_attribute,
            cfg.display_name_attribute,
            cfg.email_attribute,
            cfg.group_attribute,
        ]
        ok = conn.search(
            cfg.user_search_base,
            query,
            search_scope=ldap3.SUBTREE,
            attributes=attributes,
            size_limit=2,
        )
        entries = [r for r in (conn.response or []) if r.get("type") == "searchResEntry"]
        if not ok and not entries:
            code = (conn.result or {}).get("result")
            if code not in (0, 32):  # 32 = noSuchObject
                raise DirectoryError(
                    f"The user search failed ({(conn.result or {}).get('description')})."
                )
        if not entries:
            raise InvalidCredentialsError
        if len(entries) > 1:
            raise DirectoryError("The user filter matched more than one directory entry.")
        entry = entries[0]
        attrs = entry.get("attributes") or {}
        groups = _values(attrs, cfg.group_attribute)
        if cfg.nested_groups:
            groups = self._expand_groups(conn, groups)
        return DirectoryUser(
            dn=entry["dn"],
            username=normalize_username(_first(attrs, cfg.username_attribute) or username),
            display_name=_first(attrs, cfg.display_name_attribute),
            email=_first(attrs, cfg.email_attribute),
            groups=groups,
        )

    def _expand_groups(self, conn: ldap3.Connection, groups: list[str]) -> list[str]:
        seen = {g.lower(): g for g in groups}
        frontier = list(groups)
        for _ in range(MAX_NESTED_DEPTH):
            next_frontier: list[str] = []
            for group in frontier:
                if len(seen) >= MAX_GROUPS:
                    return list(seen.values())
                conn.search(
                    group,
                    "(objectClass=*)",
                    search_scope=ldap3.BASE,
                    attributes=[self.cfg.group_attribute],
                )
                for r in conn.response or []:
                    if r.get("type") != "searchResEntry":
                        continue
                    for parent in _values(r.get("attributes") or {}, self.cfg.group_attribute):
                        if parent.lower() not in seen:
                            seen[parent.lower()] = parent
                            next_frontier.append(parent)
            if not next_frontier:
                break
            frontier = next_frontier
        return list(seen.values())

    # ----------------------------------------------------------------------- public
    def authenticate(self, username: str, password: str) -> DirectoryUser:
        # An empty password would be an unauthenticated bind, which many servers accept.
        if not password or not username:
            raise InvalidCredentialsError
        service = self._service()
        try:
            user = self._search_user(service, username)
        finally:
            service.unbind()
        conn = self._bind(user.dn, password)
        if conn is None:
            raise InvalidCredentialsError
        conn.unbind()
        return user

    def lookup(self, username: str) -> DirectoryUser:
        """Find a user without their password (Kerberos sign-in: the ticket proved identity)."""
        service = self._service()
        try:
            return self._search_user(service, username)
        finally:
            service.unbind()

    def test_connection(self) -> dict[str, Any]:
        service = self._service()
        try:
            found = service.search(
                self.cfg.user_search_base,
                "(objectClass=*)",
                search_scope=ldap3.BASE,
                attributes=[],
            )
            if not found:
                raise DirectoryError(
                    f"Connected and bound, but the search base was not found "
                    f"({(service.result or {}).get('description')})."
                )
        finally:
            service.unbind()
        return {
            "bind": "anonymous" if not self.cfg.bind_dn else "service account",
            "search_base": self.cfg.user_search_base,
        }
