from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import select

from app.db.models import DatasetCase, DatasetVersion
from app.db.models.dataset import ImmutableRecordError


def jsonl(*records: dict) -> str:
    return "".join(json.dumps(r) + "\n" for r in records)


def qa(i: int, **extra) -> dict:
    return {
        "id": f"q{i}",
        "type": "qa",
        "input": f"Question {i}?",
        "reference_answer": f"A{i}",
        "tags": ["even" if i % 2 == 0 else "odd"],
        **extra,
    }


async def create(api, name="qa-set", records=None, **fields):
    records = records if records is not None else [qa(i) for i in range(1, 6)]
    body = {
        "name": name,
        "dataset_type": "qa",
        "content": jsonl(*records),
        "filename": "cases.jsonl",
        **fields,
    }
    return await api.post("/api/v1/datasets", json=body)


async def test_create_dataset_with_first_version(api):
    response = await create(api, description="five cases", tags=["demo"], version_label="initial")
    assert response.status_code == 201, response.text
    dataset = response.json()
    version = dataset["current_version"]
    assert dataset["dataset_type"] == "qa" and dataset["tags"] == ["demo"]
    assert dataset["version_count"] == 1 and dataset["source"] == "user"
    assert version["version_number"] == 1 and version["version_label"] == "initial"
    assert version["record_count"] == 5 and version["is_current"] is True
    assert version["source_format"] == "jsonl" and version["source_filename"] == "cases.jsonl"
    assert version["type_counts"] == {"qa": 5}
    assert version["tag_counts"] == {"odd": 3, "even": 2}
    assert len(version["content_hash"]) == 64 and version["schema_version"] == "1"

    listed = (await api.get("/api/v1/datasets")).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == dataset["id"]
    assert (await api.get("/api/v1/datasets?dataset_type=rag")).json()["total"] == 0
    assert (await api.get("/api/v1/datasets?tag=demo")).json()["total"] == 1
    assert (await api.get("/api/v1/datasets?q=QA-")).json()["total"] == 1


async def test_invalid_upload_reports_rows_and_creates_nothing(api):
    records = [qa(1), {"id": "q2", "type": "qa", "input": "missing answer"}, qa(1)]
    response = await create(api, records=records)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "dataset_invalid"
    details = error["details"]
    assert details["valid"] is False and details["error_count"] == 2
    assert [(e["line"], e["field"]) for e in details["errors"]] == [
        (2, "reference_answer"),
        (3, "id"),
    ]
    assert (await api.get("/api/v1/datasets")).json()["total"] == 0


async def test_validate_endpoint_does_not_save(api):
    response = await api.post(
        "/api/v1/datasets/validate",
        json={"content": "id,type,input,reference_answer\nq1,qa,Hi?,Hello\n", "dataset_type": "qa"},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["valid"] is True and result["format"] == "csv"
    assert result["preview"][0]["reference_answer"] == "Hello"
    assert result["limits"]["max_records"] > 0
    assert (await api.get("/api/v1/datasets")).json()["total"] == 0


async def test_versions_are_immutable_hash_deduplicated_and_switchable(api, migrated_app):
    dataset = (await create(api)).json()
    did = dataset["id"]
    v1 = dataset["current_version"]

    same = await api.post(
        f"/api/v1/datasets/{did}/versions", json={"content": jsonl(*[qa(i) for i in range(1, 6)])}
    )
    assert same.status_code == 409
    assert same.json()["error"]["details"]["version_number"] == 1

    v2 = await api.post(
        f"/api/v1/datasets/{did}/versions",
        json={"content": jsonl(*[qa(i) for i in range(1, 8)]), "version_label": "more"},
    )
    assert v2.status_code == 201, v2.text
    assert v2.json()["version_number"] == 2 and v2.json()["record_count"] == 7
    assert v2.json()["is_current"] is True

    versions = (await api.get(f"/api/v1/datasets/{did}/versions")).json()
    assert [v["version_number"] for v in versions] == [2, 1]
    assert [v["is_current"] for v in versions] == [True, False]

    # Pin the older version again.
    pinned = await api.patch(f"/api/v1/datasets/{did}", json={"current_version_id": v1["id"]})
    assert pinned.json()["current_version"]["version_number"] == 1
    old = (await api.get(f"/api/v1/datasets/{did}/versions/{v1['id']}")).json()
    assert old["content_hash"] == v1["content_hash"] and old["record_count"] == 5

    # The ORM refuses to modify a stored version or case.
    async with migrated_app.state.session_factory() as session:
        version = await session.get(DatasetVersion, __import__("uuid").UUID(v1["id"]))
        version.record_count = 99
        with pytest.raises(ImmutableRecordError):
            await session.flush()
        await session.rollback()
        case = await session.scalar(select(DatasetCase).limit(1))
        case.content = {"tampered": True}
        with pytest.raises(ImmutableRecordError):
            await session.flush()


async def test_export_matches_content_hash(api):
    dataset = (await create(api, name="Export me!")).json()
    version = dataset["current_version"]
    response = await api.get(f"/api/v1/datasets/{dataset['id']}/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["x-content-sha256"] == version["content_hash"]
    assert 'filename="export-me-v1.jsonl"' in response.headers["content-disposition"]
    assert hashlib.sha256(response.content).hexdigest() == version["content_hash"]
    lines = response.text.splitlines()
    assert len(lines) == 5 and json.loads(lines[0])["id"] == "q1"

    # Re-importing the export reproduces the exact same hash.
    reimport = await api.post(
        "/api/v1/datasets",
        json={"name": "reimported", "dataset_type": "qa", "content": response.text},
    )
    assert reimport.json()["current_version"]["content_hash"] == version["content_hash"]


async def test_preview_filters_and_case_detail(api):
    long_text = "x" * 3000
    records = [*(qa(i) for i in range(1, 6)), qa(6, input=long_text)]
    records.append({"id": "nested/id-7", "type": "qa", "input": "?", "reference_answer": "!"})
    did = (await create(api, records=records)).json()["id"]

    page = (await api.get(f"/api/v1/datasets/{did}/preview?limit=3")).json()
    assert page["total"] == 7 and [c["case_identifier"] for c in page["items"]] == [
        "q1",
        "q2",
        "q3",
    ]
    assert [c["position"] for c in page["items"]] == [0, 1, 2]
    second = (await api.get(f"/api/v1/datasets/{did}/preview?limit=3&offset=3")).json()
    assert second["items"][0]["case_identifier"] == "q4"

    even = (await api.get(f"/api/v1/datasets/{did}/preview?tag=even")).json()
    assert [c["case_identifier"] for c in even["items"]] == ["q2", "q4", "q6"]
    assert (await api.get(f"/api/v1/datasets/{did}/preview?tag=ev")).json()["total"] == 0
    assert (await api.get(f"/api/v1/datasets/{did}/preview?q=Q5")).json()["total"] == 1
    assert (await api.get(f"/api/v1/datasets/{did}/preview?case_type=rag")).json()["total"] == 0

    truncated = next(c for c in even["items"] if c["case_identifier"] == "q6")
    assert truncated["truncated"] is True and len(truncated["content"]["input"]) < 1100

    full = (await api.get(f"/api/v1/datasets/{did}/cases/q6")).json()
    assert full["truncated"] is False and full["content"]["input"] == long_text
    nested = await api.get(f"/api/v1/datasets/{did}/cases/nested/id-7")
    assert nested.status_code == 200 and nested.json()["content"]["reference_answer"] == "!"
    assert (await api.get(f"/api/v1/datasets/{did}/cases/nope")).status_code == 404


async def test_duplicate_keeps_content_hash(api):
    original = (await create(api)).json()
    dup = await api.post(f"/api/v1/datasets/{original['id']}/duplicate", json={})
    assert dup.status_code == 201
    copy = dup.json()
    assert copy["name"] == "qa-set (copy)" and copy["id"] != original["id"]
    assert copy["current_version"]["content_hash"] == original["current_version"]["content_hash"]
    assert copy["current_version"]["record_count"] == 5
    preview = (await api.get(f"/api/v1/datasets/{copy['id']}/preview")).json()
    assert preview["total"] == 5
    again = (await api.post(f"/api/v1/datasets/{original['id']}/duplicate")).json()
    assert again["name"] == "qa-set (copy) 2"


async def test_name_conflicts_and_metadata_update(api):
    first = (await create(api)).json()
    assert (await create(api)).status_code == 409
    other = (await create(api, name="other")).json()
    clash = await api.patch(f"/api/v1/datasets/{other['id']}", json={"name": "qa-set"})
    assert clash.status_code == 409
    updated = await api.patch(
        f"/api/v1/datasets/{first['id']}", json={"description": "new", "tags": ["x", "x", "y"]}
    )
    assert updated.json()["description"] == "new" and updated.json()["tags"] == ["x", "y"]


async def test_samples_install_once(api):
    samples = (await api.get("/api/v1/datasets/samples")).json()
    ids = {s["sample_id"] for s in samples}
    assert {"general-qa", "tool-calling", "rag-grounding"} <= ids
    assert all(s["license"] == "CC0-1.0" and s["installed_dataset_id"] is None for s in samples)

    installed = await api.post("/api/v1/datasets/samples/tool-calling/install")
    assert installed.status_code == 201
    dataset = installed.json()
    assert dataset["source"] == "sample" and dataset["license"] == "CC0-1.0"
    assert dataset["current_version"]["record_count"] == 8
    again = await api.post("/api/v1/datasets/samples/tool-calling/install")
    assert again.status_code == 409
    listed = {s["sample_id"]: s for s in (await api.get("/api/v1/datasets/samples")).json()}
    assert listed["tool-calling"]["installed_dataset_id"] == dataset["id"]
    assert (await api.post("/api/v1/datasets/samples/nope/install")).status_code == 404


async def test_multipart_upload(api):
    files = {"file": ("cases.csv", b"id,input,reference_answer\nq1,Hi?,Hello\n", "text/csv")}
    response = await api.post(
        "/api/v1/datasets",
        data={"name": "from-curl", "dataset_type": "qa", "tags": "a, b"},
        files=files,
    )
    assert response.status_code == 201, response.text
    dataset = response.json()
    assert dataset["tags"] == ["a", "b"]
    assert dataset["current_version"]["source_format"] == "csv"
    assert dataset["current_version"]["source_filename"] == "cases.csv"

    version = await api.post(
        f"/api/v1/datasets/{dataset['id']}/versions",
        files={"file": ("v2.jsonl", jsonl(qa(1), qa(2)).encode(), "application/x-ndjson")},
    )
    assert version.status_code == 201 and version.json()["version_number"] == 2


async def test_upload_limits_and_bad_files(api, migrated_app):
    unsupported = await api.post(
        "/api/v1/datasets/validate",
        files={"file": ("cases.xlsx", b"PK\x00\x03binary", "application/octet-stream")},
    )
    assert unsupported.status_code == 415

    not_utf8 = await api.post(
        "/api/v1/datasets/validate",
        files={"file": ("cases.csv", "id\ncafé\n".encode("latin-1"), "text/csv")},
    )
    assert not_utf8.status_code == 415 and "UTF-8" in not_utf8.json()["error"]["message"]

    migrated_app.state.settings.dataset_max_upload_bytes = 50
    too_big = await create(api)
    assert too_big.status_code == 413
    assert too_big.json()["error"]["code"] == "payload_too_large"

    missing = await api.post("/api/v1/datasets/validate", json={"dataset_type": "qa"})
    assert missing.status_code == 422


async def test_delete_dataset_and_project_cascade(api):
    did = (await create(api)).json()["id"]
    assert (await api.delete(f"/api/v1/datasets/{did}")).status_code == 204
    assert (await api.get(f"/api/v1/datasets/{did}")).status_code == 404

    project = (await api.post("/api/v1/projects", json={"name": "p"})).json()
    scoped = (await create(api, name="scoped", project_id=project["id"])).json()
    assert scoped["project_id"] == project["id"]
    counts = (await api.get(f"/api/v1/projects/{project['id']}")).json()
    assert counts["dataset_count"] == 1
    assert (await api.get(f"/api/v1/datasets?project_id={project['id']}")).json()["total"] == 1
    assert (await api.delete(f"/api/v1/projects/{project['id']}")).status_code == 204
    assert (await api.get(f"/api/v1/datasets/{scoped['id']}")).status_code == 404

    bad_project = await create(api, name="orphan", project_id=project["id"])
    assert bad_project.status_code == 404
