# Storage contract

ReliaMesh keeps authentication metadata separate from tenant operational state.
The same `Store` interface is implemented by `SQLiteStore` and `FirestoreStore`.
The storage layer performs no telemetry exports, cross-tenant analysis, or
tenant enumeration. It receives only data already validated by the service.

## Atomicity and limits

`transact(tenant_id, callback)` reads one tenant's state, passes a fresh dictionary
to the callback, and commits its returned state atomically. The callback returns
`(new_state, result)`. Exceptions roll back the operation, including quota or
state-size failures. A callback must be deterministic and have no external side
effects: Firestore can call it repeatedly when retrying concurrent transactions.
Generate response identifiers and timestamps before entering the callback when
they must remain identical across retries.

- State must be JSON with finite values, encoded below **800,000 bytes**. A
  rejected update leaves the previous state intact. The limit includes JSON
  escaping and structural overhead.
- Each tenant can have at most **16 active keys**. Key scopes are `ingest`,
  `read`, and `manage`. Tenant creation grants all three to the initial key.
- Only lowercase SHA-256 key digests enter storage. Raw credentials never enter
  the adapter. Listing keys returns scopes, creation time, active status, and a
  12-character digest prefix called `key_id`.
- Revocation accepts either the full digest or `key_id`. Prefix collisions
  within a tenant are rejected; revocation is always tenant-scoped. Revoking a
  key removes its record and frees its slot. There is no unbounded revocation
  history. Revoked credentials fail subsequent authentication.
- Tenant deletion atomically removes state, keys, and tenant metadata. Every
  state write and key addition verifies tenant existence within its transaction,
  preventing a concurrent request from recreating a deleted tenant's data.

API operations also pass `expected_key_hash` to recheck the authenticated key
inside the same transaction as the operation. This prevents an in-flight old key
from accessing a deleted-and-recreated tenant ID or bypassing completed
revocation. Trusted local administrative calls may omit this keyword.

`read_state` returns `None` for an unknown tenant and `{}` for an existing tenant
whose state is empty or expired. `transact` and `add_key` raise `TenantMissing`
for an unknown tenant. Capacity failures raise `StorageLimit`; duplicate tenants
raise `TenantExists`; duplicate key digests or prefixes raise `KeyConflict`.

The initial design serializes writes within each tenant. It is intended for
bounded early deployments, not claimed as a high-throughput event warehouse.
The service must bound event histories, replay caches, cardinality and daily
usage within its state. Sharding is a future migration, not a hidden property
of these adapters.

## Retention and deletion

Operational state has a seven-day plus five-minute inactivity TTL. The five-minute
grace preserves duplicate detection for accepted events whose clocks were ahead
at ingestion; individual observations still expire at seven days. Successful state writes set
`updated_at` and `expires_at`; read operations do not extend retention.
Expired state is treated as empty immediately, independent of physical cleanup.
The detection engine separately prunes each timestamped event/window/incident
at its retention boundary, so ongoing ingestion cannot preserve old entries
merely by refreshing the document TTL. This is an application invariant as well
as a storage setting.

Authentication and tenant metadata do **not** inherit the operational-state TTL.
They remain until explicit key revocation or tenant deletion. Expiring quiet
telemetry must not silently disable a working integration or release its tenant
identity for reuse.

For SQLite, reads of an expired tenant remove its state. `purge_expired()` removes
up to 100 expired states per call for periodic maintenance without waiting for
the tenants to become active. Operators must schedule maintenance while an
installation is idle; logical expiry alone does not physically erase an idle
database. The adapter enables SQLite secure deletion. WAL segments, filesystem
snapshots and backups still have their own lifecycle, so this is not a claim of
instant forensic erasure.

For Firestore, configure TTL on **`rm_states.expires_at`**. The adapter also
deletes expired state when read. TTL cleanup is asynchronous and can lag the
expiry time; application reads suppress expired content immediately. Google
documents that TTL deletions typically occur within 24 hours, and deletion of
expired documents incurs charges. See [Firestore TTL
documentation](https://firebase.google.com/docs/firestore/ttl).

Backups and exports must have an explicit, short operator-controlled lifetime.
Deleting the live tenant does not delete a previously exported backup. A restore
must reapply tenant deletions and expired-state cleanup before reopening traffic.

## SQLite

Use a persistent local filesystem path and mount that directory durably in a
container. SQLite uses WAL, full synchronous writes, foreign-key cascades,
`BEGIN IMMEDIATE` transactions, a 10-second busy timeout, and a separate
connection per operation. Multiple local worker threads and processes coordinate
through SQLite; this is not a multi-host shared-filesystem database. A per-instance
lock also supports `:memory:` for tests. SQLite schema version is currently `1`;
unknown future schema versions are refused rather than overwritten.

Back up a running database with SQLite's online backup API or a documented
SQLite-aware tool. Copying only the main database file while WAL is active is
not a safe backup. Validate restoration with tenant reads and a synthetic event
on an isolated copy before relying on a backup process.

## Firestore

The managed adapter targets a Standard Native Firestore database. Its constructor
explicitly selects project `reliamesh` and database `(default)` unless another
database name within that project is configured. Other project IDs are rejected
before the SDK is loaded. The client also overrides the ADC quota project to
`reliamesh`, so an unrelated workstation quota default is never used. The local
emulator supports only loopback hosts and anonymous credentials. Importing or
using SQLite does not read Application Default Credentials or import the Google SDK.

| Collection | Contents | Expiration |
| --- | --- | --- |
| `rm_tenants` | Schema version, creation time, up to 16 key digests | Explicit deletion |
| `rm_keys` | Tenant ID, digest prefix, scopes, creation time | Explicit revocation/deletion |
| `rm_states` | Schema version, JSON string, update/expiry timestamps | Seven days plus five-minute replay grace |

State is a JSON string rather than a nested indexed map. Disable single-field
indexing for `rm_states.state_json`: no query uses it, and indexing it serves no
product purpose. The adapter only reads exact document paths. Tenant deletion
touches at most 18 documents; it needs no collection scans. Firestore transaction
retries are bounded to five attempts. A readiness check reads one fixed document
with a three-second timeout, performs no writes, and exposes no credential error
details.

Firestore server SDK access uses IAM. Browser/mobile rules do not provide the
tenant isolation of this service. Only the runtime identity should access these
collections; service authentication and scoped API keys enforce tenant access.

## Verification

`python -m pytest tests/test_storage.py tests/test_firestore.py` runs the common
adapter contract against SQLite and a strict Firestore transaction double. The
tests cover rollback, limits, scope handling, tenant isolation, key-prefix
collisions, logical expiry, deletion races, and contention retries. Separate
SQLite instances are exercised concurrently against the same file.

The opt-in emulator test runs when `FIRESTORE_EMULATOR_HOST` is set to a loopback
host, using project `reliamesh`. It creates one randomly named synthetic tenant,
verifies concurrent updates and authentication, and deletes that tenant in a
`finally` block. Without an emulator it is reported as skipped. Unit tests are
not evidence of successful deployment, actual IAM, TTL policy configuration,
or production throughput; those require deployed verification.
