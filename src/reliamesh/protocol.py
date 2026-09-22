"""ReliaMesh reliability-event protocol v1. No customer-content fields."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}$")]
Operation = Literal["agent", "model", "tool", "validation"]
FailureType = Literal[
    "agent_error", "model_error", "tool_error", "malformed_output", "validation_failure",
    "timeout", "retry_exhausted", "retry_explosion", "loop_detected", "fallback_activated",
    "task_incomplete", "unknown",
]
VERSION_FIELDS = (
    "provider", "model", "model_version", "framework", "framework_version", "sdk",
    "sdk_version", "tool", "tool_version", "prompt_version", "agent_version",
)
# Opaque customer prompt/agent versions and deployment identities never enter
# cross-deployment fingerprints. Public component labels must be deliberately safe.
PUBLIC_COMPONENT_FIELDS = VERSION_FIELDS[:-2]


class Event(BaseModel):
    """One observed execution outcome; HTTP transport success is irrelevant."""

    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True,
        json_schema_extra={"allOf": [{
            "if": {"properties": {"outcome": {"const": "failure"}}},
            "then": {"required": ["failure_type"], "properties": {"failure_type": {"type": "string"}}},
            "else": {"properties": {"failure_type": {"type": "null"}}},
        }]},
    )

    schema_version: Literal["1.0"] = "1.0"
    event_id: Annotated[str, Field(
        min_length=36, max_length=36,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    )]
    timestamp: datetime
    deployment_id: Identifier
    agent_id: Identifier
    operation: Operation
    outcome: Literal["success", "failure"]
    failure_type: FailureType | None = None
    latency_ms: Annotated[float, Field(ge=0, le=86_400_000, allow_inf_nan=False)] | None = None
    input_tokens: Annotated[int, Field(ge=0, le=1_000_000_000)] | None = None
    output_tokens: Annotated[int, Field(ge=0, le=1_000_000_000)] | None = None
    retry_count: Annotated[int, Field(ge=0, le=1_000_000)] | None = None
    provider: Identifier | None = None
    model: Identifier | None = None
    model_version: Identifier | None = None
    framework: Identifier | None = None
    framework_version: Identifier | None = None
    sdk: Identifier | None = None
    sdk_version: Identifier | None = None
    tool: Identifier | None = None
    tool_version: Identifier | None = None
    prompt_version: Identifier | None = None
    agent_version: Identifier | None = None
    synthetic: bool = False

    @field_validator("event_id")
    @classmethod
    def valid_uuid(cls, value: str) -> str:
        if len(value) != 36:
            raise ValueError("event_id must be a canonical UUID")
        try:
            canonical = str(UUID(value))
        except ValueError as exc:
            raise ValueError("event_id must be a canonical UUID") from exc
        if canonical != value:
            raise ValueError("event_id must be a lowercase canonical UUID")
        return canonical

    @field_validator("timestamp", mode="before")
    @classmethod
    def aware_timestamp(cls, value: object) -> datetime:
        if isinstance(value, str):
            if len(value) > 40 or "T" not in value:
                raise ValueError("timestamp must be an aware ISO8601 datetime")
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("timestamp must be an aware ISO8601 datetime") from exc
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be an aware ISO8601 datetime")
        try:
            return value.astimezone(UTC)
        except (OverflowError, ValueError) as exc:
            raise ValueError("timestamp is outside the supported datetime range") from exc

    @model_validator(mode="after")
    def classified_outcome(self) -> Event:
        if (self.outcome == "failure") != (self.failure_type is not None):
            raise ValueError("failure_type is required exactly when outcome is failure")
        return self


class EventBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    events: Annotated[list[Event], Field(min_length=1, max_length=100)]


def canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def version_metadata(event: Event) -> dict[str, str]:
    return {key: value for key in VERSION_FIELDS if (value := getattr(event, key)) is not None}


def failure_fingerprint(event: Event) -> str | None:
    """SHA256 of classification and intentionally public component identifiers."""
    if event.outcome != "failure":
        return None
    return canonical_hash({
        "fingerprint_version": "1.0", "operation": event.operation,
        "failure_type": event.failure_type,
        **{key: getattr(event, key) for key in PUBLIC_COMPONENT_FIELDS if getattr(event, key)},
    })
