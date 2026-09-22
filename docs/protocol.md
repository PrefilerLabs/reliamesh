# Reliability event protocol 1.0

Owned by Prefiler Labs Private Limited. The protocol and implementation are Apache-2.0.
The machine-readable contract is [reliability-event-v1.schema.json](../schemas/reliability-event-v1.schema.json).
The API accepts `{"events": [...]}` with 1–100 events per batch. Unknown fields
are rejected at every model boundary. A failed batch is atomic.

Required fields:

| Field | Contract |
| --- | --- |
| `event_id` | Canonical lowercase UUID; generate once and retain across retries. |
| `timestamp` | ISO8601 timestamp with timezone, normalized to UTC. |
| `deployment_id`, `agent_id` | Customer-selected opaque identifiers, at most 64 characters. |
| `operation` | `agent`, `model`, `tool`, or `validation`. |
| `outcome` | `success` or `failure`, representing the observed execution outcome. |

`schema_version` defaults to `1.0`; other values are rejected. `synthetic` defaults
to `false`; tests, demonstrations, load generation and induced failures must
explicitly set it to `true`. Synthetic events use separate detection streams.
`failure_type` is required when outcome is failure, and must be absent/null for
success. An HTTP 200 response with invalid structured output is a failure.

| Failure type | Meaning |
| --- | --- |
| `agent_error` | Agent execution could not complete. |
| `model_error` | Model/provider operation failed. |
| `tool_error` | Tool invocation failed. |
| `malformed_output` | Output could not be parsed into the required structure. |
| `validation_failure` | Parsed output failed a caller-defined validator. |
| `timeout` | Operation exceeded its time allowance. |
| `retry_exhausted` | Retry policy exhausted its allowance. |
| `retry_explosion` | Caller-defined retry threshold exceeded. |
| `loop_detected` | Caller detected repeated non-progressing execution. |
| `fallback_activated` | Caller declares fallback a materially degraded outcome. |
| `task_incomplete` | Caller determined the requested task was not completed. |
| `unknown` | Failure observed without a more specific classification. |

The server does not read outputs to infer these classifications. Integrators must
provide local validation and correct outcome classification. In particular, loop
and task-completion detection require a local application signal. Classification
records are evidence of caller reports, not independently verified ground truth.

Optional measurements are finite `latency_ms` (0–86,400,000), integer
`input_tokens` and `output_tokens` (each 0–1,000,000,000), and integer `retry_count`
(0–1,000,000). Missing measurements remain unknown; they do not become zeros.
Total-token detection requires both input and output measurements on each event.
Boolean values cannot masquerade as integer counts.

Optional version labels are `provider`, `model`, `model_version`, `framework`,
`framework_version`, `sdk`, `sdk_version`, `tool`, `tool_version`, `prompt_version`,
and `agent_version`. Every identifier/label must match
`[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}`. Use public component names and opaque customer
version IDs or hashes. Identifiers cannot contain whitespace, email addresses,
query strings or arbitrary JSON. Syntactic checks cannot prove a label contains
no secret: never put content or credentials in these fields.

There are deliberately no prompt, response, message, exception text, trace text,
arbitrary attributes, tool arguments/results, headers, or content-hash fields.
Do not hash raw prompts into event metadata: a hash of guessable content can leak
information. Prefer independent version IDs or customer-controlled build hashes.

## Fingerprints and version comparisons

Failure fingerprints are SHA256 over a canonical JSON object containing
`fingerprint_version: "1.0"`, operation, failure classification, and available
public component labels (`provider` through `tool_version` in the list above).
Keys are lexically sorted, JSON uses ASCII escaping and compact separators, and
UTF-8 bytes are hashed. Prompt/agent version IDs, tenant/deployment/agent identity,
timestamps, measurements, and event IDs are excluded. A success has no fingerprint.
Fingerprints identify matching reported classifications; they do not establish
that failures share a cause. Public labels must be safe to aggregate deliberately.

Within a tenant, each complete version-label set also has a SHA256 identifier,
including opaque prompt/agent versions. Summary baseline/current `version_counts`
map these IDs to counts; the stream's `versions` map provides labels for currently
retained samples. Historical incidents retain version IDs after labels age out.
Version changes do not split streams. This allows temporal comparisons across
versions, without asserting that a version change caused a regression.

## Time, retries, and bounds

The service accepts event time strictly newer than server time minus seven days,
and no later than server time plus five minutes. Within a batch events are sorted by timestamp and UUID.
Across requests each stream requires monotonically nondecreasing timestamps;
older unseen events fail with `late_event`. This intentionally favors a bounded,
reproducible streaming detector over arbitrary historical backfill. Arrange each
stream's events in time order and flush older batches before newer ones.

The last 2,048 event IDs per tenant are retained with full SHA256 payload digests.
Exact retries count as duplicates. Reusing an ID with a changed normalized payload
fails with `event_id_conflict` while the ID is retained. Eviction advances a tenant
timestamp replay floor: unseen events at or below that floor fail with
`stale_replay`, preventing evicted events from being counted again. This can also
reject legitimate delayed events, including events from slower streams, and limits
the number of distinct accepted events with identical timestamps. Use accurate,
high-resolution timestamps. IDs older than retention fail `event_expired`.
An attacker with valid credentials can change both ID and time; this is not an
attestation system.

The caller's authenticated credential defines the tenant; there is no client
`tenant_id` field. Limits are 16 streams per tenant, eight version sets currently
referenced by a stream's windows, and 650,000 serialized state bytes. Limit errors
reject a batch, without evicting an unrelated active stream or silently sampling.
These are deliberate initial deployment bounds, not a high-volume service claim.

## Python service contract

`reliamesh.protocol.Event.model_validate(mapping)` validates a single event.
`EventBatch.model_validate({"events": [...]})` validates a request body.
`failure_fingerprint(event)` returns a lowercase SHA256 hex string or `None`.
The service uses Pydantic; the separately distributed SDK has only standard-library
runtime dependencies. Schema additions require explicit privacy review; breaking
changes require a new protocol version.
