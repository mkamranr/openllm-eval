"""Parse JSONL/CSV dataset content, validate every record, normalize, and hash.

The *normalized* form of a record is its validated model dumped to JSON with ``None`` fields
removed and defaults filled in. A dataset version's ``content_hash`` is the SHA-256 of the
normalized JSONL export (one canonical JSON object per line: sorted keys, compact separators,
UTF-8, ``\\n`` line endings). Consequences:

* the same records produce the same hash whether they came from CSV or JSONL, or with
  different whitespace or key order;
* anyone can verify a version with ``sha256sum`` on the exported file;
* record order is significant (it defines case order in runs).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError

from app.datasets.schema import CASE_MODELS, CASE_TYPES, known_fields

Format = Literal["jsonl", "csv"]
MAX_REPORTED_ISSUES = 200
PREVIEW_SIZE = 5


class DatasetFormatError(ValueError):
    """The content cannot be read at all (wrong encoding, binary, unknown format)."""


@dataclass(frozen=True)
class Limits:
    max_records: int
    max_record_bytes: int


@dataclass
class Issue:
    message: str
    line: int | None = None
    case_id: str | None = None
    field: str | None = None


@dataclass
class NormalizedCase:
    line: int
    case_identifier: str
    case_type: str
    tags: list[str]
    content: dict[str, Any]


@dataclass
class ValidationReport:
    format: Format
    rows: int = 0
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    error_count: int = 0
    warning_count: int = 0
    type_counts: Counter[str] = field(default_factory=Counter)
    tag_counts: Counter[str] = field(default_factory=Counter)
    split_counts: Counter[str] = field(default_factory=Counter)
    cases: list[NormalizedCase] = field(default_factory=list)
    content_hash: str | None = None

    @property
    def valid(self) -> bool:
        return self.error_count == 0 and bool(self.cases)

    @property
    def record_count(self) -> int:
        return len(self.cases)

    def error(self, message: str, **where: Any) -> None:
        self.error_count += 1
        if len(self.errors) < MAX_REPORTED_ISSUES:
            self.errors.append(Issue(message, **where))

    def warn(self, message: str, **where: Any) -> None:
        self.warning_count += 1
        if len(self.warnings) < MAX_REPORTED_ISSUES:
            self.warnings.append(Issue(message, **where))


# --------------------------------------------------------------------------- canonical form


def canonical_line(content: dict[str, Any]) -> str:
    return json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def content_hash(contents: Iterable[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for content in contents:
        digest.update(canonical_line(content).encode("utf-8"))
    return digest.hexdigest()


# --------------------------------------------------------------------------- decoding


def decode_content(raw: bytes) -> str:
    if b"\x00" in raw:
        raise DatasetFormatError(
            "The file looks binary (contains NUL bytes). Use UTF-8 JSONL or CSV."
        )
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DatasetFormatError(
            f"The file is not valid UTF-8 (byte offset {exc.start}). Save it as UTF-8 and retry."
        ) from None


def detect_format(text: str, filename: str | None = None, explicit: str | None = None) -> Format:
    if explicit:
        if explicit not in ("jsonl", "csv"):
            raise DatasetFormatError(f"Unsupported format '{explicit}'. Use 'jsonl' or 'csv'.")
        return explicit  # type: ignore[return-value]
    if filename:
        lowered = filename.lower()
        if lowered.endswith((".jsonl", ".ndjson")):
            return "jsonl"
        if lowered.endswith(".csv"):
            return "csv"
        if lowered.endswith(".json"):
            raise DatasetFormatError(
                "A .json file is not supported; use JSONL (one JSON object per line)."
            )
        if "." in lowered.rsplit("/", 1)[-1]:
            raise DatasetFormatError("Unsupported file type. Upload a .jsonl or .csv file.")
    stripped = text.lstrip()
    if stripped.startswith("["):
        raise DatasetFormatError(
            "The content looks like a JSON array; use JSONL (one JSON object per line)."
        )
    return "jsonl" if stripped.startswith("{") else "csv"


# --------------------------------------------------------------------------- JSONL


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key '{key}'")
        out[key] = value
    return out


def _loads(text: str) -> Any:
    return json.loads(text, parse_constant=_reject_constant, object_pairs_hook=_no_duplicate_keys)


def iter_jsonl(text: str) -> Iterator[tuple[int, dict[str, Any] | str]]:
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = _loads(line)
        except json.JSONDecodeError as exc:
            yield number, f"invalid JSON: {exc.msg} (column {exc.colno})"
            continue
        except ValueError as exc:
            yield number, f"invalid JSON: {exc}"
            continue
        if not isinstance(value, dict):
            yield number, f"each line must be a JSON object, got {type(value).__name__}"
            continue
        yield number, value


# --------------------------------------------------------------------------- CSV

# Columns whose cells hold JSON (objects/arrays). "tags" and "constraints" also accept plain text.
_JSON_COLUMNS = {
    "messages",
    "metadata",
    "tools",
    "expected_tool_calls",
    "tool_results",
    "retrieved_contexts",
    "expected_citations",
    "json_schema",
    "answer_location",
}
_BOOL_COLUMNS = {"answerable", "ordered_calls"}
_NESTED_PREFIXES = {"metadata", "answer_location"}
_TRUE = {"true", "1", "yes", "y"}
_FALSE = {"false", "0", "no", "n"}


def _csv_row(header: list[str], cells: list[str]) -> dict[str, Any] | str:
    if len(cells) != len(header):
        return f"expected {len(header)} columns, found {len(cells)}"
    record: dict[str, Any] = {}
    for column, cell in zip(header, cells, strict=True):
        if cell == "":
            continue
        try:
            if column in _JSON_COLUMNS:
                record[column] = _loads(cell)
            elif column in ("tags", "constraints"):
                if cell.lstrip().startswith("["):
                    record[column] = _loads(cell)
                elif column == "tags":
                    record[column] = [t.strip() for t in cell.split(",") if t.strip()]
                else:
                    record[column] = [cell]
            elif column == "expected_output":
                try:
                    record[column] = _loads(cell)
                except ValueError:
                    record[column] = cell
            elif column in _BOOL_COLUMNS:
                lowered = cell.strip().lower()
                if lowered not in _TRUE | _FALSE:
                    return f"column '{column}': expected true/false, got '{cell}'"
                record[column] = lowered in _TRUE
            elif "." in column and column.split(".", 1)[0] in _NESTED_PREFIXES:
                prefix, key = column.split(".", 1)
                value: Any = cell
                if prefix == "answer_location":
                    try:
                        value = _loads(cell)
                    except ValueError:
                        value = cell
                record.setdefault(prefix, {})[key] = value
            else:
                record[column] = cell
        except ValueError as exc:
            return f"column '{column}': invalid JSON ({exc})"
    return record


def iter_csv(text: str, max_field_bytes: int) -> Iterator[tuple[int, dict[str, Any] | str]]:
    csv.field_size_limit(max(max_field_bytes, 131072))
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        return
    except csv.Error as exc:
        yield 1, f"cannot read CSV header: {exc}"
        return
    header = [h.strip() for h in header]
    if any(not h for h in header):
        yield 1, "the header has an empty column name"
        return
    duplicates = [h for h, n in Counter(header).items() if n > 1]
    if duplicates:
        yield 1, f"the header repeats column(s): {', '.join(duplicates)}"
        return
    previous_end = reader.line_num
    while True:
        start = previous_end + 1
        try:
            cells = next(reader)
        except StopIteration:
            return
        except csv.Error as exc:
            yield start, f"malformed CSV: {exc}"
            return
        previous_end = reader.line_num
        if not any(c.strip() for c in cells):
            continue
        yield start, _csv_row(header, cells)


# --------------------------------------------------------------------------- validation


def _pydantic_issues(exc: ValidationError) -> Iterator[tuple[str | None, str]]:
    for err in exc.errors(include_url=False, include_input=False):
        loc = [str(p) for p in err["loc"]]
        message = err["msg"].removeprefix("Value error, ")
        yield (".".join(loc) or None), message


def validate_rows(
    rows: Iterable[tuple[int, dict[str, Any] | str]],
    *,
    fmt: Format,
    dataset_type: str,
    limits: Limits,
) -> ValidationReport:
    report = ValidationReport(format=fmt)
    seen: dict[str, int] = {}
    for line, row in rows:
        report.rows += 1
        if report.rows > limits.max_records:
            report.error(
                f"too many records: the limit is {limits.max_records} "
                "(DATASET_MAX_RECORDS); validation stopped here",
                line=line,
            )
            break
        if isinstance(row, str):
            report.error(row, line=line)
            continue
        raw_id = row.get("id")
        case_id = raw_id if isinstance(raw_id, str) else None

        case_type = row.get("type")
        if case_type is None:
            if dataset_type == "mixed":
                report.error(
                    "'type' is required because the dataset type is 'mixed'",
                    line=line,
                    case_id=case_id,
                    field="type",
                )
                continue
            case_type = dataset_type
            row = {**row, "type": case_type}
        if not isinstance(case_type, str) or case_type not in CASE_MODELS:
            report.error(
                f"unknown type {case_type!r}; expected one of: {', '.join(CASE_TYPES)}",
                line=line,
                case_id=case_id,
                field="type",
            )
            continue
        if dataset_type != "mixed" and case_type != dataset_type:
            report.error(
                f"type '{case_type}' does not match the dataset type '{dataset_type}'",
                line=line,
                case_id=case_id,
                field="type",
            )
            continue

        try:
            case = CASE_MODELS[case_type].model_validate(row)
        except ValidationError as exc:
            for loc, message in _pydantic_issues(exc):
                report.error(message, line=line, case_id=case_id, field=loc)
            continue

        if case.id in seen:
            report.error(
                f"duplicate id '{case.id}' (first used on line {seen[case.id]})",
                line=line,
                case_id=case.id,
                field="id",
            )
            continue
        seen[case.id] = line

        unknown = sorted(set(row) - known_fields(case_type))
        if unknown:
            report.warn(
                f"unknown field(s) {', '.join(unknown)} are kept but not used by type "
                f"'{case_type}'",
                line=line,
                case_id=case.id,
            )

        content = case.model_dump(mode="json", exclude_none=True)
        size = len(canonical_line(content).encode("utf-8"))
        if size > limits.max_record_bytes:
            report.error(
                f"record is {size} bytes; the limit is {limits.max_record_bytes} "
                "(DATASET_MAX_RECORD_BYTES)",
                line=line,
                case_id=case.id,
            )
            continue

        report.cases.append(
            NormalizedCase(
                line=line,
                case_identifier=case.id,
                case_type=case_type,
                tags=list(case.tags),
                content=content,
            )
        )
        report.type_counts[case_type] += 1
        report.tag_counts.update(case.tags)
        if case.split:
            report.split_counts[case.split] += 1

    if report.rows == 0:
        report.error("the dataset contains no records")
    if report.error_count == 0:
        report.content_hash = content_hash(c.content for c in report.cases)
    return report


def validate_text(
    text: str,
    *,
    fmt: Format,
    dataset_type: str,
    limits: Limits,
) -> ValidationReport:
    rows = iter_jsonl(text) if fmt == "jsonl" else iter_csv(text, limits.max_record_bytes)
    return validate_rows(rows, fmt=fmt, dataset_type=dataset_type, limits=limits)


def validate_bytes(
    raw: bytes,
    *,
    dataset_type: str,
    limits: Limits,
    filename: str | None = None,
    fmt: str | None = None,
) -> ValidationReport:
    text = decode_content(raw)
    detected = detect_format(text, filename, fmt)
    return validate_text(text, fmt=detected, dataset_type=dataset_type, limits=limits)
