from __future__ import annotations

import hashlib
import json

import pytest

from app.datasets.samples import SAMPLES
from app.datasets.validation import (
    DatasetFormatError,
    Limits,
    canonical_line,
    decode_content,
    detect_format,
    validate_bytes,
    validate_text,
)

LIMITS = Limits(max_records=1000, max_record_bytes=1024 * 1024)

QA = {"id": "q1", "type": "qa", "input": "What is 2+2?", "reference_answer": "4", "tags": ["math"]}
WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
            "additionalProperties": False,
        },
    },
}


def jsonl(*records: dict) -> str:
    return "".join(json.dumps(r) + "\n" for r in records)


def check(text: str, dataset_type: str = "mixed", fmt: str = "jsonl"):
    return validate_text(text, fmt=fmt, dataset_type=dataset_type, limits=LIMITS)  # type: ignore[arg-type]


def messages(report) -> list[str]:
    return [f"{e.line}:{e.field}:{e.message}" for e in report.errors]


# ----------------------------------------------------------------- happy paths


def test_valid_jsonl_is_normalized_and_hashed():
    report = check(jsonl(QA, {**QA, "id": "q2", "tags": [" math ", "math", "easy"]}))
    assert report.valid and report.record_count == 2 and report.error_count == 0
    assert report.cases[1].tags == ["math", "easy"]  # trimmed and de-duplicated
    # Defaults are explicit and None fields are dropped in the normalized form.
    assert report.cases[0].content == {
        "id": "q1",
        "type": "qa",
        "input": "What is 2+2?",
        "reference_answer": "4",
        "tags": ["math"],
        "metadata": {},
    }
    assert report.type_counts == {"qa": 2}
    assert report.tag_counts == {"math": 2, "easy": 1}
    assert report.content_hash and len(report.content_hash) == 64


@pytest.mark.parametrize("sample_id", list(SAMPLES))
def test_bundled_samples_are_valid(sample_id):
    sample = SAMPLES[sample_id]
    report = validate_bytes(
        sample.read_bytes(),
        dataset_type=sample.dataset_type,
        limits=LIMITS,
        filename=sample.filename,
    )
    assert report.valid, messages(report)
    assert report.warning_count == 0


def test_hash_is_reproducible_across_formatting_key_order_and_format():
    a = check(jsonl(QA, {**QA, "id": "q2"}))
    reordered = (
        "\n\n"
        + "\n".join(
            json.dumps(dict(reversed(list(r.items()))), indent=None, separators=(" ,", " : "))
            for r in (QA, {**QA, "id": "q2"})
        )
        + "\n"
    )
    b = check(reordered)
    csv_text = (
        "id,type,input,reference_answer,tags\r\n"
        "q1,qa,What is 2+2?,4,math\r\n"
        'q2,qa,"What is 2+2?",4,"math"\r\n'
    )
    c = check(csv_text, fmt="csv")
    assert a.valid and b.valid and c.valid, (messages(b), messages(c))
    assert a.content_hash == b.content_hash == c.content_hash


def test_hash_equals_sha256_of_normalized_export_and_changes_with_content():
    report = check(jsonl(QA))
    export = "".join(canonical_line(c.content) for c in report.cases).encode()
    assert hashlib.sha256(export).hexdigest() == report.content_hash
    assert check(jsonl({**QA, "reference_answer": "four"})).content_hash != report.content_hash
    # Order is part of the content.
    two = check(jsonl(QA, {**QA, "id": "q2"}))
    swapped = check(jsonl({**QA, "id": "q2"}, QA))
    assert two.content_hash != swapped.content_hash


def test_dataset_type_fills_missing_record_type():
    record = {k: v for k, v in QA.items() if k != "type"}
    report = check(jsonl(record), dataset_type="qa")
    assert report.valid and report.cases[0].content["type"] == "qa"


# ----------------------------------------------------------------- errors with line numbers


def test_errors_report_line_numbers_case_ids_and_fields():
    text = (
        json.dumps(QA) + "\n"
        "\n"  # blank lines are skipped but still counted for line numbers
        "{not json}\n"
        + json.dumps({"id": "q3", "type": "qa", "input": "x"})
        + "\n"
        + json.dumps({**QA})
        + "\n"  # duplicate id
        + json.dumps(["a list"])
        + "\n"
        + json.dumps({"id": "q7", "type": "poem", "input": "x"})
        + "\n"
    )
    report = check(text)
    assert not report.valid and report.content_hash is None
    assert report.rows == 6 and report.record_count == 1
    found = messages(report)
    assert found[0].startswith("3:None:invalid JSON")
    assert "4:reference_answer:Field required" in found
    assert any(m.startswith("5:id:duplicate id 'q1' (first used on line 1)") for m in found)
    assert "6:None:each line must be a JSON object, got list" in found
    assert any(m.startswith("7:type:unknown type 'poem'") for m in found)
    assert report.errors[1].case_id == "q3"


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"id": "a", "type": "chat"}, "provide exactly one of 'input' or 'messages'"),
        (
            {
                "id": "a",
                "type": "chat",
                "input": "x",
                "messages": [{"role": "user", "content": "x"}],
            },
            "provide exactly one",
        ),
        (
            {"id": "a", "type": "chat", "messages": [{"role": "assistant", "content": "x"}]},
            "at least one user message",
        ),
        ({"id": " a", "type": "chat", "input": "x"}, "whitespace"),
        ({"id": "a", "type": "chat", "input": "x", "tags": ["ok", ""]}, "1-64 characters"),
        ({"id": "a", "type": "chat", "input": "x", "split": "holdout"}, "Input should be"),
        ({"id": "a", "type": "long_context", "input": "q", "context": "c"}, "expected_answer"),
        ({"id": "a", "type": "safety", "input": "q"}, "Field required"),
        (
            {
                "id": "a",
                "type": "structured_output",
                "input": "q",
                "json_schema": {"type": "object", "properties": {"n": {"type": "integer"}}},
                "expected_output": {"n": "five"},
            },
            "expected_output does not match json_schema",
        ),
        (
            {"id": "a", "type": "structured_output", "input": "q", "json_schema": {"type": 12}},
            "not a valid JSON Schema",
        ),
    ],
)
def test_type_specific_rules(record, expected):
    report = check(jsonl(record))
    assert not report.valid
    assert any(expected in e.message for e in report.errors), messages(report)


def test_tool_call_rules():
    base = {"id": "t", "type": "tool_call", "input": "weather in Oslo?", "tools": [WEATHER_TOOL]}
    ok = check(
        jsonl(
            {
                **base,
                "expected_tool_calls": [{"name": "get_weather", "arguments": {"city": "Oslo"}}],
            }
        )
    )
    assert ok.valid, messages(ok)
    assert ok.cases[0].content["expected_behavior"] == "call_tool"

    cases = {
        "unknown tool": {**base, "expected_tool_calls": [{"name": "search", "arguments": {}}]},
        "do not match": {
            **base,
            "expected_tool_calls": [{"name": "get_weather", "arguments": {"town": "Oslo"}}],
        },
        "needs at least one": {**base},
        "must be empty": {
            **base,
            "expected_behavior": "no_tool",
            "expected_tool_calls": [{"name": "get_weather", "arguments": {"city": "Oslo"}}],
        },
        "names must be unique": {
            **base,
            "tools": [WEATHER_TOOL, WEATHER_TOOL],
            "expected_behavior": "no_tool",
        },
    }
    for expected, record in cases.items():
        report = check(jsonl(record))
        assert any(expected in e.message for e in report.errors), (expected, messages(report))
    no_tool = check(jsonl({**base, "expected_behavior": "no_tool"}))
    assert no_tool.valid


def test_rag_citations_must_reference_supplied_documents():
    base = {
        "id": "r",
        "type": "rag",
        "input": "q",
        "retrieved_contexts": [{"document_id": "doc-1", "chunk_id": "c1", "text": "fact"}],
    }
    assert check(jsonl({**base, "expected_citations": [{"document_id": "doc-1"}]})).valid
    bad = check(jsonl({**base, "expected_citations": [{"document_id": "doc-9"}]}))
    assert any("not in retrieved_contexts" in e.message for e in bad.errors)
    abstain = check(
        jsonl({**base, "answerable": False, "expected_citations": [{"document_id": "doc-1"}]})
    )
    assert any("unanswerable" in e.message for e in abstain.errors)
    empty = check(jsonl({**base, "retrieved_contexts": []}))
    assert not empty.valid


def test_unknown_fields_are_kept_with_a_warning():
    report = check(jsonl({**QA, "refrence_answer": "typo"}))
    assert report.valid and report.warning_count == 1
    assert "refrence_answer" in report.warnings[0].message
    assert report.cases[0].content["refrence_answer"] == "typo"


def test_type_must_match_declared_dataset_type_and_mixed_requires_type():
    assert "does not match the dataset type" in check(jsonl(QA), "rag").errors[0].message
    untyped = {k: v for k, v in QA.items() if k != "type"}
    assert "'type' is required" in check(jsonl(untyped), "mixed").errors[0].message


def test_json_strictness_nan_and_duplicate_keys():
    nan = check('{"id": "a", "type": "chat", "input": "x", "metadata": {"v": NaN}}\n')
    dup = check('{"id": "a", "id": "b", "type": "chat", "input": "x"}\n')
    assert "NaN is not valid JSON" in nan.errors[0].message
    assert "duplicate key 'id'" in dup.errors[0].message


def test_limits_on_records_and_record_size():
    many = check(jsonl(*({**QA, "id": f"q{i}"} for i in range(5))))
    assert many.valid
    small = validate_text(
        jsonl(*({**QA, "id": f"q{i}"} for i in range(5))),
        fmt="jsonl",
        dataset_type="mixed",
        limits=Limits(max_records=3, max_record_bytes=10_000),
    )
    assert any("too many records" in e.message for e in small.errors)
    big = validate_text(
        jsonl({**QA, "input": "x" * 5000}),
        fmt="jsonl",
        dataset_type="mixed",
        limits=Limits(max_records=10, max_record_bytes=1000),
    )
    assert any("DATASET_MAX_RECORD_BYTES" in e.message for e in big.errors)


def test_empty_content_is_invalid():
    report = check("\n\n")
    assert not report.valid and "no records" in report.errors[0].message


# ----------------------------------------------------------------- CSV


def test_csv_json_columns_booleans_nested_and_line_numbers():
    text = (
        "id,type,input,retrieved_contexts,answerable,metadata.owner,tags\n"
        'r1,rag,"Multi\nline question",'
        '"[{""document_id"": ""d1"", ""text"": ""t""}]",no,ops,"a, b"\n'
        "r2,rag,q,not-json,true,,\n"
        'r3,rag,q,"[{""text"": ""t""}]",maybe,,\n'
        "r4,rag,too,few\n"
    )
    report = check(text, fmt="csv")
    assert report.cases[0].content["input"] == "Multi\nline question"
    assert report.cases[0].content["answerable"] is False
    assert report.cases[0].content["metadata"] == {"owner": "ops"}
    assert report.cases[0].tags == ["a", "b"]
    found = messages(report)
    # r2 starts on physical line 4 because r1's quoted cell spans two lines.
    assert any(m.startswith("4:None:column 'retrieved_contexts': invalid JSON") for m in found)
    assert "5:None:column 'answerable': expected true/false, got 'maybe'" in found
    assert "6:None:expected 7 columns, found 4" in found


def test_csv_header_problems():
    assert "repeats column" in check("id,id\n1,2\n", fmt="csv").errors[0].message
    assert "empty column name" in check("id,,type\n1,2,3\n", fmt="csv").errors[0].message


# ----------------------------------------------------------------- decoding / detection


def test_decoding_rejects_binary_and_non_utf8_and_strips_bom():
    with pytest.raises(DatasetFormatError, match="binary"):
        decode_content(b"\x00\x01PK")
    with pytest.raises(DatasetFormatError, match="not valid UTF-8"):
        decode_content("café".encode("latin-1"))
    assert decode_content("﻿{}".encode()) == "{}"


@pytest.mark.parametrize(
    ("text", "filename", "explicit", "expected"),
    [
        ("{}", "a.jsonl", None, "jsonl"),
        ("{}", "a.NDJSON", None, "jsonl"),
        ("id,type", "a.csv", None, "csv"),
        ('  {"id": 1}', None, None, "jsonl"),
        ("id,type\n", None, None, "csv"),
        ("{}", "a.csv", "jsonl", "jsonl"),
    ],
)
def test_detect_format(text, filename, explicit, expected):
    assert detect_format(text, filename, explicit) == expected


@pytest.mark.parametrize(
    ("text", "filename", "match"),
    [
        ("[]", "a.json", "not supported"),
        ("x", "a.xlsx", "Unsupported file type"),
        ('[{"id": 1}]', None, "JSON array"),
    ],
)
def test_detect_format_rejects(text, filename, match):
    with pytest.raises(DatasetFormatError, match=match):
        detect_format(text, filename)
