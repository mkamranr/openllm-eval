from __future__ import annotations

import httpx
from typer.testing import CliRunner

from app.cli import main as cli

runner = CliRunner()

ENDPOINT = {
    "id": "6b1f7a52-8a0e-4d55-9a57-3c7c1f0f7b11",
    "name": "local-vllm",
    "enabled": True,
    "last_test_status": None,
    "model_identifier": "qwen",
    "base_url": "http://vllm:8000/v1",
}


def patch_api(monkeypatch, routes):
    calls = []

    def fake_request(method, url, timeout, **kwargs):
        calls.append((method, url, kwargs))
        for (m, suffix), (status, body) in routes.items():
            if m == method and url.endswith(suffix):
                return httpx.Response(status, json=body)
        return httpx.Response(404, json={"error": {"message": "no route"}})

    monkeypatch.setattr(cli.httpx, "request", fake_request)
    return calls


def test_endpoint_list(monkeypatch):
    patch_api(monkeypatch, {("GET", "/api/v1/endpoints"): (200, {"items": [ENDPOINT]})})
    result = runner.invoke(cli.app, ["endpoint", "list"])
    assert result.exit_code == 0, result.output
    assert "local-vllm" in result.output and "http://vllm:8000/v1" in result.output


def test_endpoint_list_empty(monkeypatch):
    patch_api(monkeypatch, {("GET", "/api/v1/endpoints"): (200, {"items": []})})
    result = runner.invoke(cli.app, ["endpoint", "list"])
    assert result.exit_code == 0 and "No endpoints" in result.output


def test_endpoint_test_by_name(monkeypatch):
    test_result = {
        "ok": True,
        "models_check": {"ok": True, "model_listed": True},
        "response": {
            "content": "ok",
            "error": None,
            "timing": {"latency_ms": 120.5, "ttft_ms": 40.1},
            "usage": {"input_tokens": 5, "output_tokens": 1, "source": "server_reported"},
        },
    }
    calls = patch_api(
        monkeypatch,
        {
            ("GET", "/api/v1/endpoints"): (200, {"items": [ENDPOINT]}),
            ("POST", f"/api/v1/endpoints/{ENDPOINT['id']}/test"): (200, test_result),
        },
    )
    result = runner.invoke(cli.app, ["endpoint", "test", "local-vllm", "--no-stream"])
    assert result.exit_code == 0, result.output
    assert calls[0][2]["params"] == {"name": "local-vllm"}
    assert calls[1][2]["json"]["stream"] is False
    assert "TTFT 40.1 ms" in result.output
    assert "model listed: True" in result.output


def test_endpoint_test_by_id(monkeypatch):
    calls = patch_api(
        monkeypatch,
        {
            ("GET", f"/api/v1/endpoints/{ENDPOINT['id']}"): (200, ENDPOINT),
            ("POST", "/test"): (
                200,
                {
                    "ok": True,
                    "models_check": None,
                    "response": {"timing": {"latency_ms": 1, "ttft_ms": None}, "usage": {}},
                },
            ),
        },
    )
    result = runner.invoke(cli.app, ["endpoint", "test", ENDPOINT["id"]])
    assert result.exit_code == 0, result.output
    assert calls[0][1].endswith(f"/api/v1/endpoints/{ENDPOINT['id']}")
    assert "TTFT n/a" in result.output


def test_endpoint_test_failure_exit_codes(monkeypatch):
    failed = {
        "ok": False,
        "models_check": None,
        "response": {
            "error": {"type": "timeout", "message": "timed out"},
            "timing": {"latency_ms": 5000, "ttft_ms": None},
            "usage": {},
        },
    }
    patch_api(
        monkeypatch,
        {
            ("GET", "/api/v1/endpoints"): (200, {"items": [ENDPOINT]}),
            ("POST", "/test"): (200, failed),
        },
    )
    result = runner.invoke(cli.app, ["endpoint", "test", "local-vllm"])
    assert result.exit_code == 1 and "timeout: timed out" in result.output

    patch_api(monkeypatch, {("GET", "/api/v1/endpoints"): (200, {"items": []})})
    assert runner.invoke(cli.app, ["endpoint", "test", "missing"]).exit_code == 1


def test_unreachable_api_exits_2(monkeypatch):
    def boom(*args, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(cli.httpx, "request", boom)
    assert runner.invoke(cli.app, ["endpoint", "list"]).exit_code == 2
