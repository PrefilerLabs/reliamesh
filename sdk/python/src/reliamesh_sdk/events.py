"""Schema 1.0 event construction without third-party dependencies."""

import math
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}\Z")
OPERATIONS = frozenset({"agent", "model", "tool", "validation"})
FAILURE_TYPES = frozenset({
    "agent_error", "model_error", "tool_error", "malformed_output", "validation_failure",
    "timeout", "retry_exhausted", "retry_explosion", "loop_detected", "fallback_activated",
    "task_incomplete", "unknown",
})
IDENTIFIERS = frozenset({
    "deployment_id", "agent_id", "provider", "model", "model_version", "framework",
    "framework_version", "sdk", "sdk_version", "tool", "tool_version", "prompt_version",
    "agent_version",
})
COUNTERS = {"input_tokens": 10**9, "output_tokens": 10**9, "retry_count": 10**6}
REQUIRED = frozenset({"schema_version", "event_id", "timestamp", "deployment_id", "agent_id",
                      "operation", "outcome"})
FIELDS = REQUIRED | IDENTIFIERS | COUNTERS.keys() | {"failure_type", "latency_ms", "synthetic"}


def validate_event(value: Mapping) -> dict:
    """Copy and validate an event; error text never includes supplied values."""
    if not isinstance(value, Mapping) or set(value) - FIELDS or REQUIRED - set(value):
        raise ValueError("event has missing or unsupported fields")
    result = dict(value)
    if result["schema_version"] != "1.0":
        raise ValueError("unsupported event schema version")
    try:
        if not isinstance(result["event_id"], str):
            raise ValueError
        result["event_id"] = str(UUID(result["event_id"]))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("event_id must be a UUID") from None
    try:
        timestamp = datetime.fromisoformat(result["timestamp"])
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError
        result["timestamp"] = timestamp.astimezone(UTC).isoformat()
    except (ValueError, TypeError, OverflowError):
        raise ValueError("timestamp must be an ISO 8601 time with timezone") from None
    for key in IDENTIFIERS & result.keys():
        if not isinstance(result[key], str) or not IDENTIFIER.fullmatch(result[key]):
            raise ValueError("event identifiers must use 1-64 safe ASCII characters")
    if not isinstance(result["operation"], str) or result["operation"] not in OPERATIONS:
        raise ValueError("unsupported operation")
    if result["outcome"] not in ("success", "failure"):
        raise ValueError("unsupported outcome")
    failure = result.get("failure_type")
    if result["outcome"] == "failure":
        if not isinstance(failure, str) or failure not in FAILURE_TYPES:
            raise ValueError("failure outcome requires a supported failure_type")
    elif failure is not None:
        raise ValueError("success outcome must not include a failure_type")
    for key, maximum in COUNTERS.items():
        if key in result and (type(result[key]) is not int or not 0 <= result[key] <= maximum):
            raise ValueError("event counter is outside allowed bounds")
    if "latency_ms" in result:
        latency = result["latency_ms"]
        if (type(latency) not in (int, float) or not 0 <= latency <= 86_400_000
                or not math.isfinite(latency)):
            raise ValueError("latency_ms is outside allowed bounds")
    if "synthetic" in result and type(result["synthetic"]) is not bool:
        raise ValueError("synthetic must be boolean")
    return result


def event(*, deployment_id: str, agent_id: str, operation: str, outcome: str,
          failure_type: str | None = None, timestamp: datetime | None = None,
          event_id: str | None = None, synthetic: bool = False, **measurements) -> dict:
    """Construct a strict protocol event. Unknown metadata is rejected, never exported."""
    result = {
        "schema_version": "1.0", "event_id": event_id or str(uuid4()),
        "timestamp": (timestamp if timestamp is not None else datetime.now(UTC)).isoformat(),
        "deployment_id": deployment_id, "agent_id": agent_id,
        "operation": operation, "outcome": outcome, "synthetic": synthetic,
    }
    if failure_type is not None:
        result["failure_type"] = failure_type
    if set(measurements) & result.keys():
        raise ValueError("duplicate event fields")
    result.update(measurements)
    return validate_event(result)


_OTEL_FIELDS = {
    "gen_ai.provider.name": "provider",
    "gen_ai.request.model": "model",
    "gen_ai.usage.input_tokens": "input_tokens",
    "gen_ai.usage.output_tokens": "output_tokens",
    "telemetry.sdk.name": "sdk",
    "telemetry.sdk.version": "sdk_version",
    "reliamesh.model.version": "model_version",
    "reliamesh.prompt.version": "prompt_version",
    "reliamesh.agent.version": "agent_version",
    "reliamesh.framework.name": "framework",
    "reliamesh.framework.version": "framework_version",
    "reliamesh.tool.name": "tool",
    "reliamesh.tool.version": "tool_version",
}
_OTEL_OPERATIONS = {
    "chat": "model", "text_completion": "model", "generate_content": "model",
    "embeddings": "model", "invoke_agent": "agent", "execute_tool": "tool",
}


def from_otel_attributes(attributes: Mapping, *, deployment_id: str, agent_id: str,
                         outcome: str, failure_type: str | None = None,
                         latency_ms: float | None = None, synthetic: bool = False) -> dict:
    """Pure whitelist mapping; does not export, inspect spans, or capture content.

    `reliamesh.*` names are local extensions, not official OTel conventions.
    The caller explicitly supplies the outcome and failure classification.
    """
    operation = attributes.get("gen_ai.operation.name")
    if not isinstance(operation, str) or operation not in _OTEL_OPERATIONS:
        raise ValueError("unsupported OpenTelemetry operation; map it explicitly")
    selected = {target: attributes[source] for source, target in _OTEL_FIELDS.items()
                if source in attributes}
    if latency_ms is not None:
        selected["latency_ms"] = latency_ms
    return event(deployment_id=deployment_id, agent_id=agent_id,
                 operation=_OTEL_OPERATIONS[operation], outcome=outcome,
                 failure_type=failure_type, synthetic=synthetic, **selected)
