"""Bounded, transactional tenant storage without any telemetry transport.

Callbacks passed to ``transact`` must be deterministic and free of side effects:
Firestore can invoke them more than once while resolving contention.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, TypeVar
from urllib.parse import urlsplit

MAX_STATE_BYTES = 800_000
MAX_ACTIVE_KEYS = 16
RETENTION = timedelta(days=7, minutes=5)
SCHEMA_VERSION = 1
SCOPES = frozenset({"ingest", "read", "manage"})
T = TypeVar("T")


class StorageError(Exception):
    """Base class for controlled storage errors (messages contain no data)."""


class StorageLimit(StorageError):
    """A bounded storage resource has reached its limit."""


class TenantMissing(StorageError):
    """The tenant does not exist, including after concurrent deletion."""


class TenantExists(StorageError):
    """The tenant already exists."""


class KeyConflict(StorageError):
    """A key digest or its public identifier is already registered."""


class Store(Protocol):
    def create_tenant(self, tenant_id: str, key_hash: str, now: datetime) -> None: ...

    def authenticate(self, key_hash: str) -> dict[str, Any] | None: ...

    def add_key(
        self, tenant_id: str, key_hash: str, scopes: list[str], now: datetime,
        *, expected_key_hash: str | None = None,
    ) -> None: ...

    def revoke_key(self, tenant_id: str, key_hash: str, *, expected_key_hash: str | None = None) -> bool: ...

    def list_keys(self, tenant_id: str, *, expected_key_hash: str | None = None) -> list[dict[str, Any]]: ...

    def transact(
        self, tenant_id: str, callback: Callable[[dict[str, Any]], tuple[dict[str, Any], T]],
        *, expected_key_hash: str | None = None,
    ) -> T: ...

    def read_state(self, tenant_id: str, *, expected_key_hash: str | None = None) -> dict[str, Any] | None: ...

    def delete_tenant(self, tenant_id: str, *, expected_key_hash: str | None = None) -> bool: ...

    def health(self) -> bool: ...


def _now() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(UTC)


def _tenant_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("invalid tenant identifier")
    return value


def _digest(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("key digest must be lowercase SHA-256")
    return value


def _key_selector(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"(?:[0-9a-f]{12}|[0-9a-f]{64})", value):
        raise ValueError("invalid key identifier")
    return value


def _scopes(values: list[str]) -> list[str]:
    if not isinstance(values, list) or not values or len(values) > len(SCOPES):
        raise ValueError("invalid key scopes")
    if any(not isinstance(value, str) or value not in SCOPES for value in values):
        raise ValueError("invalid key scopes")
    if len(set(values)) != len(values):
        raise ValueError("duplicate key scope")
    return sorted(values)


def _serialize(state: dict[str, Any]) -> str:
    if not isinstance(state, dict):
        raise ValueError("tenant state must be an object")
    try:
        encoded = json.dumps(state, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as from_error:
        raise ValueError("tenant state must contain finite JSON values") from from_error
    if len(encoded.encode("utf-8")) >= MAX_STATE_BYTES:
        raise StorageLimit("tenant state size limit reached")
    return encoded


def _key_record(tenant_id: str, key_hash: str, scopes: list[str], now: datetime) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "key_id": key_hash[:12],
        "scopes": scopes,
        "active": True,
        "created_at": _utc(now).isoformat(),
    }


class SQLiteStore:
    """Portable local storage. Each operation owns its SQLite connection.

    WAL and a bounded busy timeout coordinate writers across processes. A local
    lock also supports shared in-memory databases, which lack WAL locking.
    """

    def __init__(self, path: str | Path):
        self._lock = threading.RLock()
        self._anchor: sqlite3.Connection | None = None
        self._uri = str(path) == ":memory:"
        if self._uri:
            self._path = f"file:reliamesh-{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._anchor = self._connect()
        else:
            resolved = Path(path).expanduser().resolve()
            resolved.parent.mkdir(parents=True, exist_ok=True)
            self._path = str(resolved)
        with self._lock, self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise StorageError("unsupported database schema version")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS tenants ("
                "tenant_id TEXT PRIMARY KEY, created_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS api_keys ("
                "key_hash TEXT PRIMARY KEY, tenant_id TEXT NOT NULL "
                "REFERENCES tenants(tenant_id) ON DELETE CASCADE, "
                "key_id TEXT NOT NULL, scopes TEXT NOT NULL, created_at TEXT NOT NULL, "
                "UNIQUE(tenant_id, key_id))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS tenant_states ("
                "tenant_id TEXT PRIMARY KEY REFERENCES tenants(tenant_id) ON DELETE CASCADE, "
                "state_json TEXT NOT NULL, updated_at TEXT NOT NULL, expires_at TEXT NOT NULL)"
            )
            connection.execute("CREATE INDEX IF NOT EXISTS keys_tenant ON api_keys(tenant_id)")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS states_expiry ON tenant_states(expires_at)"
            )
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=10.0, uri=self._uri)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA synchronous=FULL")
        # Clear deleted pages; snapshots/backups have their own deletion policy.
        connection.execute("PRAGMA secure_delete=ON")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _require_tenant(connection: sqlite3.Connection, tenant_id: str) -> None:
        if connection.execute(
            "SELECT 1 FROM tenants WHERE tenant_id=?", (tenant_id,)
        ).fetchone() is None:
            raise TenantMissing("tenant does not exist")

    @staticmethod
    def _require_key(
        connection: sqlite3.Connection, tenant_id: str, expected_key_hash: str | None,
    ) -> None:
        if expected_key_hash is not None and connection.execute(
            "SELECT 1 FROM api_keys WHERE tenant_id=? AND key_hash=?",
            (tenant_id, _digest(expected_key_hash)),
        ).fetchone() is None:
            raise TenantMissing("tenant credential is no longer active")

    def create_tenant(self, tenant_id: str, key_hash: str, now: datetime) -> None:
        tenant_id, key_hash, now = _tenant_id(tenant_id), _digest(key_hash), _utc(now)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM tenants WHERE tenant_id=?", (tenant_id,)
            ).fetchone():
                raise TenantExists("tenant already exists")
            if connection.execute(
                "SELECT 1 FROM api_keys WHERE key_hash=?", (key_hash,)
            ).fetchone():
                raise KeyConflict("key already exists")
            connection.execute("INSERT INTO tenants VALUES (?, ?)", (tenant_id, now.isoformat()))
            connection.execute(
                "INSERT INTO api_keys VALUES (?, ?, ?, ?, ?)",
                (key_hash, tenant_id, key_hash[:12], json.dumps(sorted(SCOPES)), now.isoformat()),
            )
            connection.execute(
                "INSERT INTO tenant_states VALUES (?, ?, ?, ?)",
                (tenant_id, "{}", now.isoformat(), (now + RETENTION).isoformat()),
            )
            connection.commit()

    def authenticate(self, key_hash: str) -> dict[str, Any] | None:
        key_hash = _digest(key_hash)
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT k.* FROM api_keys k JOIN tenants t ON k.tenant_id=t.tenant_id "
                "WHERE k.key_hash=?", (key_hash,)
            ).fetchone()
            if row is None:
                return None
            return _key_record(
                row["tenant_id"], key_hash, json.loads(row["scopes"]),
                datetime.fromisoformat(row["created_at"]),
            )

    def add_key(
        self, tenant_id: str, key_hash: str, scopes: list[str], now: datetime,
        *, expected_key_hash: str | None = None,
    ) -> None:
        tenant_id, key_hash = _tenant_id(tenant_id), _digest(key_hash)
        scopes, now = _scopes(scopes), _utc(now)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_tenant(connection, tenant_id)
            self._require_key(connection, tenant_id, expected_key_hash)
            count = connection.execute(
                "SELECT COUNT(*) FROM api_keys WHERE tenant_id=?", (tenant_id,)
            ).fetchone()[0]
            if count >= MAX_ACTIVE_KEYS:
                raise StorageLimit("active key limit reached")
            try:
                connection.execute(
                    "INSERT INTO api_keys VALUES (?, ?, ?, ?, ?)",
                    (key_hash, tenant_id, key_hash[:12], json.dumps(scopes), now.isoformat()),
                )
            except sqlite3.IntegrityError as error:
                raise KeyConflict("key already exists") from error
            connection.commit()

    def revoke_key(self, tenant_id: str, key_hash: str, *, expected_key_hash: str | None = None) -> bool:
        tenant_id, selector = _tenant_id(tenant_id), _key_selector(key_hash)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_key(connection, tenant_id, expected_key_hash)
            # This accepts either a digest or the public 12-character key_id.
            deleted = connection.execute(
                "DELETE FROM api_keys WHERE tenant_id=? AND (key_hash=? OR key_id=?)",
                (tenant_id, selector, selector),
            ).rowcount
            connection.commit()
            return bool(deleted)

    def list_keys(self, tenant_id: str, *, expected_key_hash: str | None = None) -> list[dict[str, Any]]:
        tenant_id = _tenant_id(tenant_id)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_key(connection, tenant_id, expected_key_hash)
            rows = connection.execute(
                "SELECT key_id, scopes, created_at FROM api_keys WHERE tenant_id=? "
                "ORDER BY created_at, key_id LIMIT ?", (tenant_id, MAX_ACTIVE_KEYS)
            ).fetchall()
            return [
                {"key_id": row["key_id"], "scopes": json.loads(row["scopes"]),
                 "created_at": row["created_at"], "active": True}
                for row in rows
            ]

    def transact(
        self, tenant_id: str, callback: Callable[[dict[str, Any]], tuple[dict[str, Any], T]],
        *, expected_key_hash: str | None = None,
    ) -> T:
        tenant_id = _tenant_id(tenant_id)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_tenant(connection, tenant_id)
            self._require_key(connection, tenant_id, expected_key_hash)
            now = _now()
            row = connection.execute(
                "SELECT state_json, expires_at FROM tenant_states WHERE tenant_id=?", (tenant_id,)
            ).fetchone()
            state = (
                json.loads(row["state_json"])
                if row is not None and datetime.fromisoformat(row["expires_at"]) > now else {}
            )
            new_state, result = callback(state)
            encoded = _serialize(new_state)
            connection.execute(
                "INSERT INTO tenant_states VALUES (?, ?, ?, ?) ON CONFLICT(tenant_id) "
                "DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at, "
                "expires_at=excluded.expires_at",
                (tenant_id, encoded, now.isoformat(), (now + RETENTION).isoformat()),
            )
            connection.commit()
            return result

    def read_state(self, tenant_id: str, *, expected_key_hash: str | None = None) -> dict[str, Any] | None:
        tenant_id = _tenant_id(tenant_id)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_key(connection, tenant_id, expected_key_hash)
            exists = connection.execute(
                "SELECT 1 FROM tenants WHERE tenant_id=?", (tenant_id,)
            ).fetchone()
            if not exists:
                return None
            row = connection.execute(
                "SELECT state_json, expires_at FROM tenant_states WHERE tenant_id=?", (tenant_id,)
            ).fetchone()
            if row is None:
                return {}
            if datetime.fromisoformat(row["expires_at"]) <= _now():
                connection.execute("DELETE FROM tenant_states WHERE tenant_id=?", (tenant_id,))
                connection.commit()
                return {}
            return json.loads(row["state_json"])

    def delete_tenant(self, tenant_id: str, *, expected_key_hash: str | None = None) -> bool:
        tenant_id = _tenant_id(tenant_id)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_key(connection, tenant_id, expected_key_hash)
            deleted = connection.execute(
                "DELETE FROM tenants WHERE tenant_id=?", (tenant_id,)
            ).rowcount
            connection.commit()
            return bool(deleted)

    def purge_expired(self) -> int:
        """Physically remove expired local state; safe for scheduled maintenance."""
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            deleted = connection.execute(
                "DELETE FROM tenant_states WHERE tenant_id IN ("
                "SELECT tenant_id FROM tenant_states WHERE expires_at<=? LIMIT 100)",
                (_now().isoformat(),),
            ).rowcount
            connection.commit()
            return deleted

    def health(self) -> bool:
        try:
            with self._lock, self._connection() as connection:
                return connection.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False

    def close(self) -> None:
        if self._anchor is not None:
            self._anchor.close()
            self._anchor = None


def _firestore_module():
    # A SQLite-only installation neither imports a cloud SDK nor reads ADC.
    try:
        from google.cloud import firestore
    except ImportError as error:
        raise StorageError("install the gcp extra to use Firestore") from error
    return firestore


class FirestoreStore:
    """Firestore Native document adapter, restricted to project ``reliamesh``.

    Each tenant metadata document contains at most sixteen key digests. This
    makes deletion a bounded atomic transaction and serializes key additions
    against deletion. Authentication records do not expire with telemetry.
    """

    def __init__(self, project: str = "reliamesh", database: str = "(default)"):
        if project != "reliamesh":
            raise ValueError("Firestore project must be reliamesh")
        if not re.fullmatch(r"(?:\(default\)|[a-z][a-z0-9-]{2,62})", database):
            raise ValueError("invalid Firestore database identifier")
        emulator = os.environ.get("FIRESTORE_EMULATOR_HOST")
        if emulator and urlsplit(f"//{emulator}").hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Firestore emulator must use a loopback host")
        firestore = _firestore_module()
        self._client = firestore.Client(
            project=project, database=database,
            # Override any unrelated quota project embedded in local ADC. The
            # emulator uses anonymous credentials without a quota project API.
            client_options=None if emulator else {"quota_project_id": project},
        )
        self._transactional = firestore.transactional

    def _tenant(self, tenant_id: str):
        return self._client.collection("rm_tenants").document(tenant_id)

    def _key(self, key_hash: str):
        return self._client.collection("rm_keys").document(key_hash)

    def _state(self, tenant_id: str):
        return self._client.collection("rm_states").document(tenant_id)

    def _run(self, callback):
        return self._transactional(callback)(self._client.transaction(max_attempts=5))

    @staticmethod
    def _get(reference, transaction):
        return reference.get(transaction=transaction, retry=None, timeout=10)

    @staticmethod
    def _metadata(snapshot) -> dict[str, Any]:
        if not snapshot.exists:
            raise TenantMissing("tenant does not exist")
        data = snapshot.to_dict()
        if data.get("schema_version") != SCHEMA_VERSION:
            raise StorageError("unsupported tenant schema version")
        hashes = data.get("key_hashes")
        if not isinstance(hashes, list) or len(hashes) > MAX_ACTIVE_KEYS:
            raise StorageError("invalid tenant key metadata")
        return data

    @staticmethod
    def _require_key(metadata: dict[str, Any], expected_key_hash: str | None) -> None:
        if expected_key_hash is not None and _digest(expected_key_hash) not in metadata["key_hashes"]:
            raise TenantMissing("tenant credential is no longer active")

    @staticmethod
    def _state_document(encoded: str, now: datetime) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "state_json": encoded,
            "updated_at": now,
            "expires_at": now + RETENTION,
        }

    @staticmethod
    def _decode_state(snapshot, now: datetime) -> dict[str, Any]:
        if not snapshot.exists:
            return {}
        document = snapshot.to_dict()
        if document.get("schema_version") != SCHEMA_VERSION:
            raise StorageError("unsupported state schema version")
        if document["expires_at"] <= now:
            return {}
        return json.loads(document["state_json"])

    def create_tenant(self, tenant_id: str, key_hash: str, now: datetime) -> None:
        tenant_id, key_hash, now = _tenant_id(tenant_id), _digest(key_hash), _utc(now)
        tenant_ref, key_ref, state_ref = self._tenant(tenant_id), self._key(key_hash), self._state(tenant_id)

        def create(transaction):
            tenant = self._get(tenant_ref, transaction)
            key = self._get(key_ref, transaction)
            if tenant.exists:
                raise TenantExists("tenant already exists")
            if key.exists:
                raise KeyConflict("key already exists")
            transaction.set(tenant_ref, {
                "schema_version": SCHEMA_VERSION, "created_at": now,
                "key_hashes": [key_hash],
            })
            transaction.set(key_ref, _key_record(tenant_id, key_hash, sorted(SCOPES), now))
            transaction.set(state_ref, self._state_document("{}", now))

        self._run(create)

    def authenticate(self, key_hash: str) -> dict[str, Any] | None:
        key_hash = _digest(key_hash)

        def authenticate(transaction):
            snapshot = self._get(self._key(key_hash), transaction)
            if not snapshot.exists:
                return None
            key = snapshot.to_dict()
            tenant = self._get(self._tenant(key["tenant_id"]), transaction)
            if not tenant.exists:
                return None
            metadata = self._metadata(tenant)
            if key_hash not in metadata["key_hashes"] or not key.get("active"):
                return None
            return key

        return self._run(authenticate)

    def add_key(
        self, tenant_id: str, key_hash: str, scopes: list[str], now: datetime,
        *, expected_key_hash: str | None = None,
    ) -> None:
        tenant_id, key_hash = _tenant_id(tenant_id), _digest(key_hash)
        scopes, now = _scopes(scopes), _utc(now)
        tenant_ref, key_ref = self._tenant(tenant_id), self._key(key_hash)

        def add(transaction):
            metadata = self._metadata(self._get(tenant_ref, transaction))
            self._require_key(metadata, expected_key_hash)
            key = self._get(key_ref, transaction)
            hashes = metadata["key_hashes"]
            if len(hashes) >= MAX_ACTIVE_KEYS:
                raise StorageLimit("active key limit reached")
            if key.exists or any(existing[:12] == key_hash[:12] for existing in hashes):
                raise KeyConflict("key already exists")
            metadata["key_hashes"] = [*hashes, key_hash]
            transaction.set(tenant_ref, metadata)
            transaction.set(key_ref, _key_record(tenant_id, key_hash, scopes, now))

        self._run(add)

    def revoke_key(self, tenant_id: str, key_hash: str, *, expected_key_hash: str | None = None) -> bool:
        tenant_id, selector = _tenant_id(tenant_id), _key_selector(key_hash)
        tenant_ref = self._tenant(tenant_id)

        def revoke(transaction):
            tenant = self._get(tenant_ref, transaction)
            if not tenant.exists and expected_key_hash is None:
                return False
            metadata = self._metadata(tenant)
            self._require_key(metadata, expected_key_hash)
            matches = [value for value in metadata["key_hashes"] if value == selector or value[:12] == selector]
            if not matches:
                return False
            key_hash = matches[0]
            metadata["key_hashes"] = [value for value in metadata["key_hashes"] if value != key_hash]
            transaction.set(tenant_ref, metadata)
            transaction.delete(self._key(key_hash))
            return True

        return self._run(revoke)

    def list_keys(self, tenant_id: str, *, expected_key_hash: str | None = None) -> list[dict[str, Any]]:
        tenant_id = _tenant_id(tenant_id)

        def list_keys(transaction):
            tenant = self._get(self._tenant(tenant_id), transaction)
            if not tenant.exists and expected_key_hash is None:
                return []
            metadata = self._metadata(tenant)
            self._require_key(metadata, expected_key_hash)
            records = []
            for key_hash in metadata["key_hashes"]:
                snapshot = self._get(self._key(key_hash), transaction)
                if snapshot.exists:
                    key = snapshot.to_dict()
                    if key["tenant_id"] == tenant_id:
                        records.append({name: key[name] for name in ("key_id", "scopes", "created_at", "active")})
            return sorted(records, key=lambda value: (value["created_at"], value["key_id"]))

        return self._run(list_keys)

    def transact(
        self, tenant_id: str, callback: Callable[[dict[str, Any]], tuple[dict[str, Any], T]],
        *, expected_key_hash: str | None = None,
    ) -> T:
        tenant_id = _tenant_id(tenant_id)
        tenant_ref, state_ref = self._tenant(tenant_id), self._state(tenant_id)

        def update(transaction):
            metadata = self._metadata(self._get(tenant_ref, transaction))
            self._require_key(metadata, expected_key_hash)
            snapshot = self._get(state_ref, transaction)
            now = _now()
            state = self._decode_state(snapshot, now)
            new_state, result = callback(state)
            transaction.set(state_ref, self._state_document(_serialize(new_state), now))
            return result

        return self._run(update)

    def read_state(self, tenant_id: str, *, expected_key_hash: str | None = None) -> dict[str, Any] | None:
        tenant_id = _tenant_id(tenant_id)

        def read(transaction):
            tenant = self._get(self._tenant(tenant_id), transaction)
            if not tenant.exists and expected_key_hash is None:
                return None
            metadata = self._metadata(tenant)
            self._require_key(metadata, expected_key_hash)
            state_ref = self._state(tenant_id)
            snapshot = self._get(state_ref, transaction)
            now = _now()
            state = self._decode_state(snapshot, now)
            if snapshot.exists and snapshot.to_dict()["expires_at"] <= now:
                transaction.delete(state_ref)
            return state

        return self._run(read)

    def delete_tenant(self, tenant_id: str, *, expected_key_hash: str | None = None) -> bool:
        tenant_id = _tenant_id(tenant_id)
        tenant_ref = self._tenant(tenant_id)

        def delete(transaction):
            tenant = self._get(tenant_ref, transaction)
            if not tenant.exists and expected_key_hash is None:
                return False
            metadata = self._metadata(tenant)
            self._require_key(metadata, expected_key_hash)
            for key_hash in metadata["key_hashes"]:
                transaction.delete(self._key(key_hash))
            transaction.delete(self._state(tenant_id))
            transaction.delete(tenant_ref)
            return True

        return self._run(delete)

    def health(self) -> bool:
        try:
            # One fixed document read; no enumeration and no write on a probe.
            self._client.collection("rm_states").document("_health").get(retry=None, timeout=3)
            return True
        except Exception:
            # The service returns a generic unavailable status, never ADC details.
            return False
