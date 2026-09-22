# Threat model

Assets are tenant telemetry and derived incidents, tenant keys, administrative
credentials, source/package integrity, and the authorized cloud project's budget.
Trust boundaries are the application-to-SDK mapping, public HTTP edge, tenant
credential lookup, transactional storage, release pipeline, and cloud control
plane. All clients and submitted metadata are untrusted.

| Threat | Implemented boundary | Remaining operator responsibility / limitation |
| --- | --- | --- |
| Fake telemetry and incident manipulation | Explicit synthetic flag; strict schema; tenant-scoped analysis; bounded contribution volumes | An authorized client can lie about its outcomes. Local incidents are observations, not attestations. |
| Sybil contributors and public-status manipulation | Network publication is disabled; no tenant identifier is accepted as proof of an independent organization | Independent verification, explicit consent, cohort review, and publication controls are required before enabling a network. |
| Stolen keys | High-entropy bearer credentials stored as digests; ingest/read/manage scopes; key revocation and rotation | Protect credential files and secret stores. TLS and short-lived operational exposure do not undo theft. Revoke compromised credentials. |
| Cross-tenant leakage / enumeration | Tenant selected by authenticated key; strict event contract contains no client-selected tenant field; fixed unauthorized errors | Administrative keys can provision tenants; cloud/storage administrators are privileged and must be restricted. |
| Replay or event-ID tampering | Transactional deduplication; changed payload under the same ID rejected; bounded replay horizon and timestamp checks | Replay protection rejects stale submissions after dedup eviction; delivery is best effort, not an unlimited event history. |
| Denial of service / cloud-cost attacks | Request size, JSON depth, batch, stream, version, state, queue, and quota bounds; process-local request throttles | Anonymous requests still incur edge/runtime work; per-process throttles are not distributed quotas. Keep instance bounds and inspect usage. |
| Schema bombs and content collection | Unknown fields rejected; enum/range validation; SDK whitelist; generic validation errors | Secret-like text can fit a valid label. Integrators must use opaque aliases. Edge logging must omit bodies. |
| Credential exfiltration by redirects | SDK HTTPS requirement and redirect refusal | Compromised application hosts, trusted proxies, DNS, or root CAs remain outside the SDK's guarantee. |
| Concurrent writes / partial ingestion | Storage transactions; deterministic callback; atomic batch decisions and persisted quotas | Firestore contention limits per-tenant throughput; SQLite needs a reliable persistent filesystem. |
| Compromised dependencies, packages, or CI | Minimal runtime dependencies; zero-dependency SDK; reviewable source and release checks | Review dependency changes, protect release credentials, enable repository protections, and verify release artifacts. No build system is an absolute trust guarantee. |
| Administrative compromise | Explicit project boundary; no application feature accesses another cloud project; remote provisioning disabled without admin configuration | Project administrators and CI identities need least privilege, audit review, and recovery procedures. |
| Data retention or backup resurrection | Seven-day read suppression, bounded state, purge command, tenant deletion | Schedule physical cleanup; define backup retention; do not restore revoked credentials or deleted tenants. |

## Security assumptions

The operator controls the host and reverse proxy and terminates remote traffic
with HTTPS. Local SQLite files and credential outputs are trusted private files.
The managed deployment uses Google Cloud project `reliamesh` only. Cloud identity
and platform encryption protect storage; application analysis is not end-to-end
encrypted from the service itself. Server compromise can expose retained tenant
metadata and can alter incidents.

The release does not claim formal anonymization, certified compliance, automatic
fraud detection, externally calibrated confidence, or an independent security
audit. Review security-sensitive changes and exercise incident response, key
rotation, deletion, and restore procedures before onboarding sensitive production
workloads. Report vulnerabilities according to [SECURITY.md](../SECURITY.md).
