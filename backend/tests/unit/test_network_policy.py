from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.network_policy import (
    DestinationBlockedError,
    DestinationUnresolvableError,
    NetworkPolicy,
)


def policy(**overrides) -> NetworkPolicy:
    settings = Settings(_env_file=None, **overrides)

    async def resolver(host: str, port: int) -> list[str]:
        table = {
            "public.example": ["93.184.216.34"],
            "lan-vllm.internal": ["10.0.0.5"],
            "rebind.example": ["93.184.216.34", "169.254.169.254"],
            "v6.example": ["2606:2800:220:1::1"],
            "mapped.example": ["::ffff:169.254.169.254"],
        }
        if host not in table:
            raise OSError("Name or service not known")
        return table[host]

    return NetworkPolicy.from_settings(settings, resolver=resolver)


@pytest.mark.parametrize(
    ("url", "fragment"),
    [
        ("ftp://example.com/v1", "Only http and https"),
        ("file:///etc/passwd", "Only http and https"),
        ("http://user:pw@example.com/v1", "Credentials must not be embedded"),
        ("http:///v1", "no host"),
        ("http://postgres:5432", "ENDPOINT_BLOCKED_HOSTS"),
        ("http://REDIS:6379", "ENDPOINT_BLOCKED_HOSTS"),
        ("http://metadata.google.internal/computeMetadata/v1", "metadata"),
        ("http://169.254.169.254/latest/meta-data", "metadata"),
        ("http://[fd00:ec2::254]/", "metadata"),
        ("http://0.0.0.0:8000/v1", "not a valid endpoint"),
        ("http://224.0.0.1/v1", "not a valid endpoint"),
        ("http://example.com:99999/v1", "Invalid URL"),
    ],
)
def test_static_rejections(url, fragment):
    with pytest.raises(DestinationBlockedError, match=fragment):
        policy(endpoint_allow_private_networks=True).validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://10.1.2.3:8000/v1",
        "http://127.0.0.1:8000/v1",
        "http://[::1]:8000/v1",
        "http://192.168.1.20/v1",
        "http://100.64.0.1/v1",
    ],
)
def test_private_literals_need_opt_in(url):
    with pytest.raises(DestinationBlockedError, match="ENDPOINT_ALLOW_PRIVATE_NETWORKS"):
        policy().validate_url(url)
    policy(endpoint_allow_private_networks=True).validate_url(url)  # no raise


def test_public_hosts_allowed_by_default():
    p = policy()
    dest = p.validate_url("https://api.example.com/v1")
    assert (dest.scheme, dest.host, dest.port) == ("https", "api.example.com", 443)


async def test_dns_resolution_is_checked():
    p = policy()
    assert [str(ip) for ip in await p.check_destination("http://public.example/v1")] == [
        "93.184.216.34"
    ]
    with pytest.raises(DestinationBlockedError, match="private network"):
        await p.check_destination("http://lan-vllm.internal:8000/v1")
    await policy(endpoint_allow_private_networks=True).check_destination(
        "http://lan-vllm.internal:8000/v1"
    )


async def test_any_bad_record_blocks_multi_record_dns():
    with pytest.raises(DestinationBlockedError, match="metadata"):
        await policy(endpoint_allow_private_networks=True).check_destination(
            "http://rebind.example/v1"
        )


async def test_ipv4_mapped_ipv6_is_unwrapped():
    with pytest.raises(DestinationBlockedError, match="metadata"):
        await policy(endpoint_allow_private_networks=True).check_destination(
            "http://mapped.example/"
        )


async def test_unresolvable_host():
    with pytest.raises(DestinationUnresolvableError, match="Could not resolve"):
        await policy().check_destination("http://nope.invalid/v1")


async def test_allowlist_is_exclusive_and_permits_listed_private_hosts():
    p = policy(endpoint_allowed_hosts="lan-vllm.internal,*.corp.example,192.168.50.0/24")
    await p.check_destination("http://lan-vllm.internal:8000/v1")  # private but listed
    p.validate_url("http://192.168.50.7/v1")
    with pytest.raises(DestinationBlockedError, match="ENDPOINT_ALLOWED_HOSTS"):
        await p.check_destination("http://public.example/v1")
    with pytest.raises(DestinationBlockedError, match="ENDPOINT_ALLOWED_HOSTS"):
        p.validate_url("http://192.168.51.7/v1")


async def test_allowlist_never_permits_metadata():
    p = policy(endpoint_allowed_hosts="169.254.0.0/16")
    with pytest.raises(DestinationBlockedError, match="never allowed"):
        p.validate_url("http://169.254.169.254/")


def test_describe_exposes_policy_without_secrets():
    d = policy(endpoint_allow_private_networks=True).describe()
    assert d == {
        "allow_private_networks": True,
        "allowlist": [],
        "blocked_hosts": ["postgres", "redis"],
    }
