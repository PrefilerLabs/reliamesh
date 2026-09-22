import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from reliamesh.protocol import Event, EventBatch, failure_fingerprint


def payload(**changes):
    return {
        "event_id": str(uuid4()), "timestamp": datetime.now(UTC).isoformat(),
        "deployment_id": "test-deployment", "agent_id": "assistant", "operation": "agent",
        "outcome": "success", **changes,
    }


def test_json_and_python_validation_agree():
    data = payload()
    assert Event.model_validate(data) == Event.model_validate_json(json.dumps(data))
    assert Event.model_validate(data).synthetic is False


@pytest.mark.parametrize("field", ["prompt", "response", "attributes", "exception_message", "tool_arguments", "authorization", "http_status"])
def test_content_and_unrecognized_fields_are_rejected(field):
    with pytest.raises(ValidationError):
        Event.model_validate(payload(**{field: "customer content"}))


@pytest.mark.parametrize("changes", [
    {"outcome": "failure"}, {"failure_type": "timeout"}, {"failure_type": "misc", "outcome": "failure"},
    {"event_id": "not-an-id"}, {"agent_id": "has whitespace"}, {"agent_id": "x" * 65},
    {"agent_id": "private@example.com"}, {"timestamp": "2026-01-01T00:00:00"},
    {"timestamp": 1_750_000_000}, {"latency_ms": float("nan")}, {"latency_ms": -1},
    {"input_tokens": True}, {"retry_count": "3"}, {"synthetic": "false"},
    {"schema_version": "2.0"}, {"operation": "arbitrary"},
])
def test_strict_bounded_schema(changes):
    with pytest.raises(ValidationError):
        Event.model_validate(payload(**changes))


def test_fingerprint_omits_tenant_agent_and_private_versions():
    first = Event.model_validate(payload(outcome="failure", failure_type="malformed_output", model="public-model", model_version="v1"))
    second = Event.model_validate(payload(outcome="failure", failure_type="malformed_output", model="public-model", model_version="v1", deployment_id="another", agent_id="other", prompt_version="private-hash", agent_version="private-version"))
    assert failure_fingerprint(first) == failure_fingerprint(second)
    assert failure_fingerprint(first) != failure_fingerprint(first.model_copy(update={"model_version": "v2"}))
    assert len(failure_fingerprint(first)) == 64
    assert failure_fingerprint(Event.model_validate(payload())) is None


def test_batch_limits_and_extra_fields():
    with pytest.raises(ValidationError):
        EventBatch(events=[])
    with pytest.raises(ValidationError):
        EventBatch(events=[Event.model_validate(payload())] * 101)
    with pytest.raises(ValidationError):
        EventBatch.model_validate({"events": [payload()], "tenant_id": "spoofed"})


def test_committed_schema_matches_runtime():
    from pathlib import Path
    schema = Path(__file__).resolve().parents[1] / "schemas" / "reliability-event-v1.schema.json"
    assert json.loads(schema.read_text()) == Event.model_json_schema()
