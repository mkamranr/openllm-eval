from __future__ import annotations

import json

import httpx
from typer.testing import CliRunner

from app.cli import main as cli
from app.datasets.samples import SAMPLES

runner = CliRunner()


def test_validate_sample_offline():
    result = runner.invoke(cli.app, ["dataset", "validate", str(SAMPLES["general-qa"].path)])
    assert result.exit_code == 0, result.output
    assert "general_qa.jsonl: VALID" in result.output
    assert "valid records: 12" in result.output and "sha256: " in result.output


def test_validate_reports_line_numbers_and_exits_1(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text(
        json.dumps({"id": "a", "type": "qa", "input": "x", "reference_answer": "y"})
        + "\n"
        + json.dumps({"id": "b", "type": "qa", "input": "x"})
        + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(cli.app, ["dataset", "validate", str(path)])
    assert result.exit_code == 1
    assert "bad.jsonl: INVALID" in result.output
    assert "ERROR line 2 [b] reference_answer: Field required" in result.output


def test_validate_json_output_and_type_option(tmp_path):
    path = tmp_path / "cases.csv"
    path.write_text("id,input,reference_answer\nq1,Hi?,Hello\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["dataset", "validate", str(path), "--type", "qa", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["valid"] and report["format"] == "csv" and report["type_counts"] == {"qa": 1}
    bad_type = runner.invoke(cli.app, ["dataset", "validate", str(path), "--type", "poem"])
    assert bad_type.exit_code == 2


def test_validate_unsupported_file(tmp_path):
    path = tmp_path / "cases.xlsx"
    path.write_bytes(b"PK\x03\x04")
    result = runner.invoke(cli.app, ["dataset", "validate", str(path)])
    assert result.exit_code == 1 and "error:" in result.output


def test_import_prints_validation_errors(monkeypatch, tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text('{"id": "a"}\n', encoding="utf-8")
    sent = {}

    def fake_post(url, json, timeout):
        sent.update(url=url, body=json)
        return httpx.Response(
            422,
            json={
                "error": {
                    "code": "dataset_invalid",
                    "message": "The dataset has 1 validation error; nothing was imported.",
                    "details": {
                        "format": "jsonl",
                        "rows": 1,
                        "record_count": 0,
                        "error_count": 1,
                        "warning_count": 0,
                        "type_counts": {},
                        "warnings": [],
                        "content_hash": None,
                        "errors": [
                            {"line": 1, "case_id": "a", "field": "type", "message": "required"}
                        ],
                    },
                }
            },
        )

    monkeypatch.setattr(cli.httpx, "post", fake_post)
    result = runner.invoke(
        cli.app, ["dataset", "import", str(path), "--type", "qa", "--api-url", "http://api"]
    )
    assert result.exit_code == 1
    assert sent["url"] == "http://api/api/v1/datasets"
    assert sent["body"]["name"] == "cases" and sent["body"]["dataset_type"] == "qa"
    assert "ERROR line 1 [a] type: required" in result.output


def test_import_success(monkeypatch, tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        cli.httpx,
        "post",
        lambda url, json, timeout: httpx.Response(
            201,
            json={
                "current_version": {
                    "version_number": 1,
                    "record_count": 3,
                    "content_hash": "ab" * 32,
                }
            },
        ),
    )
    result = runner.invoke(cli.app, ["dataset", "import", str(path), "--name", "x"])
    assert result.exit_code == 0 and "imported: v1 | 3 records" in result.output
