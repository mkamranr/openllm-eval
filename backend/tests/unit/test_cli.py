from __future__ import annotations

import json

import httpx
from typer.testing import CliRunner

from app import __version__
from app.cli import main as cli

runner = CliRunner()

READY = {
    "status": "ok",
    "database": {"status": "ok"},
    "redis": {"status": "ok"},
    "worker": {"status": "unavailable", "active_workers": 0, "detail": "no worker heartbeat"},
}


def test_version():
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_version_json():
    result = runner.invoke(cli.app, ["version", "--json"])
    assert json.loads(result.output) == {"version": __version__, "api_version": "v1"}


def _patch_get(monkeypatch, response=None, exc=None):
    calls = []

    def fake_get(url, timeout):
        calls.append(url)
        if exc:
            raise exc
        return response

    monkeypatch.setattr(cli.httpx, "get", fake_get)
    return calls


def test_health_ok(monkeypatch):
    calls = _patch_get(monkeypatch, httpx.Response(200, json=READY))
    result = runner.invoke(cli.app, ["health", "--api-url", "http://api:8000/"])
    assert result.exit_code == 0
    assert calls == ["http://api:8000/api/v1/health/ready"]
    assert "overall:  ok" in result.output
    assert "no worker heartbeat" in result.output


def test_health_degraded_exits_1(monkeypatch):
    _patch_get(monkeypatch, httpx.Response(503, json={**READY, "status": "degraded"}))
    result = runner.invoke(cli.app, ["health"])
    assert result.exit_code == 1


def test_health_unreachable_exits_2(monkeypatch):
    _patch_get(monkeypatch, exc=httpx.ConnectError("refused"))
    result = runner.invoke(cli.app, ["health"])
    assert result.exit_code == 2
