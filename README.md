# ReliaMesh

**Open-source reliability infrastructure for AI agents.**

ReliaMesh ingests structured outcomes, detects reliability deterioration, and
tracks recovery without collecting prompts, model responses, or tool content.
An API returning HTTP 200 does not establish that an agent completed its task.

Copyright 2026 **Prefiler Labs Private Limited**. [Apache-2.0](LICENSE).

Canonical repository: [PrefilerLabs/reliamesh](https://github.com/PrefilerLabs/reliamesh).

Managed endpoint: [api.reliamesh.com](https://api.reliamesh.com). Access is provisioned
by the operator; self-hosting needs no managed account.

## What works in 0.1.0

- A versioned, strict reliability-event contract and a Python SDK with no runtime
  dependencies. Explicit outcomes, failure taxonomy, counters, elapsed time,
  opaque version labels, and a whitelist OpenTelemetry attribute adapter.
- Authenticated tenant ingestion with scoped keys, rotation/revocation, bounded
  payloads, per-tenant quotas, duplicate detection, and tenant deletion.
- Deterministic detection of high failure rates, changes in failure rate,
  latency, token consumption, and retries; version-associated observations,
  failure fingerprints, and incident opening/recovery.
- SQLite self-hosting and a Firestore storage adapter for Cloud Run. No outbound
  telemetry from a SQLite self-hosted service.

This is an early release with bounded tenant-local analysis. It does not establish
root cause, infer correctness from model text, or claim calibrated false-positive
rates. Cross-organization network intelligence is disabled; there are no verified
cohort or adoption claims. See [detection behavior](docs/detection.md) and
[operating limits](docs/architecture.md).

## Run locally

Python 3.12 or later is required. Create and activate a virtual environment using
your platform's standard commands, then run from the source checkout:

```sh
python -m pip install -e . -e ./sdk/python
reliamesh tenant create example --output .local/example.json
reliamesh serve
```

The server listens on `http://127.0.0.1:8080`. SQLite stores data at
`.local/reliamesh.db`. Tenant creation writes credentials to the requested file;
keep it private and outside version control. No cloud account is needed.
The provisioning key has ingest/read/manage scopes; create a key limited to
`ingest` for application deployments.

Docker alternative:

```sh
docker compose up --build -d --wait
docker compose exec api reliamesh tenant create example --output /data/example.json
docker compose cp api:/data/example.json .local/example.json
```

Create `.local` before copying the credentials. The API binds only to loopback,
uses a persistent volume and runs as an unprivileged user. Install the local SDK
to run the example against this service. Back up the volume before upgrades.

In a second terminal in the same environment:

```sh
python examples/synthetic_regression.py --endpoint http://127.0.0.1:8080 --credentials .local/example.json
```

The example sends 50 successful baseline observations, induces 50 malformed-output
failures, checks that a regression incident actually opens, then sends 100 healthy
observations and verifies recovery. Every observation and incident is labeled
synthetic. It uses a new deployment ID per run; use a disposable tenant because
each tenant supports 16 active streams. This is a functional exercise, not
evidence about any real provider or customer.

## Integrate

```python
import os
from reliamesh_sdk import Client, event

client = Client(
    endpoint=os.environ["RELIAMESH_ENDPOINT"],
    api_key=os.environ["RELIAMESH_API_KEY"],
)
client.emit(event(
    deployment_id="prod-eu1", agent_id="order-agent",
    operation="validation", outcome="failure", failure_type="malformed_output",
    model="model-alias", prompt_version="release-7", latency_ms=210,
))
client.flush()
```

`emit` only queues an event. `flush` is synchronous, bounded best-effort delivery;
run it on a worker in async or latency-sensitive applications. Watch
`client.counters` for drops. The SDK never chooses a cloud endpoint or exports
silently. Use opaque identifiers; schema validation cannot detect secrets hidden
inside otherwise valid labels. [SDK usage and delivery contract](sdk/python/README.md).

## Inspect and operate

Authenticated `GET /v1/summary` returns window statistics and version metadata;
`GET /v1/incidents` returns incident history. The SDK exposes both methods.
`/openapi.json` describes the HTTP API. `/health` is public liveness;
authenticated `/ready` checks storage availability.

For self-hosting, serve behind HTTPS before allowing remote clients. Application
configuration is explicit: `RM_STORE=sqlite` or `firestore`, `RM_SQLITE_PATH`, and
`RM_DAILY_EVENTS` (default 10,000 per tenant per UTC day). Cloud storage is restricted
to `RM_GCP_PROJECT=reliamesh`. Remote tenant provisioning is disabled unless an
operator sets `RM_ADMIN_HASH`. The local CLI can provision tenants without a remote
admin endpoint.

For SQLite, run `reliamesh purge` on a schedule to physically remove expired operational state.
Reads suppress expired observations after seven days. Active tenant credentials
persist until revoked or deleted. Backups require a separate operator retention
policy. Firestore requires its TTL policy for unattended physical cleanup;
see [storage operations](docs/storage.md) and [privacy and retention](docs/privacy.md).

`reliamesh backup --output .local/backup.db` uses SQLite's consistent backup API
and refuses to overwrite an existing file. Backups contain private tenant data
and key digests. To verify a restore, stop the destination service, place the backup
at a new private path, set `RM_SQLITE_PATH` to that path, start the service, and
compare authenticated summaries before routing traffic. Review deleted tenants
and revoked keys before any production restore.

## Documentation and development

- [Architecture and limits](docs/architecture.md)
- [Managed deployment](docs/deployment.md), [operations](docs/operations.md), and [cost controls](docs/cost-controls.md)
- [Protocol](docs/protocol.md) and [detection](docs/detection.md)
- [Privacy](docs/privacy.md) and [threat model](docs/threat-model.md)
- [Security reporting](SECURITY.md), [contributing](CONTRIBUTING.md), and [changelog](CHANGELOG.md)

```sh
python -m pip install -e ".[dev]" -e ./sdk/python
python -m pytest
python -m ruff check .
```

No payments, blockchain, generative-model dependency, or proprietary hosted
dependency is required for the core reliability path.


