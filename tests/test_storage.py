"""Storage contract checks: isolation, atomicity, bounds, and retention."""

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

import reliamesh.storage as storage
from reliamesh.storage import (
    MAX_ACTIVE_KEYS,
    MAX_STATE_BYTES,
    KeyConflict,
    SQLiteStore,
    StorageError,
    StorageLimit,
    TenantExists,
    TenantMissing,
)

NOW = datetime(2026, 9, 21, 12, tzinfo=UTC)


def digest(label):
    return hashlib.sha256(label.encode()).hexdigest()


@pytest.fixture(params=["file", "memory"])
def store(request, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_now", lambda: NOW)
    result = SQLiteStore(tmp_path / "tenant.db" if request.param == "file" else ":memory:")
    yield result
    result.close()


class StorageContract:
    def test_create_and_isolate_tenants(self, store):
        store.create_tenant("a", digest("a"), NOW)
        store.create_tenant("b", digest("b"), NOW)
        assert store.read_state("a") == {}
        assert store.read_state("missing") is None
        store.transact("a", lambda state: ({"counter": 1}, "accepted"))
        assert store.read_state("a") == {"counter": 1}
        assert store.read_state("b") == {}
        key = store.authenticate(digest("a"))
        assert key["tenant_id"] == "a"
        assert key["scopes"] == ["ingest", "manage", "read"]
        assert key["active"] is True
        assert digest("a") not in str(key)
        assert store.authenticate(digest("missing")) is None

    def test_duplicate_creation_never_resets_existing_data(self, store):
        store.create_tenant("a", digest("a"), NOW)
        store.transact("a", lambda state: ({"counter": 2}, None))
        with pytest.raises(TenantExists):
            store.create_tenant("a", digest("new"), NOW)
        with pytest.raises(KeyConflict):
            store.create_tenant("b", digest("a"), NOW)
        assert store.read_state("a") == {"counter": 2}
        assert store.read_state("b") is None
        assert store.authenticate(digest("new")) is None

    def test_key_scope_rotation_and_tenant_scoped_revocation(self, store):
        store.create_tenant("a", digest("a"), NOW)
        store.create_tenant("b", digest("b"), NOW)
        store.add_key("a", digest("ingest"), ["ingest"], NOW)
        assert store.authenticate(digest("ingest"))["scopes"] == ["ingest"]
        keys = store.list_keys("a")
        assert len(keys) == 2
        assert all(set(key) == {"key_id", "scopes", "created_at", "active"} for key in keys)
        assert not store.revoke_key("b", digest("ingest")[:12])
        assert store.revoke_key("a", digest("ingest")[:12])
        assert store.authenticate(digest("ingest")) is None
        assert not store.revoke_key("a", digest("ingest"))
        assert len(store.list_keys("a")) == 1
        assert store.authenticate(digest("b"))["tenant_id"] == "b"

    def test_key_limits_and_collisions_are_atomic(self, store):
        store.create_tenant("a", digest("a"), NOW)
        with pytest.raises(KeyConflict):
            store.add_key("a", digest("a")[:12] + "0" * 52, ["ingest"], NOW)
        for index in range(MAX_ACTIVE_KEYS - 1):
            store.add_key("a", digest(str(index)), ["ingest"], NOW)
        with pytest.raises(StorageLimit):
            store.add_key("a", digest("overflow"), ["read"], NOW)
        assert store.authenticate(digest("overflow")) is None
        assert len(store.list_keys("a")) == MAX_ACTIVE_KEYS
        store.revoke_key("a", digest("0"))
        store.add_key("a", digest("replacement"), ["read"], NOW)
        assert len(store.list_keys("a")) == MAX_ACTIVE_KEYS

    def test_callback_failure_and_limit_leave_old_state_intact(self, store):
        store.create_tenant("a", digest("a"), NOW)
        store.transact("a", lambda state: ({"counter": 4}, None))

        def fail(state):
            state["counter"] = 100
            raise RuntimeError("deliberate test failure")

        with pytest.raises(RuntimeError):
            store.transact("a", fail)
        with pytest.raises(StorageLimit):
            store.transact("a", lambda state: ({"data": "x" * MAX_STATE_BYTES}, None))
        with pytest.raises(ValueError):
            store.transact("a", lambda state: ({"invalid": float("nan")}, None))
        assert store.read_state("a") == {"counter": 4}
        state = store.read_state("a")
        state["counter"] = -1
        assert store.read_state("a") == {"counter": 4}

    def test_state_expires_but_keys_remain(self, store, monkeypatch):
        store.create_tenant("a", digest("a"), NOW)
        store.create_tenant("b", digest("b"), NOW)
        store.transact("a", lambda state: ({"old": 1}, None))
        store.transact("b", lambda state: ({"old": 2}, None))
        monkeypatch.setattr(storage, "_now", lambda: NOW + storage.RETENTION)
        assert store.read_state("a") == {}
        assert store.authenticate(digest("a"))["tenant_id"] == "a"
        assert store.transact("b", lambda state: ({"fresh": True}, state)) == {}
        assert store.read_state("b") == {"fresh": True}

    def test_replay_state_survives_future_clock_skew_grace(self, store, monkeypatch):
        store.create_tenant("a", digest("a"), NOW)
        store.transact("a", lambda state: ({"replay": "synthetic-id"}, None))
        monkeypatch.setattr(storage, "_now", lambda: NOW + timedelta(days=7, minutes=1))
        assert store.read_state("a") == {"replay": "synthetic-id"}
        monkeypatch.setattr(storage, "_now", lambda: NOW + timedelta(days=7, minutes=5))
        assert store.read_state("a") == {}

    def test_tenant_deletion_removes_state_and_all_keys(self, store):
        store.create_tenant("a", digest("a"), NOW)
        store.add_key("a", digest("read"), ["read"], NOW)
        store.transact("a", lambda state: ({"counter": 1}, None))
        assert store.delete_tenant("a")
        assert not store.delete_tenant("a")
        assert store.read_state("a") is None
        assert store.list_keys("a") == []
        assert store.authenticate(digest("a")) is None
        assert store.authenticate(digest("read")) is None
        with pytest.raises(TenantMissing):
            store.transact("a", lambda state: ({}, None))
        with pytest.raises(TenantMissing):
            store.add_key("a", digest("late"), ["read"], NOW)

    def test_invalid_input_is_rejected_before_writing(self, store):
        with pytest.raises(ValueError):
            store.create_tenant("a/b", digest("a"), NOW)
        with pytest.raises(ValueError):
            store.create_tenant("a", "raw-secret-value", NOW)
        with pytest.raises(ValueError):
            store.create_tenant("a", digest("a"), NOW.replace(tzinfo=None))
        store.create_tenant("a", digest("a"), NOW)
        for scopes in ([], ["admin"], ["ingest", "ingest"], ["ingest", {}]):
            with pytest.raises(ValueError):
                store.add_key("a", digest("invalid"), scopes, NOW)
        assert len(store.list_keys("a")) == 1

    @pytest.mark.parametrize("change", ["revoke", "recreate"])
    def test_credential_check_is_part_of_each_atomic_operation(self, store, change):
        old_key, new_key = digest("old-key"), digest("new-key")
        store.create_tenant("a", old_key, NOW)
        if change == "recreate":
            store.delete_tenant("a")
            store.create_tenant("a", new_key, NOW)
        else:
            store.add_key("a", new_key, ["ingest", "read", "manage"], NOW)
            store.revoke_key("a", old_key)
        store.transact("a", lambda state: ({"counter": 7}, None), expected_key_hash=new_key)
        operations = [
            lambda: store.read_state("a", expected_key_hash=old_key),
            lambda: store.list_keys("a", expected_key_hash=old_key),
            lambda: store.transact("a", lambda state: ({"counter": 0}, None), expected_key_hash=old_key),
            lambda: store.add_key("a", digest("injected"), ["read"], NOW, expected_key_hash=old_key),
            lambda: store.revoke_key("a", new_key, expected_key_hash=old_key),
            lambda: store.delete_tenant("a", expected_key_hash=old_key),
        ]
        for operation in operations:
            with pytest.raises(TenantMissing):
                operation()
        assert store.read_state("a", expected_key_hash=new_key) == {"counter": 7}
        assert len(store.list_keys("a", expected_key_hash=new_key)) == 1
        assert store.authenticate(new_key)["tenant_id"] == "a"
        assert store.authenticate(digest("injected")) is None


class TestSQLiteContract(StorageContract):
    pass


def test_concurrent_file_updates_across_instances_do_not_lose_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_now", lambda: NOW)
    path = tmp_path / "concurrent.db"
    stores = [SQLiteStore(path) for _ in range(4)]
    stores[0].create_tenant("a", digest("a"), NOW)

    def increment(index):
        def callback(state):
            state["counter"] = state.get("counter", 0) + 1
            return state, state["counter"]

        return stores[index % len(stores)].transact("a", callback)

    with ThreadPoolExecutor(max_workers=8) as executor:
        values = list(executor.map(increment, range(80)))
    assert sorted(values) == list(range(1, 81))
    assert stores[0].read_state("a") == {"counter": 80}


def test_schema_version_and_wal(tmp_path):
    path = tmp_path / "schema.db"
    SQLiteStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        connection.execute("PRAGMA user_version=999")
    with pytest.raises(StorageError, match="schema version"):
        SQLiteStore(path)


def test_local_purge_removes_expired_state_only(store, monkeypatch):
    store.create_tenant("a", digest("a"), NOW)
    monkeypatch.setattr(storage, "_now", lambda: NOW + timedelta(days=8))
    assert store.purge_expired() == 1
    assert store.read_state("a") == {}
    assert store.authenticate(digest("a"))
    assert store.purge_expired() == 0


def test_delete_race_cannot_leave_orphan_state_or_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_now", lambda: NOW)
    path = tmp_path / "race.db"
    first, second = SQLiteStore(path), SQLiteStore(path)
    first.create_tenant("a", digest("a"), NOW)

    def add(index):
        try:
            second.add_key("a", digest(str(index)), ["ingest"], NOW)
        except TenantMissing:
            pass

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(add, index) for index in range(8)]
        futures.append(executor.submit(first.delete_tenant, "a"))
        for future in futures:
            future.result()
    assert first.read_state("a") is None
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM api_keys").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM tenant_states").fetchone()[0] == 0
