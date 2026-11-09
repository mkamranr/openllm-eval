from __future__ import annotations

import uuid


async def test_project_crud_and_archive(api):
    created = await api.post(
        "/api/v1/projects",
        json={"name": " Local comparison ", "description": "vLLM vs llama.cpp", "tags": ["gpu"]},
    )
    assert created.status_code == 201
    project = created.json()
    assert project["name"] == "Local comparison"
    assert project["archived_at"] is None and project["endpoint_count"] == 0

    pid = project["id"]
    assert (await api.get(f"/api/v1/projects/{pid}")).json()["tags"] == ["gpu"]

    listed = (await api.get("/api/v1/projects")).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == pid

    patched = await api.patch(
        f"/api/v1/projects/{pid}", json={"archived": True, "description": None}
    )
    assert patched.status_code == 200
    assert patched.json()["archived_at"] is not None
    assert patched.json()["description"] is None
    assert (await api.get("/api/v1/projects")).json()["total"] == 0
    assert (await api.get("/api/v1/projects?include_archived=true")).json()["total"] == 1

    restored = await api.patch(f"/api/v1/projects/{pid}", json={"archived": False})
    assert restored.json()["archived_at"] is None

    assert (await api.delete(f"/api/v1/projects/{pid}")).status_code == 204
    missing = await api.get(f"/api/v1/projects/{pid}")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"


async def test_project_validation_and_pagination(api):
    assert (await api.post("/api/v1/projects", json={"name": ""})).status_code == 422
    assert (await api.post("/api/v1/projects", json={"name": "x", "bogus": 1})).status_code == 422
    for i in range(3):
        await api.post("/api/v1/projects", json={"name": f"p{i}"})
    page = (await api.get("/api/v1/projects?limit=2&offset=0")).json()
    assert (page["total"], len(page["items"]), page["limit"]) == (3, 2, 2)
    assert (await api.get(f"/api/v1/projects/{uuid.uuid4()}")).status_code == 404
    assert (await api.get("/api/v1/projects/not-a-uuid")).status_code == 422


async def test_deleting_project_cascades_to_its_endpoints(api):
    pid = (await api.post("/api/v1/projects", json={"name": "temp"})).json()["id"]
    eid = (
        await api.post(
            "/api/v1/endpoints",
            json={
                "project_id": pid,
                "name": "ep",
                "base_url": "http://127.0.0.1:9/v1",
                "model_identifier": "m",
            },
        )
    ).json()["id"]
    assert (await api.get(f"/api/v1/projects/{pid}")).json()["endpoint_count"] == 1
    await api.delete(f"/api/v1/projects/{pid}")
    assert (await api.get(f"/api/v1/endpoints/{eid}")).status_code == 404
