import json
import threading
import time
from collections import deque
from datetime import UTC, datetime
from http.client import IncompleteRead
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from reliamesh_sdk import Client, DeliveryError, SDKError, event, from_otel_attributes
from reliamesh_sdk.events import IDENTIFIERS


@pytest.fixture
def receiver():
    state = {"requests": [], "statuses": deque(), "delay": 0, "redirect": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def handle_request(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["requests"].append({"path": self.path, "body": body,
                                      "auth": self.headers.get("Authorization")})
            time.sleep(state["delay"])
            status = state["statuses"].popleft() if state["statuses"] else 200
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if state["redirect"]:
                self.send_header("Location", state["redirect"])
            self.end_headers()
            try:
                count = len(json.loads(body)["events"]) if body else 1
                self.wfile.write(json.dumps({"schema_version": "1.0", "accepted": count,
                                            "duplicates": 0}).encode())
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        do_POST = handle_request
        do_GET = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield state, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    worker.join(timeout=2)


def example_event(**kwargs):
    return event(deployment_id="test-deployment", agent_id="test-agent", operation="agent",
                 outcome="success", synthetic=True, **kwargs)


def client(receiver, **kwargs):
    return Client(endpoint=receiver[1], api_key="test-only-key", **kwargs)


def test_emit_is_local_and_flush_has_explicit_authorization(receiver):
    sdk = client(receiver)
    original = example_event(prompt_version="sha256:123")
    assert sdk.emit(original)
    original["prompt_version"] = "changed"
    assert receiver[0]["requests"] == []
    assert sdk.flush() == 1
    received = receiver[0]["requests"][0]
    assert received["path"] == "/v1/events"
    assert received["auth"] == "Bearer test-only-key"
    assert json.loads(received["body"])["events"][0]["prompt_version"] == "sha256:123"
    assert sdk.counters == dict(enqueued=1, sent=1, dropped=0, failed_batches=0, retries=0, queued=0)


@pytest.mark.parametrize("status", [429, 503])
def test_retries_reuse_ids_and_payload(receiver, status):
    receiver[0]["statuses"].extend([status, 200])
    sdk = client(receiver)
    sdk.emit(example_event())
    assert sdk.flush() == 1
    requests = receiver[0]["requests"]
    assert len(requests) == 2
    assert requests[0]["body"] == requests[1]["body"]
    assert sdk.counters["retries"] == 1


@pytest.mark.parametrize("status", [400, 401, 403, 500])
def test_nonretryable_failure_is_dropped_and_observable(receiver, status):
    receiver[0]["statuses"].append(status)
    sdk = client(receiver)
    sdk.emit(example_event())
    assert sdk.flush() == 0
    assert len(receiver[0]["requests"]) == 1
    assert sdk.counters["dropped"] == 1
    assert sdk.counters["failed_batches"] == 1


def test_retry_budget_is_bounded(receiver):
    receiver[0]["statuses"].extend([503] * 10)
    sdk = client(receiver, max_retries=1, failure_mode="raise")
    sdk.emit(example_event())
    with pytest.raises(DeliveryError, match="HTTP 503"):
        sdk.flush()
    assert len(receiver[0]["requests"]) == 2
    assert sdk.counters["queued"] == 0


def test_timeout_fails_once_without_hanging(receiver):
    receiver[0]["delay"] = 0.2
    sdk = client(receiver, timeout=0.02, failure_mode="raise")
    sdk.emit(example_event())
    start = time.monotonic()
    with pytest.raises(DeliveryError):
        sdk.flush()
    assert time.monotonic() - start < 1
    assert len(receiver[0]["requests"]) == 1


def test_redirects_do_not_forward_credentials(receiver):
    receiver[0]["redirect"] = receiver[1] + "/credential-trap"
    receiver[0]["statuses"].append(307)
    sdk = client(receiver)
    sdk.emit(example_event())
    assert sdk.flush() == 0
    assert len(receiver[0]["requests"]) == 1


@pytest.mark.parametrize("endpoint", [
    "http://example.com", "ftp://example.com", "https://name:password@example.com",
    "https://example.com?token=secret", "https://example.com#fragment", "", "/relative",
    "https://example.com:99999", "https://example.com\n", "http://localhost.example.com",
    "https://example.com\\@other.example",
])
def test_rejects_unsafe_endpoints(endpoint):
    with pytest.raises(ValueError, match="endpoint"):
        Client(endpoint=endpoint, api_key="test-only-key")


@pytest.mark.parametrize("endpoint", ["http://localhost:8000", "http://127.0.0.1:8000",
                                       "http://[::1]:8000", "https://example.com"])
def test_accepts_explicit_secure_or_loopback_endpoints(endpoint):
    assert Client(endpoint=endpoint, api_key="test-only-key").endpoint == endpoint


def test_queue_is_bounded_and_invalid_content_cannot_leak(receiver):
    sdk = client(receiver, queue_capacity=1)
    assert not sdk.emit({**example_event(), "prompt": "sensitive body"})
    assert sdk.emit(example_event())
    assert not sdk.emit(example_event())
    assert sdk.counters["queued"] == 1
    assert sdk.counters["dropped"] == 2
    sdk.flush()
    assert b"sensitive" not in receiver[0]["requests"][0]["body"]


def test_raise_mode_preserves_app_exception(receiver):
    sdk = client(receiver, failure_mode="raise", queue_capacity=1)
    sdk.emit(example_event())
    with pytest.raises(RuntimeError, match="application-secret"):
        with sdk.observe(deployment_id="test", agent_id="test"):
            raise RuntimeError("application-secret")
    assert sdk.counters["dropped"] == 1


def test_observe_uses_generic_classification_and_never_exception_text(receiver):
    sdk = client(receiver)
    with pytest.raises(TimeoutError):
        with sdk.observe(deployment_id="test", agent_id="test", operation="tool", synthetic=True):
            raise TimeoutError("private customer content")
    assert not receiver[0]["requests"]
    sdk.flush()
    body = receiver[0]["requests"][0]["body"]
    assert b"private" not in body
    observed = json.loads(body)["events"][0]
    assert observed["outcome"] == "failure"
    assert observed["failure_type"] == "timeout"
    assert observed["latency_ms"] >= 0
    assert observed["synthetic"] is True


def test_observe_success_and_read_endpoints(receiver):
    sdk = client(receiver)
    with sdk.observe(deployment_id="test", agent_id="test", operation="model"):
        pass
    sdk.flush()
    assert sdk.summary()["accepted"] == 1
    assert sdk.incidents()["accepted"] == 1
    assert [request["path"] for request in receiver[0]["requests"]] == [
        "/v1/events", "/v1/summary", "/v1/incidents"]


def test_flush_batch_size_limit(receiver):
    sdk = client(receiver)
    for _ in range(201):
        sdk.emit(example_event())
    assert sdk.flush() == 201
    assert [len(json.loads(request["body"])["events"]) for request in receiver[0]["requests"]] == [100, 100, 1]


def test_flush_batches_respect_server_byte_limit(receiver):
    sdk = client(receiver)
    for _ in range(100):
        sdk.emit(event(**{field: "a" * 64 for field in IDENTIFIERS}, operation="validation",
                       outcome="failure", failure_type="validation_failure", input_tokens=10**9,
                       output_tokens=10**9, retry_count=10**6, latency_ms=86_400_000))
    assert sdk.flush() == 100
    requests = receiver[0]["requests"]
    assert len(requests) == 2
    assert all(len(request["body"]) <= 131_072 for request in requests)


def test_long_fractional_timestamp_is_canonicalized_before_queueing(receiver):
    sdk = client(receiver)
    value = example_event()
    value["timestamp"] = "2026-09-21T00:00:00." + "0" * 200_000 + "+00:00"
    assert sdk.emit(value)
    assert sdk.flush() == 1
    assert len(receiver[0]["requests"][0]["body"]) < 1000


@pytest.mark.parametrize("bad", [
    {"input_tokens": True}, {"latency_ms": float("nan")}, {"retry_count": -1},
    {"prompt_version": "customer email@example.com"}, {"attributes": {}},
    {"failure_type": "timeout"}, {"model": "x" * 65}, {"latency_ms": 10**1000},
])
def test_event_builder_rejects_invalid_or_content_like_fields(bad):
    with pytest.raises(ValueError):
        example_event(**bad)


def test_otel_adapter_is_pure_whitelist_and_version_aware():
    mapped = from_otel_attributes({
        "gen_ai.operation.name": "chat", "gen_ai.provider.name": "synthetic",
        "gen_ai.request.model": "synthetic-model", "gen_ai.usage.input_tokens": 20,
        "reliamesh.prompt.version": "hash-123", "telemetry.sdk.version": "1.0",
        "gen_ai.input.messages": [{"content": "private prompt"}],
        "gen_ai.output.messages": [{"content": "private output"}],
        "exception.message": "private exception", "arbitrary": "private",
    }, deployment_id="test", agent_id="test", outcome="success", synthetic=True)
    assert mapped["operation"] == "model"
    assert mapped["input_tokens"] == 20
    assert mapped["prompt_version"] == "hash-123"
    assert mapped["sdk_version"] == "1.0"
    assert "private" not in json.dumps(mapped)


def test_otel_unsupported_operation_is_explicit():
    with pytest.raises(ValueError, match="operation"):
        from_otel_attributes({"gen_ai.operation.name": "custom"}, deployment_id="test",
                             agent_id="test", outcome="success")


def test_default_timestamps_are_aware_and_naive_are_rejected():
    assert datetime.fromisoformat(example_event()["timestamp"]).tzinfo == UTC
    with pytest.raises(ValueError, match="timezone"):
        example_event(timestamp=datetime(2026, 1, 1))


def test_invalid_event_errors_do_not_include_sensitive_values(receiver):
    sdk = client(receiver, failure_mode="raise")
    with pytest.raises(SDKError) as caught:
        sdk.emit({"secret": "sensitive-value"})
    assert "sensitive-value" not in str(caught.value)
    assert "test-only-key" not in repr(sdk)


def test_incomplete_response_is_sanitized_and_dropped(receiver, monkeypatch):
    sdk = client(receiver)
    sdk.emit(example_event())

    def interrupted_response(*_args, **_kwargs):
        raise IncompleteRead(b"private-server-body", 100)

    monkeypatch.setattr(sdk._opener, "open", interrupted_response)
    assert sdk.flush() == 0
    assert sdk.counters["dropped"] == 1


@pytest.mark.parametrize("config", [
    {"timeout": 0}, {"timeout": float("nan")}, {"timeout": 10**1000},
    {"max_retries": 4}, {"max_retries": True}, {"queue_capacity": 0},
    {"queue_capacity": 10001}, {"failure_mode": "ignore"},
])
def test_delivery_configuration_stays_bounded(receiver, config):
    with pytest.raises(ValueError):
        client(receiver, **config)
