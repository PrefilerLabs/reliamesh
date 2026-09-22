"""Regression tests for independently reviewed security/operational failures."""

import asyncio
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from reliamesh.api import SafetyMiddleware, create_app
from reliamesh.cli import backup_sqlite
from reliamesh.config import Settings
from reliamesh.detection import DetectionError, process_events
from reliamesh.protocol import Event
from reliamesh.security import digest_key, new_key
from reliamesh.storage import SQLiteStore


def test_backup_missing_source_fails_without_creating_source_or_output(tmp_path):
    source, output = tmp_path / "missing.db", tmp_path / "backup.db"
    with pytest.raises(sqlite3.OperationalError):
        backup_sqlite(source, output)
    assert not source.exists()
    assert not output.exists()


def test_firestore_purge_rejects_before_loading_cloud_credentials(monkeypatch, capsys):
    import reliamesh.cli as cli

    monkeypatch.setenv("RM_STORE", "firestore")
    monkeypatch.setenv("RM_GCP_PROJECT", "reliamesh")
    monkeypatch.setenv("RM_ADMIN_HASH", "")
    monkeypatch.setenv("RM_DAILY_EVENTS", "10000")
    monkeypatch.setattr(sys, "argv", ["reliamesh", "purge"])

    def unexpected_store(settings):
        pytest.fail("unsupported command must not construct the cloud store")

    monkeypatch.setattr(cli, "make_store", unexpected_store)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert "purge supports SQLite only" in capsys.readouterr().err


def test_backup_rejects_empty_database_and_preserves_existing_output(tmp_path):
    source, output = tmp_path / "empty.db", tmp_path / "existing.db"
    sqlite3.connect(source).close()
    output.write_bytes(b"existing backup must survive")
    with pytest.raises(ValueError, match="supported ReliaMesh"):
        backup_sqlite(source, output)
    assert output.read_bytes() == b"existing backup must survive"


def test_backup_is_restorable_and_never_overwrites(tmp_path):
    source, output = tmp_path / "source.db", tmp_path / "backup.db"
    store = SQLiteStore(source)
    key_hash = digest_key(new_key())
    store.create_tenant("review-tenant", key_hash, datetime.now(UTC))
    store.transact("review-tenant", lambda state: ({"example": "aggregate"}, None))
    backup_sqlite(source, output)
    backup = SQLiteStore(output)
    assert backup.authenticate(key_hash)["tenant_id"] == "review-tenant"
    assert backup.read_state("review-tenant") == {"example": "aggregate"}
    with pytest.raises(FileExistsError):
        backup_sqlite(source, output)
    assert backup.read_state("review-tenant") == {"example": "aggregate"}
    # Closed handles permit rename/delete on Windows after the operation.
    store.close()
    backup.close()
    moved = tmp_path / "renamed.db"
    output.rename(moved)
    moved.unlink()


def test_failed_backup_removes_only_its_incomplete_destination(tmp_path, monkeypatch):
    source, output = tmp_path / "source.db", tmp_path / "backup.db"
    SQLiteStore(source).close()
    original_connect = sqlite3.connect

    class FailingSource:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, *args):
            return self.connection.execute(*args)

        def backup(self, target):
            raise sqlite3.OperationalError("simulated disk failure")

        def close(self):
            self.connection.close()

    def connect(path, **kwargs):
        connection = original_connect(path, **kwargs)
        return FailingSource(connection) if kwargs.get("uri") else connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(sqlite3.OperationalError):
        backup_sqlite(source, output)
    assert source.exists()
    assert not output.exists()


def run_middleware(app):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    async def run():
        await SafetyMiddleware(app, Settings())(
            {"type": "http", "path": "/v1/summary", "method": "GET", "headers": []},
            receive, send,
        )

    return run(), sent


def test_failure_after_response_headers_does_not_restart_or_echo(caplog):
    secret = "private-exception-content"

    async def broken_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise ValueError(secret)

    coroutine, sent = run_middleware(broken_app)
    with pytest.raises(RuntimeError, match="^response_failed$") as error:
        asyncio.run(coroutine)
    assert error.value.__suppress_context__ is True
    assert sum(message["type"] == "http.response.start" for message in sent) == 1
    assert secret not in caplog.text
    assert secret not in repr(sent)


def test_failure_before_response_headers_is_safe_503(caplog):
    async def broken_app(scope, receive, send):
        raise ValueError("private-database-error-content")

    coroutine, sent = run_middleware(broken_app)
    asyncio.run(coroutine)
    assert sent[0]["status"] == 503
    assert json.loads(sent[1]["body"]) == {"error": "temporarily_unavailable"}
    assert "private-database-error-content" not in caplog.text


@pytest.mark.parametrize("timestamp", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"])
def test_out_of_range_timezone_normalization_is_validation_error(timestamp):
    store = SQLiteStore(":memory:")
    key = new_key()
    store.create_tenant("review-tenant", digest_key(key), datetime.now(UTC))
    client = TestClient(create_app(Settings(), store))
    response = client.post("/v1/events", headers={"Authorization": f"Bearer {key}"}, json={
        "events": [{"event_id": "00000000-0000-0000-0000-000000000001", "timestamp": timestamp,
                    "deployment_id": "test", "agent_id": "test", "operation": "agent", "outcome": "success"}],
    })
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"
    assert store.read_state("review-tenant") == {}
    store.close()


def test_storage_clock_skew_grace_preserves_dedup_until_event_expires(tmp_path, monkeypatch):
    import reliamesh.storage as storage

    start = datetime(2026, 9, 1, tzinfo=UTC)
    clock = [start]
    monkeypatch.setattr(storage, "_now", lambda: clock[0])
    store = SQLiteStore(tmp_path / "replay.db")
    store.create_tenant("review-tenant", digest_key(new_key()), start)
    future_event = Event.model_validate({
        "event_id": "00000000-0000-0000-0000-000000000001",
        "timestamp": start + timedelta(minutes=5), "deployment_id": "test",
        "agent_id": "test", "operation": "agent", "outcome": "success",
    })
    store.transact("review-tenant", lambda state: process_events(state, [future_event], clock[0]))
    clock[0] = start + timedelta(days=7, minutes=1)
    retained_state = store.read_state("review-tenant")
    _, receipt = process_events(retained_state, [future_event], clock[0])
    assert receipt == {"accepted": 0, "duplicates": 1}
    clock[0] = start + timedelta(days=7, minutes=5)
    assert store.read_state("review-tenant") == {}
    with pytest.raises(DetectionError, match="event_expired"):
        process_events({}, [future_event], clock[0])
    store.close()


@pytest.mark.parametrize("method,path,payload,storage_method", [
    ("GET", "/v1/summary", None, "read_state"),
    ("GET", "/v1/keys", None, "list_keys"),
    ("POST", "/v1/events", {"events": [{
        "event_id": "00000000-0000-0000-0000-000000000001",
        "deployment_id": "test", "agent_id": "test", "operation": "agent", "outcome": "success",
    }]}, "transact"),
    ("POST", "/v1/keys", {"scopes": ["read"]}, "add_key"),
    ("DELETE", "/v1/keys/000000000000", None, "revoke_key"),
    ("DELETE", "/v1/tenant", None, "delete_tenant"),
])
def test_inflight_old_key_cannot_access_recreated_tenant(tmp_path, monkeypatch, method, path, payload, storage_method):
    store = SQLiteStore(tmp_path / "generation.db")
    old_key, new_owner_key = new_key(), new_key()
    now = datetime.now(UTC)
    store.create_tenant("review-tenant", digest_key(old_key), now)
    original_operation = getattr(store, storage_method)
    original_delete = store.delete_tenant

    def recreate_before_operation(*args, **kwargs):
        original_delete("review-tenant")
        store.create_tenant("review-tenant", digest_key(new_owner_key), datetime.now(UTC))
        return original_operation(*args, **kwargs)

    monkeypatch.setattr(store, storage_method, recreate_before_operation)
    client = TestClient(create_app(Settings(), store))
    if payload and "events" in payload:
        payload = {"events": [{**payload["events"][0], "timestamp": datetime.now(UTC).isoformat()}]}
    response = client.request(method, path, json=payload, headers={
        "Authorization": f"Bearer {old_key}", "X-ReliaMesh-Confirm-Delete": "review-tenant",
    })
    assert response.status_code == 401
    assert response.json() == {"error": "invalid_credentials"}
    assert store.authenticate(digest_key(new_owner_key))["tenant_id"] == "review-tenant"
    assert store.authenticate(digest_key(old_key)) is None
    # Recreated tenant has only its owner's key: no stale manage request grants access.
    with store._connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM api_keys").fetchone()[0] == 1
        assert json.loads(connection.execute("SELECT state_json FROM tenant_states").fetchone()[0]) == {}
    store.close()


def test_openapi_describes_actual_bearer_credentials_and_safe_errors():
    store = SQLiteStore(":memory:")
    client = TestClient(create_app(Settings(), store))
    contract = client.get("/openapi.json").json()
    schemes = contract["components"]["securitySchemes"]
    assert schemes["TenantBearer"]["type"] == "http"
    assert schemes["TenantBearer"]["scheme"] == "bearer"
    assert schemes["AdministratorBearer"]["scheme"] == "bearer"
    assert contract["paths"]["/v1/events"]["post"]["security"] == [{"TenantBearer": []}]
    assert contract["paths"]["/v1/admin/tenants"]["post"]["security"] == [{"AdministratorBearer": []}]
    assert "security" not in contract["paths"]["/"]["get"]
    ingest = contract["paths"]["/v1/events"]["post"]
    assert "ingest scope" in ingest["description"]
    assert ingest["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/IngestReceipt")
    assert ingest["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")
    assert client.get("/v1/summary", headers={"Authorization": "Basic invalid"}).status_code == 401
    assert client.get("/v1/summary").json() == {"error": "invalid_credentials"}
    store.close()
