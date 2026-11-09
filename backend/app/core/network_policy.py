"""Outbound destination policy for user-supplied model endpoint URLs (SSRF protection, §12.3).

Rules, in order:

1. Only ``http`` / ``https`` URLs with a host and no embedded credentials are accepted.
2. Configured blocked hostnames (this deployment's own services) and well-known cloud metadata
   hostnames are always rejected.
3. Every resolved IP is checked. Unspecified, multicast, reserved, link-local, and cloud metadata
   addresses are always rejected.
4. If an allowlist is configured, the host (by name, wildcard, IP, or CIDR) must match it. A
   matching entry may be private: an explicit allowlist is an explicit decision.
5. Without an allowlist, non-public addresses (RFC 1918, loopback, CGNAT, ULA) are accepted only
   when ``ENDPOINT_ALLOW_PRIVATE_NETWORKS=true``. Private model servers are common, so this is
   configurable rather than blanket-blocked (spec §12.3).

DNS is resolved and re-validated immediately before every request. A residual DNS-rebinding
window remains between this check and the HTTP client's own resolution; it is documented in
SECURITY.md. Redirects are never followed by adapters, so a permitted host cannot bounce a
request to a forbidden one.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.core.config import Settings

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
Resolver = Callable[[str, int], Awaitable[list[str]]]

ALLOWED_SCHEMES = frozenset({"http", "https"})

METADATA_HOSTNAMES = frozenset(
    {"metadata.google.internal", "metadata", "instance-data", "instance-data.ec2.internal"}
)
METADATA_IPS = frozenset(
    ipaddress.ip_address(ip)
    for ip in (
        "169.254.169.254",  # AWS, GCP, Azure, OpenStack, DigitalOcean, ...
        "169.254.170.2",  # AWS ECS task metadata
        "fd00:ec2::254",  # AWS IMDS over IPv6
        "100.100.100.200",  # Alibaba Cloud
    )
)

PRIVATE_HINT = (
    "Private-network endpoints are disabled. Set ENDPOINT_ALLOW_PRIVATE_NETWORKS=true "
    "or add the host to ENDPOINT_ALLOWED_HOSTS."
)


class DestinationBlockedError(Exception):
    """The URL or one of its resolved addresses violates the network policy."""


class DestinationUnresolvableError(Exception):
    """The hostname could not be resolved."""


async def default_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def _parse_ip(value: str) -> IPAddress | None:
    try:
        ip = ipaddress.ip_address(value.strip("[]").split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _host_matches(host: str, pattern: str) -> bool:
    if pattern.startswith("*."):
        return host.endswith(pattern[1:]) and host != pattern[2:]
    return host == pattern


@dataclass(frozen=True)
class ParsedDestination:
    scheme: str
    host: str
    port: int


@dataclass(frozen=True)
class NetworkPolicy:
    allow_private: bool = False
    allowed_hostnames: tuple[str, ...] = ()
    allowed_networks: tuple[IPNetwork, ...] = ()
    blocked_hostnames: tuple[str, ...] = ()
    resolver: Resolver = field(default=default_resolver, compare=False)

    @classmethod
    def from_settings(cls, settings: Settings, resolver: Resolver | None = None) -> NetworkPolicy:
        hostnames: list[str] = []
        networks: list[IPNetwork] = []
        for entry in settings.endpoint_allowed_hosts:
            entry = entry.strip().lower()
            try:
                networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                hostnames.append(entry)
        return cls(
            allow_private=settings.endpoint_allow_private_networks,
            allowed_hostnames=tuple(hostnames),
            allowed_networks=tuple(networks),
            blocked_hostnames=tuple(h.strip().lower() for h in settings.endpoint_blocked_hosts),
            resolver=resolver or default_resolver,
        )

    @property
    def has_allowlist(self) -> bool:
        return bool(self.allowed_hostnames or self.allowed_networks)

    def describe(self) -> dict[str, object]:
        return {
            "allow_private_networks": self.allow_private,
            "allowlist": [*self.allowed_hostnames, *map(str, self.allowed_networks)],
            "blocked_hosts": list(self.blocked_hostnames),
        }

    # ---- static URL validation (no network access) ----
    def validate_url(self, url: str) -> ParsedDestination:
        try:
            parts = urlsplit(url.strip())
            port = parts.port
        except ValueError as exc:
            raise DestinationBlockedError(f"Invalid URL: {exc}") from None
        scheme = parts.scheme.lower()
        if scheme not in ALLOWED_SCHEMES:
            raise DestinationBlockedError("Only http and https URLs are allowed.")
        if parts.username is not None or parts.password is not None:
            raise DestinationBlockedError(
                "Credentials must not be embedded in the URL; use a secret reference instead."
            )
        host = (parts.hostname or "").rstrip(".").lower()
        if not host:
            raise DestinationBlockedError("URL has no host.")
        if any(_host_matches(host, blocked) for blocked in self.blocked_hostnames):
            raise DestinationBlockedError(f"Host '{host}' is blocked by ENDPOINT_BLOCKED_HOSTS.")
        if host in METADATA_HOSTNAMES:
            raise DestinationBlockedError("Cloud metadata endpoints are not allowed.")
        destination = ParsedDestination(
            scheme=scheme, host=host, port=port or (443 if scheme == "https" else 80)
        )
        literal = _parse_ip(host)
        if literal is not None:
            self.check_ip(literal, host_allowlisted=False)
        return destination

    def _hostname_allowlisted(self, host: str) -> bool:
        return any(_host_matches(host, pattern) for pattern in self.allowed_hostnames)

    def check_ip(self, ip: IPAddress, *, host_allowlisted: bool) -> None:
        if ip in METADATA_IPS or ip.is_link_local:
            raise DestinationBlockedError(
                f"Address {ip} is link-local or a cloud metadata service and is never allowed."
            )
        # Note: Python marks ::1 as reserved (::/8); loopback is handled as private below.
        if ip.is_unspecified or ip.is_multicast or (ip.is_reserved and not ip.is_loopback):
            raise DestinationBlockedError(f"Address {ip} is not a valid endpoint destination.")
        if self.has_allowlist:
            if host_allowlisted or any(ip in net for net in self.allowed_networks):
                return
            raise DestinationBlockedError(f"Address {ip} is not in ENDPOINT_ALLOWED_HOSTS.")
        if not ip.is_global and not self.allow_private:
            raise DestinationBlockedError(f"Address {ip} is on a private network. {PRIVATE_HINT}")

    # ---- full check with DNS resolution (call immediately before each request) ----
    async def check_destination(self, url: str) -> list[IPAddress]:
        destination = self.validate_url(url)
        literal = _parse_ip(destination.host)
        if literal is not None:
            addresses: Iterable[IPAddress] = [literal]
        else:
            try:
                resolved = await self.resolver(destination.host, destination.port)
            except (OSError, UnicodeError) as exc:
                raise DestinationUnresolvableError(
                    f"Could not resolve host '{destination.host}': {exc}"
                ) from None
            addresses = [ip for ip in (_parse_ip(a) for a in resolved) if ip is not None]
            if not addresses:
                raise DestinationUnresolvableError(
                    f"Host '{destination.host}' resolved to no addresses."
                )
        allowlisted = self._hostname_allowlisted(destination.host)
        result = list(addresses)
        for ip in result:  # every address must pass: no partial bypass via multi-record DNS
            self.check_ip(ip, host_allowlisted=allowlisted)
        return result
