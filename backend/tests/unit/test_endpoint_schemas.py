from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.endpoint import EndpointCreate, RequestConfiguration


def test_request_configuration_defaults():
    rc = RequestConfiguration()
    assert rc.stream is True
    assert rc.verify_tls is True
    assert rc.temperature is None  # unset, not 0
    assert rc.max_retries == 1


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer x"},
        {"X-Api-Key": "x"},
        {"x-auth-token": "x"},
        {"Cookie": "a=b"},
    ],
)
def test_sensitive_plain_headers_rejected(headers):
    with pytest.raises(ValidationError, match="secret_headers"):
        RequestConfiguration(headers=headers)


@pytest.mark.parametrize(
    "headers",
    [{"Host": "evil"}, {"Content-Length": "1"}, {"Bad Header": "x"}, {"X-Ok": "a\r\nInjected: 1"}],
)
def test_invalid_headers_rejected(headers):
    with pytest.raises(ValidationError):
        RequestConfiguration(headers=headers)


def test_secret_headers_cannot_override_authorization():
    with pytest.raises(ValidationError, match="secret_reference"):
        RequestConfiguration(secret_headers={"Authorization": "env:OPENLLM_SECRET_X"})


def test_valid_custom_and_secret_headers():
    rc = RequestConfiguration(
        headers={"X-Mock-Mode": "echo"},
        secret_headers={"X-Gateway-Key": "env:OPENLLM_SECRET_GW"},
    )
    assert rc.headers == {"X-Mock-Mode": "echo"}


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        RequestConfiguration(api_key="sk-plaintext")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        EndpointCreate(
            name="x", base_url="http://h/v1", model_identifier="m", api_key="sk-plaintext"
        )  # type: ignore[call-arg]


def test_tags_are_cleaned_and_deduplicated():
    e = EndpointCreate(
        name=" vllm ", base_url="http://h/v1", model_identifier="m", tags=[" gpu ", "gpu", "int8"]
    )
    assert e.name == "vllm"
    assert e.tags == ["gpu", "int8"]


def test_metadata_size_limited():
    with pytest.raises(ValidationError, match="Metadata"):
        EndpointCreate(
            name="x",
            base_url="http://h/v1",
            model_identifier="m",
            deployment_metadata={"blob": "x" * 70_000},
        )


@pytest.mark.parametrize(
    ("name", "sensitive"),
    [
        ("Authorization", True),
        ("X-Api-Key", True),
        ("api_key", True),
        ("X-Auth-Token", True),
        ("X-Access-Token", True),
        ("X-Gateway-Key", True),
        ("Cookie", True),
        ("X-Session-Id", True),
        ("Proxy-Authorization", True),
        ("X-Mock-First-Token-Delay-Ms", False),
        ("X-Max-Tokens-Hint", False),
        ("X-Team", False),
        ("X-Keyboard-Layout", False),
        ("OpenAI-Organization", False),
    ],
)
def test_sensitive_header_detection(name, sensitive):
    from app.schemas.endpoint import is_sensitive_header

    assert is_sensitive_header(name) is sensitive
