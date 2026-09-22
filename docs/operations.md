# Operations

All managed resources belong to Google Cloud project **reliamesh**. Cloud commands
must explicitly target that project. Runtime authentication uses a dedicated
service account; no service account keys are needed or distributed.

## Service and data

- Cloud Run `reliamesh-api`, region `asia-south1`: one CPU, 512 MiB, concurrency 16,
  zero minimum and two maximum instances, 30-second request timeout.
- Firestore Standard Native `(default)`, `asia-south1`, deletion protection and
  seven-day point-in-time recovery. Runtime role: project `roles/datastore.user`.
- Artifact Registry repository `reliamesh` in `asia-south1`.
- Secret Manager `reliamesh-admin-hash`: SHA256 administrator key digest.
  The runtime may access only this secret. Plaintext administrative credentials
  are delivered separately, never in Git, images, logs, or documentation.
- Authentication is enforced inside the API; Cloud Run's HTTP endpoint is public
  so SDKs can connect. No public signup. Operators provision tenants.

Use `infra/deploy.ps1 -Image <registry-image@sha256:digest>` after checks pass.
The script refuses other registries/projects and mutable image tags. Set the
CLI configuration/account explicitly on shared workstations. CI uses workload
identity with repository and branch restrictions, never a service-account key.

## Keys and access

Bootstrap a local installation with `reliamesh tenant create example --output
.local/example.json`. For managed provisioning use the admin bearer key with
`POST /v1/admin/tenants` and `{ "tenant_id": "example" }`. The response returns
the tenant key once. Keep it private. Mint a narrower `ingest` key for agent
processes using `POST /v1/keys`. Keys authorize only their tenant and scopes;
caller-supplied tenant IDs cannot redirect ingestion or reads.

Rotation: create a replacement key, update clients, verify receipt, revoke the
old key using `DELETE /v1/keys/{key_id}`. Revocation affects subsequent
authentication; an already authenticated in-flight request may finish.
Administrative rotation requires generating a new key/digest, adding a Secret
Manager version and deploying that exact version. Retire old revisions before
disabling the old secret version. The administrator key provisions tenants;
it cannot read tenant data through the public API.

## Retention, deletion, and recovery

See [privacy](privacy.md) and [storage](storage.md). Per-entry telemetry histories
are pruned at seven days and inactive state expires after seven days plus a
five-minute replay-protection grace period for accepted clock skew. Firestore
TTL on `rm_states.expires_at` performs asynchronous physical cleanup; the API
suppresses expired state immediately. `state_json` indexing is disabled.
Authentication metadata remains until revocation or tenant deletion.

Tenant deletion requires a manage key and matching
`X-ReliaMesh-Confirm-Delete` header. It atomically removes current credentials,
metadata and operational state. Encrypted managed recovery history can retain
deleted data for up to seven additional days. Never restore a deleted tenant
without separately reconciling deletion requests and revocations.

SQLite: run `reliamesh purge` periodically, repeating while 100 rows are removed.
Create a consistent backup with `reliamesh backup --output <private-new-file>`.
Stop the service before restoring a backup file to `RM_SQLITE_PATH`; preserve
permissions and reconcile key revocations/deletions after the backup time.
Backups contain sensitive tenant identifiers and key digests. Encrypt backups
at rest and delete them after seven days. Test restores before relying on them.

Firestore PITR is configured; restoring a timestamp to a separate database in
the **same** project is an operator recovery procedure, not an automatic failover.
Review tenant deletions and key revocations before switching service databases.
Never restore or export into another project.

## Incident response and limits

`/health` proves process availability. Authenticated `/ready` checks storage.
SDK `summary()` proves a tenant's instrumentation is being ingested; synthetic
tests use `synthetic=true` and remain separate from real streams.

On elevated 5xx, check fixed request IDs and Cloud Run revision/error counters;
never add payload or credential logging. Roll back traffic to the last verified
revision and investigate storage permissions, quota, document bounds and latency.
Detection incidents represent tenant agent behavior, not necessarily a ReliaMesh
service outage; insufficient samples are not a healthy reliability claim.

Limits include 128 KiB/request, 100 events/batch, 16 streams/tenant, 16 active keys,
10,000 newly accepted events/tenant/UTC day, bounded state and process request
limits. The daily event cap is transactional across instances. Process request
limits are intentionally approximate across instances. Limits do **not** create
a monetary hard cap, and public requests still consume platform resources.

Logging retention is seven days. Cloud Run platform request logs include network
metadata and URLs, but not the telemetry body or Authorization header. Application
access logging is disabled. Do not put private information in URLs or opaque IDs.

Cloud Monitoring runs an external HTTPS availability check every five minutes.
Policies cover multi-location availability failures, server errors and unexpected
sustained request volume. Alerts are visible in the project's Monitoring console;
outbound notification channels are not configured. Configure an approved operator
notification destination before relying on unattended incident response.



