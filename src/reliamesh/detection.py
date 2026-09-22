"""Bounded, deterministic tenant-local detection, independent of storage/network."""

from __future__ import annotations

import copy
import json
import math
from collections import Counter
from datetime import datetime
from statistics import median

from reliamesh.protocol import (
    PUBLIC_COMPONENT_FIELDS,
    Event,
    canonical_hash,
    version_metadata,
)

WINDOW = 50
MAX_STREAMS = 16
MAX_VERSIONS = 8
MAX_DEDUP = 2048
MAX_INCIDENTS = 32
MAX_STATE_BYTES = 650_000
RETENTION_SECONDS = 7 * 24 * 3600
FUTURE_SECONDS = 300
# Compact sample: event epoch seconds, failed, latency, total tokens, version ID,
# failure classification, retry count. No event content is retained.
TS, FAILED, LATENCY, TOKENS, VERSION, FAILURE, RETRIES = range(7)


class DetectionError(ValueError):
    """Public safe error code; the original state is unchanged on failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _epoch(now: datetime) -> float:
    if now.tzinfo is None or now.utcoffset() is None:
        raise DetectionError("aware_now_required")
    return now.timestamp()


def _empty() -> dict:
    return {
        "schema_version": "1.0", "streams": {}, "dedup": {}, "incidents": [],
        "replay_floor": 0.0, "totals": {"accepted": 0, "duplicates": 0},
    }


def _prune(state: dict, now: float) -> dict:
    state = copy.deepcopy(state) if state else _empty()
    cutoff = now - RETENTION_SECONDS
    state["dedup"] = {key: value for key, value in state["dedup"].items() if value[1] > cutoff}
    state["incidents"] = [item for item in state["incidents"] if item["opened_at"] > cutoff]
    for key, stream in list(state["streams"].items()):
        if stream["last_timestamp"] <= cutoff:
            del state["streams"][key]
            continue
        original_baseline = len(stream["baseline"])
        baseline_expiry = stream["baseline"][0][TS] + RETENTION_SECONDS if original_baseline else None
        for field in ("baseline", "current"):
            stream[field] = [row for row in stream[field] if row[TS] > cutoff]
        if stream["active_incident"] and not any(
            item["incident_id"] == stream["active_incident"] for item in state["incidents"]
        ):
            stream["active_incident"] = None
        if len(stream["baseline"]) < original_baseline:
            if stream["active_incident"]:
                incident = _incident(state, stream)
                incident.update(status="expired", resolved_at=baseline_expiry, resolution="baseline_expired")
                stream["active_incident"] = None
            # A partial baseline is no longer comparable. Rebuild from fresh samples.
            rows = stream["baseline"] + stream["current"]
            stream["baseline"] = rows[:WINDOW] if len(rows) >= WINDOW else []
            stream["current"] = rows[WINDOW:] if len(rows) >= WINDOW else rows
        _trim_versions(stream)
    return state


def _trim_versions(stream: dict) -> None:
    used = {row[VERSION] for row in stream["baseline"] + stream["current"]}
    stream["versions"] = {key: value for key, value in stream["versions"].items() if key in used}


def _incident(state: dict, stream: dict) -> dict:
    return next(item for item in state["incidents"] if item["incident_id"] == stream["active_incident"])


def wilson_interval(failures: int, count: int) -> tuple[float, float]:
    """Two-sided 95% Wilson score interval for a binomial proportion."""
    if count == 0:
        return (0.0, 1.0)
    z = 1.959963984540054
    rate = failures / count
    denominator = 1 + z * z / count
    center = (rate + z * z / (2 * count)) / denominator
    radius = z * math.sqrt(rate * (1 - rate) / count + z * z / (4 * count * count)) / denominator
    return (0.0 if failures == 0 else max(0.0, center - radius),
            1.0 if failures == count else min(1.0, center + radius))


def _stats(rows: list) -> dict:
    failed = sum(row[FAILED] for row in rows)
    latencies = [row[LATENCY] for row in rows if row[LATENCY] is not None]
    tokens = [row[TOKENS] for row in rows if row[TOKENS] is not None]
    retries = [row[RETRIES] for row in rows if row[RETRIES] is not None]
    low, high = wilson_interval(failed, len(rows))
    return {
        "count": len(rows), "failures": failed, "failure_rate": failed / len(rows) if rows else None,
        "failure_rate_interval_95": [round(low, 6), round(high, 6)],
        "latency_samples": len(latencies), "median_latency_ms": median(latencies) if latencies else None,
        "token_samples": len(tokens), "median_total_tokens": median(tokens) if tokens else None,
        "retry_samples": len(retries), "median_retry_count": median(retries) if retries else None,
        "first_timestamp": rows[0][TS] if rows else None,
        "last_timestamp": rows[-1][TS] if rows else None,
        "version_counts": dict(sorted(Counter(row[VERSION] for row in rows).items())),
    }


def _metric_signal(baseline: list, current: list, index: int, minimum_delta: float) -> dict | None:
    before = [row[index] for row in baseline if row[index] is not None]
    after = [row[index] for row in current if row[index] is not None]
    if min(len(before), len(after)) < 40:
        return None
    base_median, current_median = median(before), median(after)
    threshold = max(sorted(before)[math.ceil(0.95 * len(before)) - 1], base_median * 1.5, base_median + minimum_delta)
    base_elevated = sum(value > threshold for value in before)
    current_elevated = sum(value > threshold for value in after)
    base_interval = wilson_interval(base_elevated, len(before))
    current_interval = wilson_interval(current_elevated, len(after))
    if current_median > threshold and current_interval[0] > base_interval[1]:
        return {
            "threshold": threshold, "baseline_median": base_median, "current_median": current_median,
            "baseline_elevated_interval_95": list(base_interval),
            "current_elevated_interval_95": list(current_interval),
        }
    return None


def _signals(stream: dict) -> dict:
    baseline, current = stream["baseline"], stream["current"]
    base, observed = _stats(baseline), _stats(current)
    signals = {}
    if len(current) >= 25 and observed["failure_rate_interval_95"][0] > 0.5:
        signals["high_failure_rate"] = {"failure_rate_interval_95": observed["failure_rate_interval_95"]}
    if len(baseline) < WINDOW or len(current) < WINDOW:
        return signals
    if (
        observed["failure_rate"] - base["failure_rate"] >= 0.15
        and observed["failure_rate_interval_95"][0] > base["failure_rate_interval_95"][1]
    ):
        signals["failure_rate_regression"] = {
            "baseline_rate": base["failure_rate"], "current_rate": observed["failure_rate"],
            "baseline_interval_95": base["failure_rate_interval_95"],
            "current_interval_95": observed["failure_rate_interval_95"],
        }
    for kind, index, minimum in (
        ("latency_degradation", LATENCY, 50), ("token_consumption_degradation", TOKENS, 100),
        ("retry_degradation", RETRIES, 2),
    ):
        if evidence := _metric_signal(baseline, current, index, minimum):
            signals[kind] = evidence
    return signals


def _recovered(stream: dict, incident: dict) -> bool:
    if len(stream["current"]) < WINDOW:
        return False
    base, current = _stats(stream["baseline"]), _stats(stream["current"])
    kinds = incident["signals"]
    if "high_failure_rate" in kinds and current["failure_rate_interval_95"][1] >= 0.25:
        return False
    if "failure_rate_regression" in kinds and current["failure_rate"] > base["failure_rate"] + 0.05:
        return False
    for kind, median_key, sample_key, delta in (
        ("latency_degradation", "median_latency_ms", "latency_samples", 25),
        ("token_consumption_degradation", "median_total_tokens", "token_samples", 50),
        ("retry_degradation", "median_retry_count", "retry_samples", 1),
    ):
        if kind in kinds and (
            current[sample_key] < 40 or current[median_key] > max(base[median_key] * 1.25, base[median_key] + delta)
        ):
            return False
    return True


def _evaluate(state: dict, stream_id: str, stream: dict, event: Event) -> None:
    signals = _signals(stream)
    if stream["active_incident"]:
        incident = _incident(state, stream)
        incident["signals"].update(signals)
        incident["current"] = _stats(stream["current"])
        if _recovered(stream, incident) and not signals:
            incident.update(status="resolved", resolved_at=event.timestamp.timestamp(), resolution="recovered")
            stream["active_incident"] = None
            stream["baseline"] = stream["current"][-WINDOW:]
            stream["current"] = []
    elif signals:
        # Keep every active incident; evict oldest resolved history first.
        if len(state["incidents"]) >= MAX_INCIDENTS:
            removable = next((item for item in state["incidents"] if item["status"] != "open"), None)
            if removable is not None:
                state["incidents"].remove(removable)
        incident_id = canonical_hash({"stream_id": stream_id, "onset_event": event.event_id})
        state["incidents"].append({
            "incident_id": incident_id, "stream_id": stream_id, "status": "open",
            "opened_at": event.timestamp.timestamp(), "resolved_at": None, "resolution": None,
            "synthetic": event.synthetic, "signals": signals,
            "baseline": _stats(stream["baseline"]), "current": _stats(stream["current"]),
        })
        stream["active_incident"] = incident_id


def process_events(state: dict, events: list[Event], now: datetime) -> tuple[dict, dict]:
    """Pure transaction callback. Return a new JSON state and accepted/duplicate counts.

    Validation/capacity/conflict errors reject the entire batch without mutation.
    Late events before a stream's last accepted timestamp are rejected explicitly.
    """
    server_time = _epoch(now)
    if not 1 <= len(events) <= 100:
        raise DetectionError("batch_size_exceeded")
    result = _prune(state, server_time)
    metrics = {"accepted": 0, "duplicates": 0}
    for event in sorted(events, key=lambda item: (item.timestamp, item.event_id)):
        timestamp = event.timestamp.timestamp()
        if timestamp <= server_time - RETENTION_SECONDS:
            raise DetectionError("event_expired")
        if timestamp > server_time + FUTURE_SECONDS:
            raise DetectionError("event_in_future")
        digest = canonical_hash(event.model_dump(mode="json"))
        if previous := result["dedup"].get(event.event_id):
            if previous[0] != digest:
                raise DetectionError("event_id_conflict")
            metrics["duplicates"] += 1
            continue
        if timestamp <= result["replay_floor"]:
            raise DetectionError("stale_replay")
        dimensions = {
            "deployment_id": event.deployment_id, "agent_id": event.agent_id,
            "operation": event.operation, "synthetic": event.synthetic,
        }
        stream_id = canonical_hash(dimensions)
        if stream_id not in result["streams"]:
            if len(result["streams"]) >= MAX_STREAMS:
                raise DetectionError("stream_limit_exceeded")
            result["streams"][stream_id] = {
                "dimensions": dimensions, "baseline": [], "current": [], "versions": {},
                "active_incident": None, "last_timestamp": timestamp,
            }
        stream = result["streams"][stream_id]
        if timestamp < stream["last_timestamp"]:
            raise DetectionError("late_event")
        versions = version_metadata(event)
        version_id = canonical_hash(versions)
        _trim_versions(stream)
        if version_id not in stream["versions"] and len(stream["versions"]) >= MAX_VERSIONS:
            raise DetectionError("version_limit_exceeded")
        stream["versions"][version_id] = versions
        total_tokens = None
        if event.input_tokens is not None and event.output_tokens is not None:
            total_tokens = event.input_tokens + event.output_tokens
        sample = [timestamp, int(event.outcome == "failure"), event.latency_ms, total_tokens,
                  version_id, event.failure_type, event.retry_count]
        if len(stream["current"]) == WINDOW:
            oldest = stream["current"].pop(0)
            if not stream["active_incident"] and stream["baseline"]:
                stream["baseline"] = (stream["baseline"] + [oldest])[-WINDOW:]
        stream["current"].append(sample)
        stream["last_timestamp"] = timestamp
        _evaluate(result, stream_id, stream, event)
        if not stream["baseline"] and len(stream["current"]) == WINDOW and not stream["active_incident"]:
            stream["baseline"], stream["current"] = stream["current"], []
        result["dedup"][event.event_id] = [digest, timestamp]
        metrics["accepted"] += 1
    if len(result["dedup"]) > MAX_DEDUP:
        ordered = sorted(result["dedup"], key=lambda key: (result["dedup"][key][1], key))
        for event_id in ordered[:len(result["dedup"]) - MAX_DEDUP]:
            result["replay_floor"] = max(result["replay_floor"], result["dedup"].pop(event_id)[1])
    for key in metrics:
        result["totals"][key] += metrics[key]
    if len(json.dumps(result, separators=(",", ":")).encode()) > MAX_STATE_BYTES:
        raise DetectionError("state_capacity_exceeded")
    return result, metrics


def _fingerprints(stream: dict) -> list[dict]:
    counts = Counter()
    classifications = {}
    for row in stream["current"]:
        if not row[FAILED]:
            continue
        metadata = stream["versions"][row[VERSION]]
        value = canonical_hash({
            "fingerprint_version": "1.0", "operation": stream["dimensions"]["operation"],
            "failure_type": row[FAILURE],
            **{key: metadata[key] for key in PUBLIC_COMPONENT_FIELDS if key in metadata},
        })
        counts[value] += 1
        classifications[value] = row[FAILURE]
    return [{"fingerprint": key, "count": count, "failure_type": classifications[key]}
            for key, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:16]]


def summarize(state: dict, now: datetime) -> dict:
    """Build a privacy-safe tenant summary; expired data is suppressed on reads."""
    view = _prune(state, _epoch(now))
    streams = []
    for stream_id, stream in sorted(view["streams"].items()):
        warmup = len(stream["baseline"]) < WINDOW or len(stream["current"]) < WINDOW
        streams.append({
            "stream_id": stream_id, "dimensions": stream["dimensions"],
            "status": "degraded" if stream["active_incident"] else "warming" if warmup else "observed",
            "warmup": warmup, "baseline": _stats(stream["baseline"]), "current": _stats(stream["current"]),
            "active_incident": stream["active_incident"], "versions": stream["versions"],
            "failure_fingerprints": _fingerprints(stream),
        })
    return {
        "schema_version": "1.0", "totals": view["totals"], "streams": streams,
        "incidents": view["incidents"],
        "limits": {"streams": MAX_STREAMS, "window_samples": WINDOW, "versions_per_stream": MAX_VERSIONS,
                   "dedup_entries": MAX_DEDUP, "incident_history": MAX_INCIDENTS,
                   "retention_seconds": RETENTION_SECONDS, "max_state_bytes": MAX_STATE_BYTES},
    }
