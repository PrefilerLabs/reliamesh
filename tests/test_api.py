from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from reliamesh.api import create_app
from reliamesh.config import Settings
from reliamesh.security import digest_key, new_key
from reliamesh.storage import SQLiteStore


@pytest.fixture
def api(tmp_path):
    store = SQLiteStore(str(tmp_path / "api.db"))
    admin = new_key()
    settings = Settings(admin_hash=digest_key(admin), global_requests_per_second=10000, requests_per_minute=10000)
    client = TestClient(create_app(settings, store))
    return client, store, admin


def tenant(api, name="tenant-a"):
    client, _, admin = api
    response = client.post("/v1/admin/tenants", json={"tenant_id": name}, headers={"Authorization": f"Bearer {admin}"})
    assert response.status_code == 201, response.text
    return {"Authorization": "Bearer " + response.json()["key"]}


def event(**kwargs):
    return {"schema_version": "1.0", "event_id": str(uuid4()), "timestamp": datetime.now(UTC).isoformat(), "deployment_id": "test", "agent_id": "test", "operation": "agent", "outcome": "success", "synthetic": True, **kwargs}


def test_tenant_isolation_and_replay(api):
    client, _, _ = api
    a, b = tenant(api), tenant(api, "tenant-b")
    payload = {"events": [event()]}
    assert client.post("/v1/events", json=payload).status_code == 401
    assert client.post("/v1/events", json=payload, headers=a).json()["accepted"] == 1
    assert client.post("/v1/events", json=payload, headers=a).json()["duplicates"] == 1
    assert client.get("/v1/summary", headers=a).json()["totals"]["accepted"] == 1
    assert client.get("/v1/summary", headers=b).json()["totals"]["accepted"] == 0


def test_privacy_no_echo_or_arbitrary_fields(api):
    client, _, _ = api
    headers = tenant(api)
    secret = "customer-super-private-secret"
    response = client.post("/v1/events", headers=headers, json={"events": [event(prompt=secret)]})
    assert response.status_code == 422
    assert secret not in response.text
    response = client.post("/v1/events", headers=headers, json={secret: secret})
    assert secret not in response.text
    assert client.get("/v1/summary", headers=headers).json()["totals"]["accepted"] == 0


def test_scopes_rotation_revocation_deletion(api):
    client, _, _ = api
    headers = tenant(api)
    issued = client.post("/v1/keys", headers=headers, json={"scopes": ["ingest"]}).json()
    ingest = {"Authorization": "Bearer " + issued["key"]}
    assert client.get("/v1/summary", headers=ingest).status_code == 403
    assert client.post("/v1/keys", headers=ingest, json={"scopes": ["manage"]}).status_code == 403
    assert client.delete("/v1/keys/" + issued["key_id"], headers=headers).status_code == 200
    assert client.post("/v1/events", headers=ingest, json={"events": [event()]}).status_code == 401
    assert client.delete("/v1/tenant", headers=headers).status_code == 400
    assert client.delete("/v1/tenant", headers={**headers, "X-ReliaMesh-Confirm-Delete": "tenant-a"}).json() == {"deleted": True}
    assert client.get("/v1/summary", headers=headers).status_code == 401


def test_request_bounds_and_security_headers(api):
    client, _, _ = api
    headers = tenant(api)
    assert client.post("/v1/events", headers=headers, content="a").status_code == 415
    response = client.post("/v1/events", headers={**headers, "Content-Type": "application/json"}, content="a" * 131073)
    assert response.status_code == 413
    response = client.post("/v1/events", headers={**headers, "Content-Type": "application/json"}, content="[" * 30 + "]" * 30)
    assert response.status_code == 422
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert client.get("/v1/network", headers=headers).json()["incidents"] == []


def test_quota_rollback_is_atomic(tmp_path):
    store = SQLiteStore(str(tmp_path / "quota.db"))
    key = new_key()
    store.create_tenant("tenant-a", digest_key(key), datetime.now(UTC))
    client = TestClient(create_app(Settings(daily_events=1), store))
    headers = {"Authorization": f"Bearer {key}"}
    response = client.post("/v1/events", json={"events": [event(), event()]}, headers=headers)
    assert response.status_code == 429
    assert client.get("/v1/summary", headers=headers).json()["totals"]["accepted"] == 0


def test_cloud_boundary():
    with pytest.raises(ValueError):
        Settings(store="firestore", project="not-authorized")
