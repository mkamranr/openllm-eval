from __future__ import annotations

import pytest

from app.core.secrets import SecretReferenceError, SecretResolver, parse_secret_reference

PREFIX = "OPENLLM_SECRET_"


def test_parse_valid_reference():
    assert parse_secret_reference("env:OPENLLM_SECRET_VLLM", PREFIX) == "OPENLLM_SECRET_VLLM"


@pytest.mark.parametrize(
    "ref",
    [
        "OPENLLM_SECRET_VLLM",  # missing scheme
        "env:openllm_secret_vllm",  # lowercase
        "vault:OPENLLM_SECRET_X",  # unsupported scheme
        "env:POSTGRES_PASSWORD",  # outside the allowed prefix
        "env:DATABASE_URL",
        "env:OPENLLM_SECRET_",  # prefix only
        "env:OPENLLM_SECRET_X; rm -rf /",
    ],
)
def test_rejects_invalid_or_out_of_scope_references(ref):
    with pytest.raises(SecretReferenceError):
        parse_secret_reference(ref, PREFIX)


def test_resolver_reads_only_prefixed_environment():
    environ = {"OPENLLM_SECRET_A": "value-a", "OPENLLM_SECRET_EMPTY": "", "POSTGRES_PASSWORD": "x"}
    resolver = SecretResolver(PREFIX, environ)
    assert resolver.resolve("env:OPENLLM_SECRET_A") == "value-a"
    assert resolver.resolve("env:OPENLLM_SECRET_MISSING") is None
    assert resolver.resolve("env:OPENLLM_SECRET_EMPTY") is None  # empty counts as unset
    assert resolver.is_available("env:OPENLLM_SECRET_A")
    assert not resolver.is_available("env:POSTGRES_PASSWORD")
    with pytest.raises(SecretReferenceError):
        resolver.resolve("env:POSTGRES_PASSWORD")
