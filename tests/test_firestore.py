"""Firestore adapter tests without credentials, plus an opt-in local emulator check.

The transaction double enforces read-before-write, rollback and optimistic retry.
It is not a replacement for the emulator or deployed Firestore verification.
"""

import copy
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from test_storage import NOW, StorageContract, digest

import reliamesh.storage as storage
from reliamesh.storage import FirestoreStore, TenantMissing


class Snapshot:
    def __init__(self, data):
        self._data = copy.deepcopy(data)
        self.exists = data is not None

    def to_dict(self):
        return copy.deepcopy(self._data)


class Reference:
    def __init__(self, client, path):
        self.client, self.path = client, path

    def get(self, *, transaction=None, **kwargs):
        if self.client.fail_reads:
            raise RuntimeError("synthetic storage outage")
        if transaction:
            assert not transaction.writes, "Firestore forbids reads after writes"
            transaction.reads[self.path] = self.client.versions.get(self.path, 0)
        return Snapshot(self.client.docs.get(self.path))


class Collection:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def document(self, name):
        assert "/" not in name
        return Reference(self.client, f"{self.name}/{name}")


class Transaction:
    def __init__(self, client, max_attempts):
        self.client, self.max_attempts = client, max_attempts
        self.reads, self.writes = {}, {}

    def set(self, reference, data):
        self.writes[reference.path] = copy.deepcopy(data)

    def delete(self, reference):
        self.writes[reference.path] = None


class MemoryFirestore:
    def __init__(self):
        self.docs, self.versions = {}, {}
        self.before_commit = None
        self.fail_reads = False
        self.lock = threading.RLock()

    def collection(self, name):
        assert name in {"rm_tenants", "rm_keys", "rm_states"}
        return Collection(self, name)

    def transaction(self, max_attempts):
        return Transaction(self, max_attempts)

    def write(self, path, value):
        self.versions[path] = self.versions.get(path, 0) + 1
        if value is None:
            self.docs.pop(path, None)
        else:
            self.docs[path] = copy.deepcopy(value)

    def transactional(self, callback):
        def run(transaction):
            with self.lock:
                for _ in range(transaction.max_attempts):
                    transaction.reads, transaction.writes = {}, {}
                    result = callback(transaction)
                    if self.before_commit:
                        hook, self.before_commit = self.before_commit, None
                        hook(self)
                    if any(self.versions.get(path, 0) != version for path, version in transaction.reads.items()):
                        continue
                    for path, value in transaction.writes.items():
                        self.write(path, value)
                    return result
            raise RuntimeError("synthetic transaction contention exhausted")

        return run


@pytest.fixture
def store(monkeypatch):
    client = MemoryFirestore()

    def create_client(*, project, database, client_options):
        assert project == "reliamesh"
        assert database == "(default)"
        assert client_options == {"quota_project_id": "reliamesh"}
        return client

    module = SimpleNamespace(Client=create_client, transactional=client.transactional)
    monkeypatch.setattr(storage, "_firestore_module", lambda: module)
    monkeypatch.setattr(storage, "_now", lambda: NOW)
    monkeypatch.delenv("FIRESTORE_EMULATOR_HOST", raising=False)
    return FirestoreStore()


class TestFirestoreContract(StorageContract):
    pass


def test_other_projects_rejected_before_loading_credentials(monkeypatch):
    def must_not_load():
        pytest.fail("client initialized before project validation")

    monkeypatch.setattr(storage, "_firestore_module", must_not_load)
    for project in ("other", "", "reliamesh/other", "RELIAMESH", None):
        with pytest.raises(ValueError, match="project"):
            FirestoreStore(project=project)


def test_nonlocal_emulators_rejected_before_client_creation(monkeypatch):
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "example.com:8080")
    monkeypatch.setattr(storage, "_firestore_module", lambda: pytest.fail("client constructed"))
    with pytest.raises(ValueError, match="loopback"):
        FirestoreStore()


def test_real_sdk_overrides_inherited_adc_quota_project_without_network(monkeypatch):
    pytest.importorskip("google.cloud.firestore")
    import google.auth
    from google.oauth2.credentials import Credentials

    monkeypatch.delenv("FIRESTORE_EMULATOR_HOST", raising=False)
    synthetic = Credentials(token="synthetic-not-a-credential", quota_project_id="unrelated")
    monkeypatch.setattr(google.auth, "default", lambda **kwargs: (synthetic, "unrelated"))
    instance = FirestoreStore(project="reliamesh")
    assert instance._client.project == "reliamesh"
    assert instance._client._credentials.quota_project_id == "reliamesh"


def test_state_is_a_json_string_with_timestamp_ttl(store):
    store.create_tenant("a", digest("a"), NOW)
    store.transact("a", lambda state: ({"counter": 3}, None))
    document = store._client.docs["rm_states/a"]
    assert isinstance(document["state_json"], str)
    assert json.loads(document["state_json"]) == {"counter": 3}
    assert document["expires_at"] == NOW + storage.RETENTION
    assert "expires_at" not in store._client.docs["rm_tenants/a"]
    assert "expires_at" not in store._client.docs[f"rm_keys/{digest('a')}"]


def test_transaction_retries_use_fresh_state(store):
    store.create_tenant("a", digest("a"), NOW)
    store.transact("a", lambda state: ({"counter": 0}, None))
    observed = []

    def competitor(client):
        document = copy.deepcopy(client.docs["rm_states/a"])
        document["state_json"] = '{"counter":41}'
        client.write("rm_states/a", document)

    def increment(state):
        observed.append(state["counter"])
        state["counter"] += 1
        return state, state["counter"]

    store._client.before_commit = competitor
    assert store.transact("a", increment) == 42
    assert observed == [0, 41]
    assert store.read_state("a") == {"counter": 42}


@pytest.mark.parametrize("operation", ["state", "key"])
def test_deletion_race_cannot_resurrect_tenant(store, operation):
    store.create_tenant("a", digest("a"), NOW)

    def competing_delete(client):
        for path in ("rm_tenants/a", "rm_states/a", f"rm_keys/{digest('a')}"):
            client.write(path, None)

    store._client.before_commit = competing_delete
    with pytest.raises(TenantMissing):
        if operation == "state":
            store.transact("a", lambda state: ({"counter": 1}, None))
        else:
            store.add_key("a", digest("new"), ["ingest"], NOW)
    assert not store._client.docs


def test_health_does_not_enumerate_or_write(store):
    assert store.health()
    assert store._client.docs == {}
    store._client.fail_reads = True
    assert not store.health()


def test_firestore_emulator_concurrent_updates_and_deletion():
    host = os.environ.get("FIRESTORE_EMULATOR_HOST")
    if not host:
        pytest.skip("set FIRESTORE_EMULATOR_HOST for local emulator integration")
    parsed = urlsplit(f"//{host}")
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("integration test requires a loopback Firestore emulator")
    pytest.importorskip("google.cloud.firestore")
    from datetime import UTC, datetime

    instance = FirestoreStore(project="reliamesh", database="(default)")
    tenant_id = f"storage-test-{uuid.uuid4().hex}"
    key_hash = digest(tenant_id)
    instance.create_tenant(tenant_id, key_hash, datetime.now(UTC))

    def increment(_):
        def callback(state):
            state["counter"] = state.get("counter", 0) + 1
            return state, state["counter"]

        return instance.transact(tenant_id, callback)

    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            values = list(executor.map(increment, range(12)))
        assert sorted(values) == list(range(1, 13))
        assert instance.read_state(tenant_id) == {"counter": 12}
        assert instance.authenticate(key_hash)["tenant_id"] == tenant_id
    finally:
        instance.delete_tenant(tenant_id)
    assert instance.read_state(tenant_id) is None
    assert instance.authenticate(key_hash) is None
