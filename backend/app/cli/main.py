"""``openllm-eval`` command-line interface.

Designed for CI and headless use: machine-readable output with ``--json`` and non-zero exit codes
on failure.
"""

from __future__ import annotations

import json
import os
import platform
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import typer
import yaml

from app import API_VERSION, __version__

app = typer.Typer(
    name="openllm-eval", help="OpenLLM-Eval command-line interface.", no_args_is_help=True
)

DEFAULT_API_URL = "http://localhost:8000"


# ------------------------------------------------------------------------------ authentication
def token_file() -> Path:
    """Where ``openllm-eval auth login`` stores the API token (mode 0600)."""
    override = os.environ.get("OPENLLM_TOKEN_FILE")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "openllm-eval" / "token"


def _token() -> str | None:
    value = os.environ.get("OPENLLM_API_TOKEN", "").strip()
    if value:
        return value
    try:
        stored = token_file().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return stored or None


def _auth() -> dict[str, Any]:
    """Extra httpx arguments carrying the API token, when one is configured."""
    token = _token()
    return {"headers": {"Authorization": f"Bearer {token}"}} if token else {}


@app.command()
def version(as_json: bool = typer.Option(False, "--json", help="Print JSON output.")) -> None:
    """Print the framework version."""
    if as_json:
        typer.echo(json.dumps({"version": __version__, "api_version": API_VERSION}))
    else:
        typer.echo(f"openllm-eval {__version__} (api {API_VERSION})")


@app.command()
def health(
    api_url: str = typer.Option(
        None, "--api-url", help="API base URL. Defaults to $OPENLLM_API_URL or localhost:8000."
    ),
    timeout: float = typer.Option(5.0, help="Request timeout in seconds."),
    as_json: bool = typer.Option(False, "--json", help="Print the raw readiness JSON."),
) -> None:
    """Check API readiness (database, Redis, worker). Exits 1 if the API is not ready."""
    base = (api_url or os.environ.get("OPENLLM_API_URL") or DEFAULT_API_URL).rstrip("/")
    try:
        response = httpx.get(f"{base}/api/v1/health/ready", timeout=timeout, **_auth())
    except httpx.HTTPError as exc:
        typer.echo(f"error: could not reach API at {base}: {type(exc).__name__}", err=True)
        raise typer.Exit(code=2) from exc

    try:
        body = response.json()
    except ValueError:
        typer.echo(f"error: non-JSON response (HTTP {response.status_code})", err=True)
        raise typer.Exit(code=2) from None

    if as_json:
        typer.echo(json.dumps(body, indent=2))
    else:
        typer.echo(f"overall:  {body.get('status')}")
        for name in ("database", "redis", "worker"):
            component = body.get(name) or {}
            detail = f" ({component['detail']})" if component.get("detail") else ""
            typer.echo(f"{name + ':':<9} {component.get('status')}{detail}")

    if response.status_code != 200:
        raise typer.Exit(code=1)


# --------------------------------------------------------------------------- endpoints
endpoint_app = typer.Typer(help="Manage model endpoints.", no_args_is_help=True)
app.add_typer(endpoint_app, name="endpoint")

API_URL_HELP = "API base URL. Defaults to $OPENLLM_API_URL or localhost:8000."


def _base(api_url: str | None) -> str:
    return (api_url or os.environ.get("OPENLLM_API_URL") or DEFAULT_API_URL).rstrip("/")


def _request(method: str, url: str, timeout: float = 30.0, **kwargs: Any) -> Any:
    """Call the API; print a clean error and exit 1 (API error) or 2 (unreachable)."""
    try:
        response = httpx.request(method, url, timeout=timeout, **_auth(), **kwargs)
    except httpx.HTTPError as exc:
        typer.echo(f"error: could not reach API: {type(exc).__name__}", err=True)
        raise typer.Exit(code=2) from exc
    try:
        body = response.json()
    except ValueError:
        typer.echo(f"error: non-JSON response (HTTP {response.status_code})", err=True)
        raise typer.Exit(code=2) from None
    if response.status_code >= 400:
        error = body.get("error", {}) if isinstance(body, dict) else {}
        typer.echo(f"error: {error.get('message', response.status_code)}", err=True)
        if response.status_code == 401:
            typer.echo("hint: run 'openllm-eval auth login' or set OPENLLM_API_TOKEN", err=True)
        raise typer.Exit(code=1)
    return body


def _resolve_endpoint(base: str, name_or_id: str) -> dict[str, Any]:
    try:
        uuid.UUID(name_or_id)
    except ValueError:
        page = _request("GET", f"{base}/api/v1/endpoints", params={"name": name_or_id})
        if not page["items"]:
            typer.echo(f"error: no endpoint named '{name_or_id}'", err=True)
            raise typer.Exit(code=1) from None
        return page["items"][0]  # type: ignore[no-any-return]
    return _request("GET", f"{base}/api/v1/endpoints/{name_or_id}")  # type: ignore[no-any-return]


@endpoint_app.command("list")
def endpoint_list(
    api_url: str = typer.Option(None, "--api-url", help=API_URL_HELP),
    as_json: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """List registered endpoints."""
    items = _request("GET", f"{_base(api_url)}/api/v1/endpoints", params={"limit": 500})["items"]
    if as_json:
        typer.echo(json.dumps(items, indent=2))
        return
    if not items:
        typer.echo("No endpoints registered.")
        return
    rows = [("NAME", "ENABLED", "LAST TEST", "MODEL", "BASE URL")] + [
        (
            e["name"],
            "yes" if e["enabled"] else "no",
            e["last_test_status"] or "-",
            e["model_identifier"],
            e["base_url"],
        )
        for e in items
    ]
    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    for row in rows:
        cells = [c.ljust(w) for c, w in zip(row[:4], widths, strict=True)]
        typer.echo("  ".join([*cells, row[4]]))


@endpoint_app.command("test")
def endpoint_test(
    name_or_id: str = typer.Argument(..., help="Endpoint name or id."),
    api_url: str = typer.Option(None, "--api-url", help=API_URL_HELP),
    prompt: str = typer.Option("Reply with the single word: ok", help="Test prompt."),
    stream: bool | None = typer.Option(
        None, "--stream/--no-stream", help="Override the endpoint's stream setting."
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the full JSON result."),
) -> None:
    """Test connectivity and send one small request. Exits 1 if the test fails."""
    base = _base(api_url)
    endpoint = _resolve_endpoint(base, name_or_id)
    result = _request(
        "POST",
        f"{base}/api/v1/endpoints/{endpoint['id']}/test",
        json={"prompt": prompt, "stream": stream},
        timeout=600.0,
    )
    if as_json:
        typer.echo(json.dumps(result, indent=2))
    else:
        response = result["response"]
        timing = response.get("timing") or {}
        models = result.get("models_check") or {}
        usage = response.get("usage") or {}
        typer.echo(f"endpoint:  {endpoint['name']} ({endpoint['model_identifier']})")
        typer.echo(f"result:    {'ok' if result['ok'] else 'FAILED'}")
        if models:
            listed = models.get("model_listed")
            suffix = f", model listed: {listed}" if listed is not None else ""
            typer.echo(f"/models:   {'ok' if models.get('ok') else 'failed'}{suffix}")
        if response.get("error"):
            error = response["error"]
            typer.echo(f"error:     {error['type']}: {error['message']}")
        ttft = timing.get("ttft_ms")
        ttft_text = f"TTFT {ttft} ms" if ttft is not None else "TTFT n/a (non-streaming)"
        typer.echo(f"latency:   {timing.get('latency_ms')} ms | {ttft_text}")
        typer.echo(
            f"tokens:    in={usage.get('input_tokens')} out={usage.get('output_tokens')}"
            f" ({usage.get('source')})"
        )
        if response.get("content"):
            typer.echo(f"content:   {response['content'][:200]}")
    if not result["ok"]:
        raise typer.Exit(code=1)


# --------------------------------------------------------------------------- datasets
dataset_app = typer.Typer(help="Validate and manage datasets.", no_args_is_help=True)
app.add_typer(dataset_app, name="dataset")


def _print_issues(kind: str, issues: list[dict[str, Any]], total: int) -> None:
    for issue in issues:
        where = f"line {issue['line']}" if issue.get("line") else "file"
        case = f" [{issue['case_id']}]" if issue.get("case_id") else ""
        field = f" {issue['field']}:" if issue.get("field") else ""
        typer.echo(f"  {kind} {where}{case}{field} {issue['message']}")
    if total > len(issues):
        typer.echo(f"  ... and {total - len(issues)} more")


def _print_report(result: dict[str, Any]) -> None:
    typer.echo(
        f"format: {result['format']} | rows: {result['rows']} | valid records: "
        f"{result['record_count']} | errors: {result['error_count']} | "
        f"warnings: {result['warning_count']}"
    )
    if result["type_counts"]:
        typer.echo("types:  " + ", ".join(f"{k}={v}" for k, v in result["type_counts"].items()))
    _print_issues("ERROR", result["errors"], result["error_count"])
    _print_issues("warn ", result["warnings"], result["warning_count"])
    if result.get("content_hash"):
        typer.echo(f"sha256: {result['content_hash']}  (normalized JSONL, schema v1)")


@dataset_app.command("validate")
def dataset_validate(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    dataset_type: str = typer.Option(
        "mixed", "--type", help="Declared dataset type (records may then omit 'type')."
    ),
    fmt: str = typer.Option(None, "--format", help="jsonl or csv (default: from extension)."),
    as_json: bool = typer.Option(False, "--json", help="Print the full JSON report."),
) -> None:
    """Validate a JSONL/CSV dataset locally (no API needed). Exits 1 if invalid."""
    from app.core.config import Settings
    from app.core.exceptions import AppError
    from app.datasets.schema import DATASET_TYPES
    from app.services.dataset_service import to_result, validate_upload

    if dataset_type not in DATASET_TYPES:
        typer.echo(f"error: --type must be one of {', '.join(DATASET_TYPES)}", err=True)
        raise typer.Exit(code=2)
    settings = Settings()
    try:
        report = validate_upload(
            settings, path.read_bytes(), dataset_type=dataset_type, filename=path.name, fmt=fmt
        )
    except AppError as exc:  # unreadable file, unsupported format, too large
        typer.echo(f"error: {exc.message}", err=True)
        raise typer.Exit(code=1) from None
    result = to_result(report, settings, dataset_type).model_dump(mode="json")
    if as_json:
        typer.echo(json.dumps(result, indent=2))
    else:
        typer.echo(f"{path.name}: {'VALID' if result['valid'] else 'INVALID'}")
        _print_report(result)
    if not result["valid"]:
        raise typer.Exit(code=1)


@dataset_app.command("list")
def dataset_list(
    api_url: str = typer.Option(None, "--api-url", help=API_URL_HELP),
    as_json: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """List datasets and their current versions."""
    items = _request("GET", f"{_base(api_url)}/api/v1/datasets", params={"limit": 500})["items"]
    if as_json:
        typer.echo(json.dumps(items, indent=2))
        return
    if not items:
        typer.echo("No datasets.")
        return
    rows = [("NAME", "TYPE", "VERSION", "RECORDS", "SHA256")]
    for d in items:
        v = d.get("current_version") or {}
        rows.append(
            (
                d["name"],
                d["dataset_type"],
                f"v{v['version_number']}" if v else "-",
                str(v.get("record_count", "-")),
                v.get("content_hash", "-")[:12],
            )
        )
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for row in rows:
        typer.echo("  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip())


@dataset_app.command("generate-needle")
def dataset_generate_needle(
    name: str = typer.Option(..., "--name", help="New dataset name."),
    lengths: str = typer.Option(
        "1024,4096,16384", "--lengths", help="Requested context lengths in tokens, comma-separated."
    ),
    depths: str = typer.Option(
        "0.1,0.5,0.9", "--depths", help="Needle positions (0 = start, 1 = end), comma-separated."
    ),
    facts: int = typer.Option(1, "--facts", help="Facts per case (more than 1 = multi-fact)."),
    repeats: int = typer.Option(1, "--repeats", help="Cases per length and depth."),
    seed: int = typer.Option(0, "--seed"),
    api_url: str = typer.Option(None, "--api-url", help=API_URL_HELP),
) -> None:
    """Generate a synthetic context-window dataset (deterministic for the same options)."""
    base = _base(api_url)
    try:
        params = {
            "context_lengths": [int(x) for x in lengths.split(",") if x.strip()],
            "depths": [float(x) for x in depths.split(",") if x.strip()],
            "needles_per_case": facts,
            "repeats": repeats,
            "seed": seed,
        }
    except ValueError:
        typer.echo("error: --lengths takes integers and --depths numbers", err=True)
        raise typer.Exit(code=1) from None
    body = _request(
        "POST",
        f"{base}/api/v1/datasets/generate",
        timeout=300.0,
        json={"name": name, "params": params},
    )
    version = body.get("current_version") or {}
    typer.echo(
        f"generated: {body['id']} | {version.get('record_count')} cases | "
        f"sha256 {version.get('content_hash')}"
    )


@dataset_app.command("import")
def dataset_import(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    name: str = typer.Option(None, "--name", help="New dataset name (default: file name)."),
    dataset_type: str = typer.Option("mixed", "--type", help="Declared dataset type."),
    to: str = typer.Option(
        None, "--to", help="Add as a new version of this existing dataset (name or id)."
    ),
    label: str = typer.Option(None, "--label", help="Version label."),
    api_url: str = typer.Option(None, "--api-url", help=API_URL_HELP),
) -> None:
    """Upload a dataset (or a new version) to the API. Exits 1 on validation errors."""
    base = _base(api_url)
    content = path.read_text(encoding="utf-8-sig")
    if to:
        target = to
        try:
            uuid.UUID(to)
        except ValueError:
            page = _request("GET", f"{base}/api/v1/datasets", params={"q": to, "limit": 500})
            match = [d for d in page["items"] if d["name"] == to]
            if not match:
                typer.echo(f"error: no dataset named '{to}'", err=True)
                raise typer.Exit(code=1) from None
            target = match[0]["id"]
        url = f"{base}/api/v1/datasets/{target}/versions"
        payload: dict[str, Any] = {"content": content, "filename": path.name}
    else:
        url = f"{base}/api/v1/datasets"
        payload = {
            "name": name or path.stem,
            "dataset_type": dataset_type,
            "content": content,
            "filename": path.name,
        }
    if label:
        payload["version_label"] = label
    try:
        response = httpx.post(url, json=payload, timeout=300.0, **_auth())
    except httpx.HTTPError as exc:
        typer.echo(f"error: could not reach API: {type(exc).__name__}", err=True)
        raise typer.Exit(code=2) from exc
    body = response.json()
    if response.status_code >= 400:
        error = body.get("error", {})
        typer.echo(f"error: {error.get('message')}", err=True)
        if error.get("code") == "dataset_invalid" and isinstance(error.get("details"), dict):
            _print_report(error["details"])
        raise typer.Exit(code=1)
    version = body if to else body.get("current_version") or {}
    typer.echo(
        f"imported: v{version.get('version_number')} | {version.get('record_count')} records | "
        f"sha256 {version.get('content_hash')}"
    )


# -------------------------------------------------------------------------------- runs
run_app = typer.Typer(help="Create and follow benchmark runs.", no_args_is_help=True)
app.add_typer(run_app, name="run")

TERMINAL = ("completed", "completed_with_errors", "cancelled", "failed")
CONFIG_KEYS = {
    "name",
    "description",
    "project",
    "endpoints",
    "dataset",
    "datasets",
    "evaluation",
    "generation",
    "load",
    "report",
    "profile",
}


def _resolve_by_name(base: str, kind: str, ref: str) -> dict[str, Any]:
    """Find a project/endpoint/dataset/profile by id or exact name."""
    try:
        uuid.UUID(ref)
    except ValueError:
        pass
    else:
        return _request("GET", f"{base}/api/v1/{kind}/{ref}")  # type: ignore[no-any-return]
    params: dict[str, Any] = {"limit": 500}
    if kind == "endpoints":
        params["name"] = ref
    elif kind != "profiles":
        params["q"] = ref
    page = _request("GET", f"{base}/api/v1/{kind}", params=params)
    match = [item for item in page["items"] if item["name"] == ref]
    if not match:
        typer.echo(f"error: no {kind[:-1]} named '{ref}'", err=True)
        raise typer.Exit(code=1)
    return match[0]  # type: ignore[no-any-return]


def _dataset_from_path(base: str, path: Path, name: str | None) -> dict[str, Any]:
    """Import a dataset file, reusing the existing version when the content is unchanged."""
    if not path.is_file():
        typer.echo(f"error: dataset file not found: {path}", err=True)
        raise typer.Exit(code=1)
    name = name or path.stem
    content = path.read_text(encoding="utf-8-sig")
    page = _request("GET", f"{base}/api/v1/datasets", params={"q": name, "limit": 500})
    existing = next((d for d in page["items"] if d["name"] == name), None)
    if existing is None:
        created = _request(
            "POST",
            f"{base}/api/v1/datasets",
            timeout=300.0,
            json={"name": name, "dataset_type": "mixed", "content": content, "filename": path.name},
        )
        return {"dataset_id": created["id"], "version_id": created["current_version"]["id"]}
    try:
        response = httpx.request(
            "POST",
            f"{base}/api/v1/datasets/{existing['id']}/versions",
            json={"content": content, "filename": path.name},
            timeout=300.0,
            **_auth(),
        )
    except httpx.HTTPError as exc:
        typer.echo(f"error: could not reach API: {type(exc).__name__}", err=True)
        raise typer.Exit(code=2) from exc
    body = response.json()
    details = (body.get("error") or {}).get("details") if response.status_code >= 400 else None
    if response.status_code == 409 and isinstance(details, dict) and details.get("version_id"):
        # Identical content: reuse that version (same content hash).
        return {"dataset_id": existing["id"], "version_id": details["version_id"]}
    if response.status_code >= 400:
        typer.echo(f"error: {body['error'].get('message')}", err=True)
        raise typer.Exit(code=1)
    return {"dataset_id": existing["id"], "version_id": body["id"]}


def build_run_payload(base: str, config: dict[str, Any], config_dir: Path) -> dict[str, Any]:
    """Translate a YAML benchmark config (spec section 10) into a POST /runs body."""
    unknown = set(config) - CONFIG_KEYS
    if unknown:
        typer.echo(f"error: unknown config keys: {', '.join(sorted(unknown))}", err=True)
        raise typer.Exit(code=1)
    endpoints = []
    for entry in config.get("endpoints") or []:
        ref = entry if isinstance(entry, str) else (entry or {}).get("name")
        if not ref:
            typer.echo("error: each endpoint needs a name", err=True)
            raise typer.Exit(code=1)
        endpoints.append(_resolve_by_name(base, "endpoints", str(ref))["id"])
    if not endpoints:
        typer.echo("error: the config lists no endpoints", err=True)
        raise typer.Exit(code=1)

    raw = config.get("datasets") or ([config["dataset"]] if config.get("dataset") else [])
    datasets = []
    for item in raw:
        entry = {"name": item} if isinstance(item, str) else dict(item or {})
        if entry.get("path"):
            datasets.append(_dataset_from_path(base, config_dir / entry["path"], entry.get("name")))
            continue
        dataset = _resolve_by_name(base, "datasets", str(entry.get("name") or entry.get("id")))
        ref_body: dict[str, Any] = {"dataset_id": dataset["id"]}
        if entry.get("version") is not None:
            versions = _request("GET", f"{base}/api/v1/datasets/{dataset['id']}/versions")
            match = [v for v in versions if v["version_number"] == int(entry["version"])]
            if not match:
                typer.echo(f"error: {dataset['name']} has no version {entry['version']}", err=True)
                raise typer.Exit(code=1)
            ref_body["version_id"] = match[0]["id"]
        datasets.append(ref_body)
    if not datasets:
        typer.echo("error: the config names no dataset", err=True)
        raise typer.Exit(code=1)

    generation = dict(config.get("generation") or {})
    load = dict(config.get("load") or {})
    if "stream" in generation:  # the spec's example puts stream under generation
        load.setdefault("stream", generation.pop("stream"))
    evaluation = dict(config.get("evaluation") or {})
    suites = evaluation.pop("suites", None)
    # The embedding endpoint and the LLM judge are named by endpoint name or id.
    for key in ("embedding", "judge"):
        block = evaluation.get(key)
        if isinstance(block, dict) and block.get("endpoint"):
            ref = str(block.pop("endpoint"))
            block["endpoint_id"] = _resolve_by_name(base, "endpoints", ref)["id"]
    payload: dict[str, Any] = {
        "name": config.get("name") or "benchmark",
        "description": config.get("description"),
        "endpoint_ids": endpoints,
        "datasets": datasets,
    }
    # With a profile, only what the config sets explicitly overrides the profile.
    if config.get("profile"):
        payload["profile_id"] = _resolve_by_name(base, "profiles", str(config["profile"]))["id"]
    if suites or not config.get("profile"):
        payload["suites"] = suites or ["performance"]
    if evaluation:
        payload["evaluation"] = evaluation
    if generation or not config.get("profile"):
        payload["generation"] = generation
    if load or not config.get("profile"):
        payload["load"] = load
    if config.get("project"):
        payload["project_id"] = _resolve_by_name(base, "projects", str(config["project"]))["id"]
    return payload


def _fmt(value: Any, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _progress_line(run: dict[str, Any]) -> str:
    p = run.get("progress") or {}
    planned = p.get("planned")
    total = f"/{planned}" if planned else ""
    return (
        f"{run['status']:<22} {p.get('completed', 0)}{total} requests | "
        f"ok {p.get('succeeded', 0)} | failed {p.get('failed', 0)} | "
        f"in flight {p.get('in_flight', 0)}"
    )


def _exit_for(status: str) -> None:
    if status != "completed":
        raise typer.Exit(code=1)


def _wait(base: str, run_id: str, interval: float, quiet: bool) -> dict[str, Any]:
    last = ""
    while True:
        run = _request("GET", f"{base}/api/v1/runs/{run_id}")
        line = _progress_line(run)
        if line != last and not quiet:
            typer.echo(line, err=True)
            last = line
        if run["status"] in TERMINAL:
            return run  # type: ignore[no-any-return]
        time.sleep(interval)


def _print_metrics(base: str, run: dict[str, Any]) -> None:
    metrics = _request("GET", f"{base}/api/v1/runs/{run['id']}/metrics")
    table: dict[str, dict[tuple[str, str], Any]] = {}
    context: dict[str, dict[int, dict[tuple[str, str], Any]]] = {}
    for m in metrics["items"]:
        if m.get("context_length") is not None:
            key = (m["metric_name"], m["aggregation"])
            context.setdefault(m["run_model_id"], {}).setdefault(m["context_length"], {})[key] = m[
                "metric_value"
            ]
        elif m["load_step"] is None:
            key = (m["metric_name"], m["aggregation"])
            table.setdefault(m["run_model_id"], {})[key] = m["metric_value"]
    live = " (live)" if metrics["live"] else ""
    typer.echo(f"run {run['id']}: {run['status']}{live}")
    typer.echo(
        f"{'model':<28}{'ok/total':>10}{'err %':>7}{'TTFT p50':>10}{'TTFT p95':>10}"
        f"{'lat p50':>10}{'lat p95':>10}{'tok/s p50':>10}{'req/s':>8}"
    )
    for model in run["models"]:
        v = table.get(model["id"], {})
        err = v.get(("error_rate", "ratio"))
        ok_total = (
            f"{_fmt(v.get(('requests', 'succeeded')))}/{_fmt(v.get(('requests', 'attempted')))}"
        )
        typer.echo(
            f"{model['name'][:27]:<28}{ok_total:>10}"
            f"{_fmt(err * 100 if err is not None else None):>7}"
            f"{_fmt(v.get(('ttft_ms', 'p50'))):>10}{_fmt(v.get(('ttft_ms', 'p95'))):>10}"
            f"{_fmt(v.get(('latency_ms', 'p50'))):>10}{_fmt(v.get(('latency_ms', 'p95'))):>10}"
            f"{_fmt(v.get(('output_tokens_per_second', 'p50'))):>10}"
            f"{_fmt(v.get(('request_throughput', 'value')), 2):>8}"
        )
    typer.echo("times in ms; n/a = not measurable (the API metadata says why)")
    if context:
        typer.echo("")
        typer.echo("context window (by requested length; tokens = server-reported mean)")
        typer.echo(
            f"  {'model':<26}{'length':>9}{'ok %':>7}{'overflow':>9}{'retrieval':>10}"
            f"{'TTFT p50':>10}{'tokens':>9}"
        )
        for model in run["models"]:
            for length, v in sorted(context.get(model["id"], {}).items()):

                def pct(key: tuple[str, str], v: dict = v) -> str:
                    val = v.get(key)
                    return f"{val * 100:.0f}%" if val is not None else "n/a"

                typer.echo(
                    f"  {model['name'][:25]:<26}{length:>9}"
                    f"{pct(('context.success_rate', 'ratio')):>7}"
                    f"{pct(('context.overflow_rate', 'ratio')):>9}"
                    f"{pct(('context.retrieval_accuracy', 'pass_rate')):>10}"
                    f"{_fmt(v.get(('context.ttft_ms', 'p50'))):>10}"
                    f"{_fmt(v.get(('context.input_tokens', 'mean'))):>9}"
                )
    quality = {
        mid: {
            k: val
            for k, val in vals.items()
            if k[0].startswith(("quality.", "tool.", "rag.", "hallucination."))
        }
        for mid, vals in table.items()
    }
    if any(quality.values()):
        typer.echo("")
        typer.echo("quality (pass rate over evaluated cases; n/a = no applicable case)")
        for model in run["models"]:
            rows = quality.get(model["id"], {})
            if not rows:
                continue
            typer.echo(f"  {model['name']}")
            for (name, agg), val in sorted(rows.items()):
                if agg in ("pass_rate", "ratio"):
                    pct = f"{val * 100:.1f}%" if val is not None else "n/a"
                    typer.echo(f"    {name:<44}{pct:>8}")


@run_app.command("create")
def run_create(
    config: Path = typer.Option(
        ..., "--config", exists=True, dir_okay=False, readable=True, help="YAML configuration."
    ),
    wait: bool = typer.Option(False, "--wait", help="Follow progress until the run finishes."),
    interval: float = typer.Option(2.0, help="Polling interval with --wait (seconds)."),
    as_json: bool = typer.Option(False, "--json", help="Print the run as JSON."),
    report_dir: Path = typer.Option(
        Path("."), "--report-dir", help="Where report.formats from the config are saved (--wait)."
    ),
    api_url: str = typer.Option(None, "--api-url", help=API_URL_HELP),
) -> None:
    """Create a run from a YAML config. With --wait, exit 0 only if it completes cleanly."""
    base = _base(api_url)
    try:
        data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        typer.echo(f"error: invalid YAML: {exc}", err=True)
        raise typer.Exit(code=1) from None
    if not isinstance(data, dict):
        typer.echo("error: the config must be a YAML mapping", err=True)
        raise typer.Exit(code=1)
    payload = build_run_payload(base, data, config.parent)
    try:
        response = httpx.request(
            "POST", f"{base}/api/v1/runs", json=payload, timeout=60.0, **_auth()
        )
    except httpx.HTTPError as exc:
        typer.echo(f"error: could not reach API: {type(exc).__name__}", err=True)
        raise typer.Exit(code=2) from exc
    body = response.json()
    if response.status_code >= 400:
        error = body.get("error", {})
        typer.echo(f"error: {error.get('message')}", err=True)
        for detail in error.get("details") or []:
            if isinstance(detail, dict):
                loc = ".".join(str(p) for p in detail.get("loc", [])[1:])
                typer.echo(f"  {loc}: {detail.get('msg')}", err=True)
        raise typer.Exit(code=1)
    run_id = body["id"]
    if not wait:
        if data.get("report"):
            typer.echo("note: 'report' in the config is generated with --wait only", err=True)
        typer.echo(json.dumps(body, indent=2) if as_json else f"created run {run_id}")
        return
    typer.echo(f"created run {run_id}", err=True)
    run = _wait(base, run_id, interval, quiet=False)
    if as_json:
        typer.echo(json.dumps(run, indent=2))
    else:
        _print_metrics(base, run)
    report_cfg = data.get("report") or {}
    for fmt in (report_cfg.get("formats") or []) if isinstance(report_cfg, dict) else []:
        path = _generate_report(base, run_id, str(fmt), report_dir, False, False)
        typer.echo(f"saved {fmt} report to {path}", err=True)
    _exit_for(run["status"])


@run_app.command("status")
def run_status(
    run_id: str = typer.Argument(...),
    watch: bool = typer.Option(False, "--watch", help="Follow until the run finishes."),
    as_json: bool = typer.Option(False, "--json"),
    api_url: str = typer.Option(None, "--api-url", help=API_URL_HELP),
) -> None:
    """Show run status. Exit 1 if the run finished with errors, failed, or was cancelled."""
    base = _base(api_url)
    if watch:
        run = _wait(base, run_id, 2.0, quiet=as_json)
    else:
        run = _request("GET", f"{base}/api/v1/runs/{run_id}")
    if as_json:
        typer.echo(json.dumps(run, indent=2))
    else:
        typer.echo(f"{run['name']} ({run['id']})")
        typer.echo(_progress_line(run))
        for m in run["models"]:
            p = m.get("progress") or {}
            typer.echo(f"  {m['name']:<30} {m['status']:<10} {p.get('completed', 0)} done")
        if run.get("error_summary"):
            typer.echo(f"  {run['error_summary'].get('message')}")
    if run["status"] in TERMINAL:
        _exit_for(run["status"])


@run_app.command("results")
def run_results(
    run_id: str = typer.Argument(...),
    as_json: bool = typer.Option(False, "--json", help="Print all metrics as JSON."),
    api_url: str = typer.Option(None, "--api-url", help=API_URL_HELP),
) -> None:
    """Print aggregate metrics per model."""
    base = _base(api_url)
    run = _request("GET", f"{base}/api/v1/runs/{run_id}")
    if as_json:
        typer.echo(json.dumps(_request("GET", f"{base}/api/v1/runs/{run_id}/metrics"), indent=2))
    else:
        _print_metrics(base, run)


@run_app.command("list")
def run_list(
    limit: int = typer.Option(20, help="Number of runs."),
    as_json: bool = typer.Option(False, "--json"),
    api_url: str = typer.Option(None, "--api-url", help=API_URL_HELP),
) -> None:
    """List recent runs."""
    page = _request("GET", f"{_base(api_url)}/api/v1/runs", params={"limit": limit})
    if as_json:
        typer.echo(json.dumps(page["items"], indent=2))
        return
    for r in page["items"]:
        models = ", ".join(m["name"] for m in r["models"])
        typer.echo(f"{r['id']}  {r['status']:<22} {r['name'][:30]:<30} {models}")


@run_app.command("cancel")
def run_cancel(
    run_id: str = typer.Argument(...),
    api_url: str = typer.Option(None, "--api-url", help=API_URL_HELP),
) -> None:
    """Request cancellation of a run."""
    run = _request("POST", f"{_base(api_url)}/api/v1/runs/{run_id}/cancel")
    typer.echo(f"{run['id']}: {run['status']}")


REPORT_FORMATS = ("html", "json", "csv", "metrics_csv", "markdown")


def _download_bytes(url: str, **params: Any) -> bytes:
    try:
        response = httpx.get(url, params=params, timeout=120.0, **_auth())
    except httpx.HTTPError as exc:
        typer.echo(f"error: could not reach API: {type(exc).__name__}", err=True)
        raise typer.Exit(code=2) from exc
    if response.status_code >= 400:
        try:
            message = response.json().get("error", {}).get("message")
        except ValueError:
            message = None
        typer.echo(f"error: {message or response.status_code}", err=True)
        raise typer.Exit(code=1)
    return response.content


def _generate_report(
    base: str, run_id: str, fmt: str, out: Path, redact_content: bool, redact_endpoints: bool
) -> Path:
    if fmt not in REPORT_FORMATS:
        typer.echo(f"error: unknown report format '{fmt}' ({', '.join(REPORT_FORMATS)})", err=True)
        raise typer.Exit(code=1)
    report = _request(
        "POST",
        f"{base}/api/v1/runs/{run_id}/reports",
        timeout=300.0,
        json={
            "format": fmt,
            "redact_content": redact_content,
            "redact_endpoints": redact_endpoints,
        },
    )
    content = _download_bytes(f"{base}/api/v1/reports/{report['id']}/download")
    target = out / report["filename"] if out.is_dir() else out
    target.write_bytes(content)
    return target


report_app = typer.Typer(help="Generate and download run reports.", no_args_is_help=True)
app.add_typer(report_app, name="report")


@report_app.command("generate")
def report_generate(
    run_id: str = typer.Argument(...),
    fmt: str = typer.Option("html", "--format", "-f", help=", ".join(REPORT_FORMATS)),
    output: Path = typer.Option(Path("."), "--output", "-o", help="File or directory."),
    redact_content: bool = typer.Option(
        False, "--redact-content", help="Leave out generated text, error messages, rationales."
    ),
    redact_endpoints: bool = typer.Option(
        False, "--redact-endpoints", help="Replace endpoint URLs with [redacted]."
    ),
    api_url: str = typer.Option(None, "--api-url", help=API_URL_HELP),
) -> None:
    """Generate a report for a finished run and save it."""
    path = _generate_report(_base(api_url), run_id, fmt, output, redact_content, redact_endpoints)
    typer.echo(str(path))


@report_app.command("list")
def report_list(
    run_id: str = typer.Option(None, "--run", help="Only reports of this run."),
    as_json: bool = typer.Option(False, "--json"),
    api_url: str = typer.Option(None, "--api-url", help=API_URL_HELP),
) -> None:
    """List generated reports."""
    params: dict[str, Any] = {"limit": 100}
    if run_id:
        params["run_id"] = run_id
    page = _request("GET", f"{_base(api_url)}/api/v1/reports", params=params)
    if as_json:
        typer.echo(json.dumps(page["items"], indent=2))
        return
    for r in page["items"]:
        typer.echo(
            f"{r['id']}  {r['report_type']:<11} {r['size_bytes']:>9} B  "
            f"{r['generated_at'][:19]}  {(r.get('run_name') or '')[:30]}"
        )


@report_app.command("download")
def report_download(
    report_id: str = typer.Argument(...),
    output: Path = typer.Option(Path("."), "--output", "-o", help="File or directory."),
    api_url: str = typer.Option(None, "--api-url", help=API_URL_HELP),
) -> None:
    """Download a previously generated report."""
    base = _base(api_url)
    report = _request("GET", f"{base}/api/v1/reports/{report_id}")
    content = _download_bytes(f"{base}/api/v1/reports/{report_id}/download")
    target = output / report["filename"] if output.is_dir() else output
    target.write_bytes(content)
    typer.echo(str(target))


LABELS = {
    "equivalent_configuration": "equivalent configuration",
    "partially_controlled": "partially controlled",
    "not_directly_comparable": "NOT directly comparable",
}
COMPARE_SUMMARY = [
    ("error_rate", "ratio"),
    ("latency_ms", "p50"),
    ("latency_ms", "p95"),
    ("ttft_ms", "p50"),
    ("output_tokens_per_second", "mean"),
    ("request_throughput", "value"),
]


@app.command("compare")
def compare(
    run_ids: list[str] = typer.Argument(
        ..., help="Two or more run ids (one run compares its models)."
    ),
    baseline: str = typer.Option(None, "--baseline", help="Baseline run id (default: the first)."),
    as_json: bool = typer.Option(False, "--json", help="Print the full comparison as JSON."),
    output: Path = typer.Option(
        None, "--output", "-o", help="Save an export (.html, .csv, or .json by extension)."
    ),
    api_url: str = typer.Option(None, "--api-url", help=API_URL_HELP),
) -> None:
    """Compare runs side by side and flag configuration differences."""
    base = _base(api_url)
    params: dict[str, Any] = {"run_ids": ",".join(run_ids)}
    if baseline:
        params["baseline"] = baseline
    if output is not None:
        fmt = output.suffix.lstrip(".").lower()
        if fmt not in ("html", "csv", "json"):
            typer.echo("error: --output must end in .html, .csv, or .json", err=True)
            raise typer.Exit(code=1)
        output.write_bytes(_download_bytes(f"{base}/api/v1/compare/export", **params, format=fmt))
        typer.echo(f"saved {output}", err=True)
    result = _request("GET", f"{base}/api/v1/compare", params=params)
    if as_json:
        typer.echo(json.dumps(result, indent=2))
        return
    comp = result["comparability"]
    typer.echo(
        f"comparability: {LABELS[comp['overall']]} (performance: "
        f"{LABELS[comp['performance']]}, quality: {LABELS[comp['quality']]})"
    )
    for d in comp["differences"]:
        if d["severity"] == "info":
            continue
        values = ", ".join(json.dumps(v) for v in d["values"].values())
        typer.echo(f"  [{d['severity']}] {d['label']}: {values}")
    subjects = result["subjects"]
    by_key = {(m["metric_name"], m["aggregation"]): m for m in result["metrics"]}
    typer.echo("")
    typer.echo(f"{'metric':<34}" + "".join(f"{s['label'][:22]:>24}" for s in subjects))
    keys = COMPARE_SUMMARY + sorted(
        k for k in by_key if k[0].startswith("quality.") and k[1] == "pass_rate"
    )
    for name, agg in keys:
        m = by_key.get((name, agg))
        if not m:
            continue
        cells = []
        for v in m["values"]:
            text = _fmt(
                v["value"] * 100 if m["unit"] == "ratio" and v["value"] is not None else v["value"]
            )
            if m["unit"] == "ratio" and v["value"] is not None:
                text += "%"
            if v["relative_change"] is not None:
                text += f" ({v['relative_change'] * 100:+.0f}%)"
            cells.append(f"{text:>24}")
        typer.echo(f"{(name + ' ' + agg)[:33]:<34}" + "".join(cells))
    for t in result["transitions"]:
        label = next(s["label"] for s in subjects if s["id"] == t["subject_id"])
        regress = sum(v["pass_to_fail_total"] for v in t["evaluators"].values())
        fixed = sum(v["fail_to_pass_total"] for v in t["evaluators"].values())
        typer.echo(
            f"{label}: {regress} pass->fail, {fixed} fail->pass, "
            f"{t['new_errors_total']} new errors, {t['fixed_errors_total']} fixed errors"
        )
    for note in result["notes"]:
        typer.echo(f"note: {note}", err=True)


@run_app.command("compare")
def run_compare(
    run_ids: list[str] = typer.Argument(...),
    baseline: str = typer.Option(None, "--baseline"),
    as_json: bool = typer.Option(False, "--json"),
    output: Path = typer.Option(None, "--output", "-o"),
    api_url: str = typer.Option(None, "--api-url", help=API_URL_HELP),
) -> None:
    """Same as `openllm-eval compare`."""
    compare(run_ids, baseline, as_json, output, api_url)


@run_app.command("rerun")
def run_rerun(
    run_id: str = typer.Argument(...),
    wait: bool = typer.Option(False, "--wait", help="Follow progress until the run finishes."),
    interval: float = typer.Option(2.0, help="Polling interval with --wait (seconds)."),
    api_url: str = typer.Option(None, "--api-url", help=API_URL_HELP),
) -> None:
    """Create a new run with the same configuration as an earlier one."""
    base = _base(api_url)
    run = _request("POST", f"{base}/api/v1/runs/{run_id}/rerun", timeout=60.0)
    typer.echo(f"created run {run['id']}", err=True if wait else False)
    if wait:
        run = _wait(base, run["id"], interval, quiet=False)
        _print_metrics(base, run)
        _exit_for(run["status"])


# ------------------------------------------------------------------------------------ auth
auth_app = typer.Typer(help="Sign in and manage the stored API token.", no_args_is_help=True)
app.add_typer(auth_app, name="auth")

CSRF = {"X-OpenLLM-CSRF": "1"}


def _api_error(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error", {}).get("message") or response.status_code)
    except ValueError:
        return f"HTTP {response.status_code}"


def _save_token(token: str) -> Path:
    path = token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


@auth_app.command("login")
def auth_login(
    username: str = typer.Option(..., "--username", "-u", prompt=True),
    password: str = typer.Option(..., "--password", prompt=True, hide_input=True),
    days: int = typer.Option(90, "--days", help="Token lifetime in days (0 = never expires)."),
    api_url: str = typer.Option(None, "--api-url", help=API_URL_HELP),
) -> None:
    """Sign in with your username and password (local or Windows/AD) and store an API token.

    The token goes to ~/.config/openllm-eval/token (mode 0600); OPENLLM_API_TOKEN overrides it.
    """
    base = _base(api_url)
    try:
        with httpx.Client(base_url=base, timeout=30.0, headers=CSRF) as client:
            response = client.post(
                "/api/v1/auth/login", json={"username": username, "password": password}
            )
            if response.status_code != 200:
                typer.echo(f"error: {_api_error(response)}", err=True)
                raise typer.Exit(code=1)
            if response.json().get("must_change_password"):
                client.post("/api/v1/auth/logout")
                typer.echo(
                    "error: you must change your password first; sign in to the web UI.", err=True
                )
                raise typer.Exit(code=1)
            created = client.post(
                "/api/v1/auth/tokens",
                json={
                    "name": f"cli ({platform.node() or 'unknown host'})"[:100],
                    "expires_in_days": days or None,
                },
            )
            client.post("/api/v1/auth/logout")
    except httpx.HTTPError as exc:
        typer.echo(f"error: could not reach API at {base}: {type(exc).__name__}", err=True)
        raise typer.Exit(code=2) from exc
    if created.status_code != 201:
        typer.echo(f"error: {_api_error(created)}", err=True)
        raise typer.Exit(code=1)
    path = _save_token(created.json()["token"])
    typer.echo(f"Signed in as {username}; token saved to {path}")


@auth_app.command("logout")
def auth_logout(api_url: str = typer.Option(None, "--api-url", help=API_URL_HELP)) -> None:
    """Revoke the stored API token and delete the token file."""
    path = token_file()
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        typer.echo("Not signed in (no token file).")
        return
    base = _base(api_url)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        listing = httpx.get(f"{base}/api/v1/auth/tokens", headers=headers, timeout=30.0)
        if listing.status_code == 200:
            for item in listing.json():
                if token.startswith(item["prefix"]) and item["revoked_at"] is None:
                    httpx.delete(
                        f"{base}/api/v1/auth/tokens/{item['id']}", headers=headers, timeout=30.0
                    )
    except httpx.HTTPError:
        typer.echo("warning: could not reach the API; the token was not revoked.", err=True)
    path.unlink(missing_ok=True)
    typer.echo("Signed out.")


@auth_app.command("whoami")
def auth_whoami(
    api_url: str = typer.Option(None, "--api-url", help=API_URL_HELP),
    as_json: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    """Show the account the CLI is using."""
    me = _request("GET", f"{_base(api_url)}/api/v1/auth/me")
    if as_json:
        typer.echo(json.dumps(me, indent=2))
        return
    if not me.get("auth_enabled"):
        typer.echo("Authentication is disabled on this server (everyone is an administrator).")
        return
    typer.echo(f"{me['username']} ({me['role']}, via {me['auth_method']})")


if __name__ == "__main__":
    app()
