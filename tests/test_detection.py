import copy
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from reliamesh.detection import (
    MAX_DEDUP,
    MAX_STATE_BYTES,
    DetectionError,
    process_events,
    summarize,
    wilson_interval,
)
from reliamesh.protocol import Event, failure_fingerprint

START = datetime(2026, 9, 1, tzinfo=UTC)
NOW = START + timedelta(hours=2)


def event(index, **changes):
    return Event.model_validate({
        "event_id": str(UUID(int=index + 1)), "timestamp": START + timedelta(seconds=index),
        "deployment_id": "test", "agent_id": "assistant", "operation": "agent",
        "outcome": "success", "latency_ms": 100.0, "input_tokens": 100, "output_tokens": 50,
        "retry_count": 0, "model": "model", "model_version": "v1", "synthetic": True,
        **changes,
    })


def ingest(state, events, now=NOW):
    for start in range(0, len(events), 100):
        state, _ = process_events(state, events[start:start + 100], now)
    return state


def stream(state, now=NOW):
    return summarize(state, now)["streams"][0]


def test_semantic_failure_detects_regression_and_recovers_across_versions():
    state = ingest({}, [event(i) for i in range(100)])
    assert stream(state)["status"] == "observed"
    # The caller classified an HTTP-200 structured output as malformed.
    state = ingest(state, [event(i, outcome="failure", failure_type="malformed_output", model_version="v2") for i in range(100, 150)])
    summary = summarize(state, NOW)
    assert summary["streams"][0]["status"] == "degraded"
    assert "failure_rate_regression" in summary["incidents"][0]["signals"]
    baseline = copy.deepcopy(summary["incidents"][0]["baseline"])
    assert baseline["failure_rate"] == 0
    assert len(summary["streams"][0]["versions"]) == 2
    assert summary["streams"][0]["failure_fingerprints"][0]["fingerprint"] == failure_fingerprint(event(140, outcome="failure", failure_type="malformed_output", model_version="v2"))
    state = ingest(state, [event(i, model_version="v3") for i in range(150, 205)])
    incident = summarize(state, NOW)["incidents"][0]
    assert incident["status"] == "resolved"
    assert incident["baseline"] == baseline
    assert stream(state)["status"] == "warming"


def test_constant_failure_cold_start_has_absolute_signal_and_recovery():
    state = ingest({}, [event(i, outcome="failure", failure_type="timeout") for i in range(60)])
    summary = summarize(state, NOW)
    assert summary["streams"][0]["status"] == "degraded"
    assert summary["incidents"][0]["baseline"]["count"] == 0
    assert "high_failure_rate" in summary["incidents"][0]["signals"]
    state = ingest(state, [event(i) for i in range(60, 120)])
    assert summarize(state, NOW)["incidents"][0]["status"] == "resolved"


def test_low_samples_do_not_claim_reliability_or_regression():
    state = ingest({}, [event(i, outcome="failure", failure_type="timeout") for i in range(10)])
    assert stream(state)["status"] == "warming"
    assert stream(state)["warmup"] is True
    assert summarize(state, NOW)["incidents"] == []


@pytest.mark.parametrize("changes,signal", [
    ({"latency_ms": 1000.0}, "latency_degradation"),
    ({"input_tokens": 1000, "output_tokens": 500}, "token_consumption_degradation"),
    ({"retry_count": 12}, "retry_degradation"),
])
def test_numeric_degradation_and_recovery(changes, signal):
    state = ingest({}, [event(i) for i in range(100)])
    state = ingest(state, [event(i, **changes) for i in range(100, 150)])
    assert signal in summarize(state, NOW)["incidents"][0]["signals"]
    state = ingest(state, [event(i) for i in range(150, 210)])
    assert summarize(state, NOW)["incidents"][0]["status"] == "resolved"


def test_missing_numeric_measurements_do_not_trigger_or_claim_recovery():
    state = ingest({}, [event(i) for i in range(100)])
    state = ingest(state, [event(i, latency_ms=1000.0) for i in range(100, 150)])
    state = ingest(state, [event(i, latency_ms=None, input_tokens=None) for i in range(150, 210)])
    assert summarize(state, NOW)["incidents"][0]["status"] == "open"
    assert stream(state)["current"]["median_total_tokens"] is None


def test_replay_duplicate_conflict_and_atomicity():
    first = event(0)
    state, metrics = process_events({}, [first, first], NOW)
    assert metrics == {"accepted": 1, "duplicates": 1}
    snapshot = copy.deepcopy(state)
    with pytest.raises(DetectionError, match="event_id_conflict"):
        process_events(state, [event(1), first.model_copy(update={"latency_ms": 999.0})], NOW)
    assert state == snapshot
    _, metrics = process_events(state, [first], NOW)
    assert metrics == {"accepted": 0, "duplicates": 1}


def test_evicted_replay_is_rejected_and_state_stays_bounded():
    state = ingest({}, [event(i) for i in range(MAX_DEDUP + 100)])
    assert len(state["dedup"]) == MAX_DEDUP
    assert len(json.dumps(state, separators=(",", ":")).encode()) <= MAX_STATE_BYTES
    with pytest.raises(DetectionError, match="stale_replay"):
        process_events(state, [event(0)], NOW)


def test_batch_sorting_and_explicit_late_event_rejection():
    state, _ = process_events({}, [event(2), event(0)], NOW)
    with pytest.raises(DetectionError, match="late_event"):
        process_events(state, [event(1)], NOW)
    assert stream(state)["current"]["count"] == 2


@pytest.mark.parametrize("timestamp,error", [
    (NOW - timedelta(days=7, seconds=1), "event_expired"),
    (NOW + timedelta(minutes=5, seconds=1), "event_in_future"),
])
def test_timestamp_bounds(timestamp, error):
    with pytest.raises(DetectionError, match=error):
        process_events({}, [event(0, timestamp=timestamp)], NOW)


def test_stream_and_version_cardinality_reject_atomically():
    with pytest.raises(DetectionError, match="stream_limit_exceeded"):
        process_events({}, [event(i, agent_id=f"agent-{i}") for i in range(17)], NOW)
    with pytest.raises(DetectionError, match="version_limit_exceeded"):
        process_events({}, [event(i, model_version=f"v{i}") for i in range(9)], NOW)


def test_synthetic_and_observed_streams_never_mix():
    state = ingest({}, [event(i, synthetic=bool(i % 2)) for i in range(100)])
    assert len(summarize(state, NOW)["streams"]) == 2
    assert {item["dimensions"]["synthetic"] for item in summarize(state, NOW)["streams"]} == {True, False}


def test_read_and_write_retention_prune_incidents_and_baselines():
    state = ingest({}, [event(i) for i in range(100)])
    state = ingest(state, [event(i, outcome="failure", failure_type="timeout") for i in range(100, 150)])
    future = NOW + timedelta(days=8)
    assert summarize(state, future)["streams"] == []
    assert summarize(state, future)["incidents"] == []
    refreshed, _ = process_events(state, [event(200, timestamp=future)], future)
    assert len(refreshed["dedup"]) == 1
    assert stream(refreshed, future)["status"] == "warming"


def test_wilson_interval_matches_known_zero_failure_bound():
    low, high = wilson_interval(0, 50)
    assert low == 0
    assert high == pytest.approx(0.0713476, abs=1e-6)
    assert wilson_interval(0, 0) == (0, 1)


def test_deterministic_transaction_retry():
    events = [event(i) for i in range(100)]
    assert process_events({}, events, NOW) == process_events({}, events, NOW)


def test_history_is_bounded_across_repeated_incidents():
    state = ingest({}, [event(i) for i in range(100)])
    offset = 100
    for _ in range(34):
        state = ingest(state, [event(i, outcome="failure", failure_type="timeout") for i in range(offset, offset + 100)])
        state = ingest(state, [event(i) for i in range(offset + 100, offset + 200)])
        offset += 200
    incidents = summarize(state, NOW)["incidents"]
    assert len(incidents) == 32
    assert all(item["status"] == "resolved" for item in incidents)
    assert len(json.dumps(state, separators=(",", ":")).encode()) <= MAX_STATE_BYTES


def test_expiring_frozen_baseline_expires_incident_and_rewarms():
    state = ingest({}, [event(i) for i in range(100)])
    later = START + timedelta(days=6)
    failures = [event(100 + i, timestamp=later + timedelta(seconds=i), outcome="failure", failure_type="timeout") for i in range(50)]
    state = ingest(state, failures, now=later + timedelta(hours=1))
    assert stream(state, later + timedelta(hours=1))["status"] == "degraded"
    expired = START + timedelta(days=7, hours=1)
    summary = summarize(state, expired)
    assert summary["incidents"][0]["status"] == "expired"
    assert summary["incidents"][0]["resolution"] == "baseline_expired"
    assert summary["streams"][0]["warmup"] is True
    assert summary["streams"][0]["active_incident"] is None
    later_read = summarize(state, expired + timedelta(hours=1))
    assert later_read["incidents"][0]["resolved_at"] == summary["incidents"][0]["resolved_at"]
