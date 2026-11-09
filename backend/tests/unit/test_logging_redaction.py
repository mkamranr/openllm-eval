from __future__ import annotations

import json
import logging

from app.core.logging import (
    REDACTED,
    configure_logging,
    get_logger,
    redact_text,
    redact_value,
)


def test_sensitive_keys_redacted_recursively():
    event = {
        "event": "call",
        "headers": {"Authorization": "Bearer abc", "X-Api-Key": "k", "Accept": "json"},
        "config": [{"password": "p", "name": "ok"}],
        "access_token": "t",
    }
    out = redact_value(event)
    assert out["headers"]["Authorization"] == REDACTED
    assert out["headers"]["X-Api-Key"] == REDACTED
    assert out["headers"]["Accept"] == "json"
    assert out["config"][0] == {"password": REDACTED, "name": "ok"}
    assert out["access_token"] == REDACTED


def test_url_credentials_and_bearer_tokens_redacted_in_text():
    text = "connect postgresql://openllm:s3cret@db:5432/x failed; header Bearer sk-abc.123"
    out = redact_text(text)
    assert "s3cret" not in out
    assert "sk-abc.123" not in out
    assert "postgresql://openllm:" in out


def test_log_output_is_json_and_redacted(capsys):
    configure_logging("INFO", "json")
    get_logger("test").info("endpoint_call", api_key="sk-live-xyz", url="https://u:pw@h/v1")
    logging.getLogger("stdlib").warning("dsn redis://:hunter2@redis:6379/0")

    lines = [json.loads(line) for line in capsys.readouterr().err.strip().splitlines()]
    structured, stdlib = lines[-2], lines[-1]
    assert structured["event"] == "endpoint_call"
    assert structured["api_key"] == REDACTED
    assert "pw" not in structured["url"]
    assert structured["level"] == "info"
    assert "timestamp" in structured
    assert "hunter2" not in stdlib["event"]
    assert stdlib["logger"] == "stdlib"
