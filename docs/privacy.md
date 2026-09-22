# Privacy and retention

ReliaMesh minimizes data before transmission. Its strict event contract permits
event IDs, timestamps, opaque deployment/agent/version labels, enumerated
operation/outcome/failure classifications, elapsed time, token counts, retry
counts, and an explicit synthetic flag. It has no prompt, response, document,
business record, tool argument/result, stack trace, exception-message, arbitrary
attribute, or customer-content field. Unknown fields are rejected.

The Python SDK's context manager does not inspect returned values or exception
messages. The OpenTelemetry adapter selects a fixed list of attributes and never
copies span events, messages, or arbitrary attributes. Its output is an ordinary
event; exporting it requires an explicit client endpoint and flush.

## Labels are the integrator's responsibility

Allowed identifiers are short ASCII labels. This reduces accidental content
collection but cannot recognize a secret hidden inside a valid identifier. Use
deployment aliases and opaque release IDs; avoid customer names, account numbers,
email addresses, meaningful document names, and raw prompt text. A hash of a
predictable email address or low-entropy prompt is susceptible to guessing and is
not automatically anonymous. Prefer non-content-derived random release IDs.

## Stored state

The engine keeps bounded numerical observation windows, version metadata,
classification fingerprints, duplicate-detection hashes/IDs, cumulative counters,
quota state, and incident history. It does not retain a raw event archive. These
are still tenant-associated operational data: opaque identifiers and aggregates
can reveal activity patterns. They must remain protected.

Observations and incidents are suppressed from reads when older than seven days.
Inactive storage state expires after seven days plus a five-minute replay-protection
grace period for allowed clock skew; that grace does not extend visible observation
history. Window and deduplication state are pruned during ingestion. The operator must
schedule `reliamesh purge` for SQLite or configure Firestore TTL on operational
state for unattended physical cleanup; inactivity alone does not execute
application cleanup. Firestore TTL deletion is asynchronous, while read suppression
applies at the expiry boundary. See [storage operations](storage.md).
Active tenant registration and key digests
persist until deletion/revocation. Purging expired state resets its retained
cumulative statistics as documented by storage behavior. Incident history is also
bounded by count, so it may be shorter than seven days.

Tenant deletion removes the tenant's active application state and credentials.
Storage-engine free pages, cloud-provider recovery copies, operator backups, and
platform access logs may have separate retention periods. Operators must document
and enforce those periods; application deletion cannot promise instant erasure
from all recovery media. Keep backups encrypted, access restricted, and retention
short. A restored backup must not resurrect deleted tenants or revoked keys.

## Transport, logs, and access

Remote SDK endpoints require HTTPS; loopback HTTP is allowed for local development.
Bearer keys are transmitted only to the explicit endpoint and redirects are
rejected. Server authentication stores cryptographic digests of high-entropy
keys. Keys are credentials, even if they only have ingestion scope.

Application validation responses use fixed error descriptions and do not echo
submitted values. Application failure logging omits incoming payloads and
exception strings. Operators must separately configure reverse-proxy, cloud,
and infrastructure logging: those systems may record IP addresses, request paths,
user agents, timing, and status codes. Never place secrets in URLs or enable
request-body logging. ReliaMesh does not add a third-party analytics SDK.

## Self-hosting and network participation

SQLite self-hosting has no outbound telemetry. The SDK has no default cloud
endpoint. Firestore deployments use the explicitly configured Google Cloud storage
service and its normal service APIs; this is not global-network contribution.

Cross-organization network intelligence is disabled in 0.1.0. There is no automatic
participation switch that quietly uploads self-hosted data. A future contribution
feature requires an explicit configuration contract, independently verified
cohorts, suppression thresholds, a documented retention policy, and privacy
review. Aggregation and cohort thresholds alone do not provide differential
privacy or eliminate reidentification risk.

Schema changes that add data collection require a protocol version decision and
privacy review before release. This document describes technical handling; it is
not a substitute for a managed-service privacy notice or customer agreement.
