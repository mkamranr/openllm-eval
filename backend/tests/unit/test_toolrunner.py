"""Tool runner: registry validation, script isolation and limits, HTTP tool policy, auth."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.toolrunner.http_tool import call_http_tool, tool_policy
from app.toolrunner.limits import argument_error
from app.toolrunner.registry import RegistryError, ToolSpec, parse_registry
from app.toolrunner.script_tool import ScriptLimits, run_script
from app.toolrunner.server import create_app

POSIX = pytest.mark.skipif(os.name != "posix", reason="resource limits and process groups: POSIX")
TOKEN = "unit-test-runner-token-0123456789"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------------------------- registry
def registry_yaml(tmp_path, body: str):
    return parse_registry(body.encode(), tmp_path)


def test_registry_loads_scripts_and_hashes(tmp_path):
    write(tmp_path / "scripts" / "echo.py", "print('{}')")
    reg = registry_yaml(
        tmp_path,
        "tools:\n  - name: echo\n    kind: script\n    script: scripts/echo.py\n    version: 2\n",
    )
    assert reg.tools["echo"].version == "2"
    assert reg.scripts["echo"] == (tmp_path / "scripts" / "echo.py").resolve()
    assert len(reg.sha256) == 64
    assert reg.public()["tools"][0].keys() >= {"name", "version", "parameters", "kind"}


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("tools: [1, 2", "not valid YAML"),
        ("- a\n- b\n", "must be a mapping"),
        ("tools:\n  - name: bad name\n    kind: script\n    script: x.py\n", "name"),
        (
            "tools:\n  - {name: a, kind: script, script: scripts/a.py}\n"
            "  - {name: a, kind: script, script: scripts/a.py}\n",
            "declared twice",
        ),
        ("tools:\n  - {name: a, kind: script, script: ../outside.py}\n", "inside the tools"),
        ("tools:\n  - {name: a, kind: script, script: scripts/missing.py}\n", "not a .py file"),
        ("tools:\n  - {name: a, kind: http}\n", "needs an 'http' section"),
        (
            "tools:\n  - name: a\n    kind: http\n    http: {url: 'http://x', headers: "
            "{Authorization: 'Bearer abc'}}\n",
            "credential",
        ),
        (
            "tools:\n  - name: a\n    kind: http\n    http: {url: 'http://x', headers: "
            "{X-Key: 'env:OPENLLM_SECRET_X'}}\n",
            "non-secret",
        ),
        (
            "tools:\n  - {name: a, kind: script, script: scripts/a.py, parameters: {type: 5}}\n",
            "not a valid JSON Schema",
        ),
        (
            "tools:\n  - {name: a, kind: script, script: scripts/a.py, timeout_seconds: 600}\n",
            "timeout_seconds",
        ),
        ("tools:\n  - {name: a, kind: script, script: scripts/a.py, extra: 1}\n", "extra"),
    ],
)
def test_registry_rejects_bad_files(tmp_path, body, message):
    write(tmp_path / "scripts" / "a.py", "")
    write(tmp_path / "outside.py", "")
    (tmp_path / "tools").mkdir()
    with pytest.raises(RegistryError, match=message):
        parse_registry(body.encode(), tmp_path / "tools" if "outside" in body else tmp_path)


def test_argument_validation():
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": ["n"],
        "additionalProperties": False,
    }
    assert argument_error(schema, {"n": 1}) is None
    assert "required" in argument_error(schema, {})
    assert "at n" in argument_error(schema, {"n": "x"})
    assert argument_error(schema, [1]) == "Arguments must be a JSON object."


# ----------------------------------------------------------------------------- script tools
async def test_script_round_trip_and_clean_environment(tmp_path):
    script = write(
        tmp_path / "s.py",
        "import json, os, sys\n"
        "args = json.load(sys.stdin)\n"
        "print(json.dumps({'got': args, 'env': sorted(k for k in os.environ if k != 'SYSTEMROOT'),"
        " 'cwd': os.getcwd()}))\n",
    )
    os.environ["OPENLLM_SECRET_LEAK_TEST"] = "should-not-be-visible"
    try:
        result = await run_script(script, {"x": 1}, timeout=10, max_output_bytes=10_000)
    finally:
        del os.environ["OPENLLM_SECRET_LEAK_TEST"]
    assert result.ok, result.error
    assert result.result["got"] == {"x": 1}
    assert [k for k in result.result["env"] if not k.startswith("__")] in ([], ["LC_CTYPE"])
    assert not Path(result.result["cwd"]).exists()  # the temporary directory is removed


async def test_script_errors(tmp_path):
    bad_json = write(tmp_path / "a.py", "print('not json')")
    crash = write(tmp_path / "b.py", "import sys\nsys.stderr.write('boom')\nsys.exit(3)")
    loud = write(tmp_path / "c.py", "print('x' * 5000)")
    assert (await run_script(bad_json, {}, timeout=10, max_output_bytes=1000)).error == (
        "The script did not print a JSON value."
    )
    failed = await run_script(crash, {}, timeout=10, max_output_bytes=1000)
    assert not failed.ok and "exit code 3" in failed.error and "boom" in failed.error
    truncated = await run_script(loud, {}, timeout=10, max_output_bytes=1000)
    assert not truncated.ok and truncated.truncated


async def test_script_timeout_kills_it(tmp_path):
    slow = write(tmp_path / "slow.py", "import time\ntime.sleep(30)")
    result = await run_script(slow, {}, timeout=1, max_output_bytes=1000)
    assert not result.ok and "timed out" in result.error
    assert result.duration_ms < 10_000


@POSIX
async def test_script_memory_limit(tmp_path):
    hog = write(tmp_path / "hog.py", "x = bytearray(512 * 1024 * 1024)\nprint('{}')")
    result = await run_script(
        hog, {}, timeout=10, max_output_bytes=1000, limits=ScriptLimits(memory_mb=128)
    )
    assert not result.ok and "MemoryError" in result.error


@POSIX
async def test_script_children_die_with_it(tmp_path):
    marker = tmp_path / "marker"
    script = write(
        tmp_path / "fork.py",
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3); "
        f'open({str(marker)!r}, "w").write("x")\'])\n'
        "time.sleep(30)\n",
    )
    await run_script(script, {}, timeout=1, max_output_bytes=1000)
    import asyncio

    await asyncio.sleep(3.5)
    assert not marker.exists()


async def test_example_tools(tmp_path):
    tools = Path(__file__).resolve().parents[3] / "tools"
    calc = await run_script(
        tools / "scripts" / "calculator.py",
        {"expression": "2 * (3 + 4) ** 2"},
        timeout=10,
        max_output_bytes=1000,
    )
    assert calc.result == {"result": 98}
    evil = await run_script(
        tools / "scripts" / "calculator.py",
        {"expression": "__import__('os').system('id')"},
        timeout=10,
        max_output_bytes=1000,
    )
    assert "invalid expression" in evil.result["error"]
    order = await run_script(
        tools / "scripts" / "lookup_order.py",
        {"order_id": "A-1001"},
        timeout=10,
        max_output_bytes=1000,
    )
    assert order.result["status"] == "shipped"


# ------------------------------------------------------------------------------- http tools
def http_tool(url: str, method: str = "POST") -> ToolSpec:
    return ToolSpec(name="t", kind="http", http={"url": url, "method": method})


async def test_http_tool_requires_allowlist_and_blocks_redirects():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})
        return httpx.Response(200, json={"echo": request.url.params.get("q")})

    transport = httpx.MockTransport(handler)
    empty = tool_policy([], [])
    result = await call_http_tool(
        http_tool("http://10.0.0.5/x"), {}, policy=empty, timeout=5, transport=transport
    )
    assert "TOOL_ALLOWED_HOSTS is empty" in result.error and not calls

    policy = tool_policy(["10.0.0.5"], ["postgres"])
    ok = await call_http_tool(
        http_tool("http://10.0.0.5/x", "GET"),
        {"q": "hi"},
        policy=policy,
        timeout=5,
        transport=transport,
    )
    assert ok.ok and ok.result == {"echo": "hi"}
    redirected = await call_http_tool(
        http_tool("http://10.0.0.5/redirect"), {}, policy=policy, timeout=5, transport=transport
    )
    assert not redirected.ok and "redirects are not followed" in redirected.error
    blocked = await call_http_tool(
        http_tool("http://10.0.0.6/x"), {}, policy=policy, timeout=5, transport=transport
    )
    assert not blocked.ok and "not allowed" in blocked.error
    assert len(calls) == 2


async def test_http_tool_caps_response():
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 5000))
    tool = ToolSpec(name="t", kind="http", http={"url": "http://10.0.0.5/"}, max_output_bytes=1000)
    result = await call_http_tool(
        tool, {}, policy=tool_policy(["10.0.0.5"], []), timeout=5, transport=transport
    )
    assert not result.ok and result.truncated


# ------------------------------------------------------------------------------------ server
def runner_client(tmp_path, **settings_kw):
    write(
        tmp_path / "scripts" / "echo.py",
        "import json, sys\nprint(json.dumps(json.load(sys.stdin)))",
    )
    write(
        tmp_path / "tools.yaml",
        "tools:\n  - name: echo\n    kind: script\n    script: scripts/echo.py\n"
        "    parameters: {type: object, properties: {n: {type: integer}}, required: [n]}\n",
    )
    settings = Settings(tool_runner_token=SecretStr(TOKEN), **settings_kw)
    app = create_app(settings, tools_dir=tmp_path)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner")


async def test_server_requires_token_and_validates(tmp_path):
    async with runner_client(tmp_path) as client:
        assert (await client.get("/health")).json()["registry_ok"] is True
        assert (await client.get("/tools")).status_code == 401
        auth = {"Authorization": f"Bearer {TOKEN}"}
        assert (
            await client.get("/tools", headers={"Authorization": "Bearer nope"})
        ).status_code == 401
        tools = (await client.get("/tools", headers=auth)).json()
        assert [t["name"] for t in tools["tools"]] == ["echo"] and tools["error"] is None
        ok = (
            await client.post(
                "/execute", json={"tool": "echo", "arguments": {"n": 1}}, headers=auth
            )
        ).json()
        assert ok["ok"] and ok["result"] == {"n": 1}
        bad = (
            await client.post(
                "/execute", json={"tool": "echo", "arguments": {"n": "x"}}, headers=auth
            )
        ).json()
        assert not bad["ok"] and "Invalid arguments" in bad["error"]
        missing = (await client.post("/execute", json={"tool": "nope"}, headers=auth)).json()
        assert "not registered" in missing["error"]


async def test_server_reloads_and_reports_invalid_registry(tmp_path):
    async with runner_client(tmp_path) as client:
        auth = {"Authorization": f"Bearer {TOKEN}"}
        write(tmp_path / "tools.yaml", "tools: [1")
        os.utime(tmp_path / "tools.yaml", (1, 1))  # a different mtime, whatever the clock
        tools = (await client.get("/tools", headers=auth)).json()
        assert tools["tools"] == [] and "not valid YAML" in tools["error"]


def test_server_needs_a_token(tmp_path):
    with pytest.raises(RuntimeError, match="TOOL_RUNNER_TOKEN"):
        create_app(Settings(tool_runner_token=None), tools_dir=tmp_path)


def test_python_is_isolated_mode_capable():
    # The launcher relies on -I -S, which every supported Python has.
    assert sys.version_info >= (3, 12)
