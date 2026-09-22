# ReliaMesh Python SDK

Content-minimizing reliability events for AI agents. Python 3.12+, no runtime
dependencies. Owned by Prefiler Labs Private Limited; Apache-2.0.

Install from this source checkout:

```sh
python -m pip install ./sdk/python
```

```python
import os
from reliamesh_sdk import Client, event

client = Client(
    endpoint=os.environ["RELIAMESH_ENDPOINT"],
    api_key=os.environ["RELIAMESH_API_KEY"],
)
client.emit(event(
    deployment_id="production-eu1", agent_id="invoice-agent",
    operation="validation", outcome="failure",
    failure_type="malformed_output", model="model-alias", prompt_version="v3",
    latency_ms=140, input_tokens=80, output_tokens=30,
))
client.flush()
```

Use opaque stable identifiers; never put customer content, secrets, email
addresses, or raw prompts in identifier fields. The schema restricts shape, not
meaning. Hashing predictable personal data does not make it safe.

## Observe execution

```python
with client.observe(deployment_id="production-eu1", agent_id="invoice-agent",
                    operation="tool", tool="document-validator", tool_version="2"):
    run_tool()  # Your application function; return values are never inspected.
client.flush()
```

The context manager records elapsed time, success or failure, and a generic
exception classification. It never reads the exception message or stack. Original
application exceptions propagate. A timeout is classified as `timeout`; other
exceptions use the operation's generic failure type. Application validation must
emit explicit events for malformed output, loops, fallback, retry escalation, or
incomplete tasks. A successful HTTP call cannot establish task success.

## Delivery contract

- No default endpoint, network discovery, background thread, automatic exporter,
  exit hook, or outbound telemetry. `emit` and `observe` enqueue locally;
  `flush`, `summary`, and `incidents` initiate requests to your chosen endpoint.
- Endpoints require HTTPS, except loopback HTTP for local use. Redirects are
  rejected. TLS uses Python's normal certificate validation.
- Default queue capacity: 1,000 events, at most 100 events and 128 KiB per request. Queue capacity is
  configurable from 1 to 10,000. The queue lives only in memory.
- Default socket timeout: 2 seconds, configurable up to 30 seconds. This bounds
  individual socket operations, not the entire flush. Flush processes only the
  number of events queued at entry, so concurrent producers cannot extend it
  indefinitely.
- Only HTTP 429 and 503 retry, up to two retries by default (maximum three).
  Exponential delays are bounded to one second. Retry bodies and event IDs are
  identical so the server can deduplicate them. Transport errors and other
  statuses are not retried. The client verifies ingestion acknowledgement counts.
- Default `failure_mode="drop"` drops invalid, overflowing, or unsuccessfully sent
  events. `failure_mode="raise"` raises `SDKError`/`DeliveryError`. An attempted
  failed batch is dropped in either mode. Raise mode leaves later batches queued.
- Read `client.counters`: `enqueued`, `sent`, `dropped`, `failed_batches`,
  `retries`, and `queued`. `sent` counts acknowledged input events, including
  deduplicated events. Errors omit response bodies and credentials.
- `summary()` and `incidents()` return decoded authenticated API responses;
  read failures always raise `DeliveryError`.

This is best-effort instrumentation, not durable message delivery. Export on a
dedicated application worker or with `await asyncio.to_thread(client.flush)` in
async applications. Do not call synchronous `flush()` on a latency-sensitive event
loop. In-process queuing and counters are thread-safe; forked processes need their
own client. Drain on a controlled shutdown if losing queued observations matters.

## OpenTelemetry adapter

`from_otel_attributes(attributes, deployment_id=..., agent_id=..., outcome=...)`
constructs an event without exporting it or installing span processors. It maps
selected standard names from the [OpenTelemetry GenAI attribute registry](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/):

| Input | Event field |
| --- | --- |
| `gen_ai.provider.name` | `provider` |
| `gen_ai.request.model` | `model` |
| `gen_ai.usage.input_tokens` / `output_tokens` | `input_tokens` / `output_tokens` |
| `telemetry.sdk.name` / `version` | `sdk` / `sdk_version` |
| `gen_ai.operation.name` | `operation`, via the fixed map below |

`chat`, `text_completion`, `generate_content`, and `embeddings` map to `model`;
`invoke_agent` maps to `agent`; `execute_tool` maps to `tool`. Unknown operations
require an explicit application mapping and raise `ValueError`.

ReliaMesh-specific extensions `reliamesh.model.version`, `reliamesh.prompt.version`,
`reliamesh.agent.version`, `reliamesh.framework.name`, `reliamesh.framework.version`,
`reliamesh.tool.name`, and `reliamesh.tool.version` map to the corresponding flat
event fields. These extensions are not official OpenTelemetry conventions.
The caller explicitly supplies outcome, failure type, duration, and synthetic
status. All unlisted attributes, messages, exception details, and span events are
ignored. Invalid allowlisted values are rejected. Never pass prompt text as a
version identifier.
