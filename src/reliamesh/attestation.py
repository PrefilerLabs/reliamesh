"""Opt-in, content-minimized off-chain reports and salted Devnet commitments.

No RPC, wallet, or background work happens in this module. A report is an
operator assertion, not an independently established measurement.
"""

import hashlib
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEVNET_GENESIS = "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG"
MAX_DOCUMENT_BYTES = 16_384
Count = Annotated[int, Field(strict=True, ge=0, le=800)]


class WindowReport(BaseModel):
    """Counts of retained sample windows, never lifetime or customer content."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal["reliamesh-window-report/v1"] = "reliamesh-window-report/v1"
    telemetry_schema: Literal["1.0"] = "1.0"
    server_version: Annotated[str, Field(pattern=r"^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$")]
    generated_at: str
    synthetic: bool
    stream_count: Annotated[int, Field(strict=True, ge=0, le=16)]
    baseline_samples: Count
    baseline_failures: Count
    current_samples: Count
    current_failures: Count
    open_incidents: Annotated[int, Field(strict=True, ge=0, le=32)]
    resolved_incidents: Annotated[int, Field(strict=True, ge=0, le=32)]
    expired_incidents: Annotated[int, Field(strict=True, ge=0, le=32)]

    @field_validator("generated_at")
    @classmethod
    def timestamp(cls, value):
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError
            canonical = parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
            if value != canonical:
                raise ValueError
        except (ValueError, OverflowError):
            raise ValueError("generated_at must use UTC YYYY-MM-DDTHH:MM:SS.ffffffZ") from None
        return value

    @model_validator(mode="after")
    def counts(self):
        if self.baseline_failures > self.baseline_samples or self.current_failures > self.current_samples:
            raise ValueError("failures exceed samples")
        if max(self.baseline_samples, self.current_samples) > self.stream_count * 50:
            raise ValueError("samples exceed stream capacity")
        if self.open_incidents + self.resolved_incidents + self.expired_incidents > 32:
            raise ValueError("incidents exceed history capacity")
        return self


class CommitmentBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal["reliamesh-devnet-commitment/v1"] = "reliamesh-devnet-commitment/v1"
    network: Literal["solana-devnet"] = "solana-devnet"
    genesis_hash: Literal[DEVNET_GENESIS] = DEVNET_GENESIS
    expected_signer: Annotated[str, Field(pattern=r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")]
    salt_hex: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    report: WindowReport


def canonical_bytes(bundle: CommitmentBundle) -> bytes:
    return json.dumps(bundle.model_dump(), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def commitment(bundle: CommitmentBundle) -> str:
    return hashlib.sha256(b"ReliaMesh Devnet commitment v1\x00" + canonical_bytes(bundle)).hexdigest()


def prepare(report: dict, expected_signer: str) -> CommitmentBundle:
    return CommitmentBundle(expected_signer=expected_signer, salt_hex=secrets.token_hex(32),
                            report=WindowReport.model_validate(report))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def read_document(path, *, max_bytes=MAX_DOCUMENT_BYTES) -> dict:
    with Path(path).open("rb") as handle:
        content = handle.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ValueError("document exceeds size limit")
    try:
        value = json.loads(content, object_pairs_hook=_unique_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")))
    except (UnicodeError, RecursionError, json.JSONDecodeError):
        raise ValueError("invalid JSON document") from None
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def report_from_summary(summary: dict, *, synthetic: bool, server_version: str,
                        generated_at: datetime) -> WindowReport:
    """Project only the requested synthetic/real streams. Ignore lifetime totals.

    Identifiers and version labels never enter the report. The caller obtains
    the summary through its existing authenticated channel; this adds no export.
    """
    if type(synthetic) is not bool or summary.get("schema_version") != "1.0":
        raise ValueError("invalid summary selection")
    streams = [s for s in summary["streams"] if s["dimensions"]["synthetic"] is synthetic]
    selected = {s["stream_id"] for s in streams}
    incidents = [i for i in summary["incidents"]
                 if i["synthetic"] is synthetic and i["stream_id"] in selected]
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("aware generation time required")
    counts = {f"{window}_{name}": sum(s[window][field] for s in streams)
              for window in ("baseline", "current")
              for name, field in (("samples", "count"), ("failures", "failures"))}
    return WindowReport(server_version=server_version, synthetic=synthetic,
                        generated_at=generated_at.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
                        stream_count=len(streams), **counts,
                        **{name + "_incidents": sum(i["status"] == name for i in incidents)
                           for name in ("open", "resolved", "expired")})
