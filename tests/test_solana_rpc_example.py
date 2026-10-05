import importlib.util
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from reliamesh_sdk import Client, DeliveryError

from reliamesh.api import create_app
from reliamesh.attestation import report_from_summary
from reliamesh.config import Settings
from reliamesh.security import digest_key, new_key
from reliamesh.storage import SQLiteStore

_spec = importlib.util.spec_from_file_location(
    "solana_rpc_example", Path(__file__).resolve().parents[1] / "examples" / "solana_rpc_agent.py",
)
example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(example)
SENTINEL = "private-content-secret-never-print"


class RecordingClient:
    def __init__(self):
        self.events = []

    def emit(self, value):
        self.events.append(value)
        return True


def check(rpc, client=None, **kwargs):
    return example.check_readiness(rpc, client=client, deployment_id="test-solana", **kwargs)


def test_offline_default_and_explicit_fixture_never_connect(monkeypatch, capsys):
    def unexpected(*args, **kwargs):
        pytest.fail("offline fixture made a network connection")

    monkeypatch.setattr(example, "HTTPSConnection", unexpected)
    monkeypatch.setattr(example, "Client", unexpected)
    # An ambient key alone must not enable collection.
    monkeypatch.setenv("RELIAMESH_API_KEY", SENTINEL)
    for args in ([], ["--fixture"]):
        assert example.main(args) == 0
        output = capsys.readouterr()
        summary = json.loads(output.out)
        assert (summary["checks"], summary["ready"], summary["deferred"]) == (200, 150, 50)
        assert summary["synthetic"] is True and summary["collection_enabled"] is False
        assert summary["evidence"] is None
        assert SENTINEL not in output.out + output.err


def test_tool_outcome_does_not_claim_http_success_means_readiness():
    client = RecordingClient()
    assert check(example.FixtureRPC(), client, synthetic=True)["decision"] == "continue_planning"
    assert check(example.FixtureRPC(True), client, synthetic=True)["decision"] == "defer"
    success, failure = client.events
    assert success["operation"] == failure["operation"] == "tool"
    assert success["outcome"] == "success" and "failure_type" not in success
    assert failure["outcome"] == "failure" and failure["failure_type"] == "tool_error"
    assert failure["synthetic"] is True
    assert success["event_id"] != failure["event_id"]
    assert set(failure) == {
        "schema_version", "event_id", "timestamp", "deployment_id", "agent_id", "operation",
        "outcome", "failure_type", "synthetic", "provider", "tool", "tool_version",
        "agent_version", "sdk", "sdk_version", "retry_count", "latency_ms",
    }
    encoded = json.dumps(client.events)
    assert example.DEVNET_GENESIS not in encoded
    assert "11111111111111111111111111111111" not in encoded


@pytest.mark.parametrize(("failing", "status"), [(False, 0), (True, 2)])
def test_live_cli_requests_one_check_and_reports_a_real_observation(monkeypatch, capsys,
                                                                  failing, status):
    monkeypatch.setattr(example, "DevnetRPC", lambda: example.FixtureRPC(failing))
    assert example.main(["--live"]) == status
    result = json.loads(capsys.readouterr().out)
    assert result["synthetic"] is False and result["collection_enabled"] is False
    assert result["decision"] == ("defer" if failing else "continue_planning")
    assert example.DEVNET_GENESIS not in json.dumps(result)


@pytest.mark.parametrize(("method", "replacement", "failure_type", "reason"), [
    ("getGenesisHash", "11111111111111111111111111111111", "validation_failure", "wrong_cluster"),
    ("getSlot", True, "malformed_output", "invalid_rpc_response"),
    ("getLatestBlockhash", {"context": {"slot": 999}, "value": {
        "blockhash": "11111111111111111111111111111111", "lastValidBlockHeight": 1200,
    }}, "validation_failure", "stale_context"),
    ("getBlockHeight", 1181, "validation_failure", "blockhash_expiring"),
    ("getBlockHeight", 1201, "validation_failure", "blockhash_expiring"),
    ("getBlockHeight", -1, "malformed_output", "invalid_rpc_response"),
    ("getBlockHeight", 2**64, "malformed_output", "invalid_rpc_response"),
    ("getLatestBlockhash", {"message": SENTINEL}, "malformed_output", "invalid_rpc_response"),
])
def test_semantic_failures_are_classified_and_content_is_excluded(method, replacement,
                                                                 failure_type, reason):
    class RPC(example.FixtureRPC):
        def call(self, name, config=None):
            return replacement if name == method else super().call(name, config)

    client = RecordingClient()
    result = check(RPC(), client)
    assert result["decision"] == "defer"
    assert result["failure_type"] == client.events[0]["failure_type"] == failure_type
    assert result["reason"] == reason
    assert SENTINEL not in json.dumps([result, client.events])


def test_context_slot_and_expiry_margin_are_applied():
    calls = []

    class RPC(example.FixtureRPC):
        def call(self, method, config=None):
            calls.append((method, config))
            return 1180 if method == "getBlockHeight" else super().call(method, config)

    assert check(RPC())["outcome"] == "success"
    assert calls == [
        ("getGenesisHash", None), ("getSlot", {"commitment": "confirmed"}),
        ("getLatestBlockhash", {"commitment": "confirmed", "minContextSlot": 1000}),
        ("getBlockHeight", {"commitment": "confirmed", "minContextSlot": 1001}),
    ]


def install_connection(monkeypatch, body=b'{"jsonrpc":"2.0","id":1,"result":1000}',
                       status=200, error=None, encoding="identity"):
    state = {"requests": [], "closed": False, "read_sizes": []}

    class Response:
        def __init__(self):
            self.status = status
            self.body = io.BytesIO(body)

        def getheader(self, *_):
            return encoding

        def read1(self, size):
            state["read_sizes"].append(size)
            return self.body.read(size)

    class Connection:
        def __init__(self, host, *, timeout):
            state["host"], state["timeout"] = host, timeout

        def request(self, method, path, *, body, headers):
            state["requests"].append((method, path, json.loads(body), headers))
            if error:
                raise error

        def getresponse(self):
            return Response()

        def close(self):
            state["closed"] = True

    monkeypatch.setattr(example, "HTTPSConnection", Connection)
    return state


def test_live_transport_pins_tls_host_and_read_methods_without_credentials(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "https://" + SENTINEL + ".invalid")
    monkeypatch.setenv("RELIAMESH_API_KEY", SENTINEL)
    state = install_connection(monkeypatch)
    assert example.DevnetRPC().call("getSlot", {"commitment": "confirmed"}) == 1000
    assert state["host"] == "api.devnet.solana.com" and state["timeout"] == 3
    assert state["closed"] and len(state["requests"]) == 1
    assert state["requests"][0][:2] == ("POST", "/")
    assert SENTINEL not in json.dumps(state)
    for method in ("sendTransaction", "requestAirdrop", "getAccountInfo"):
        with pytest.raises(ValueError, match="read-only"):
            example.DevnetRPC().call(method)
    assert len(state["requests"]) == 1


@pytest.mark.parametrize(("kwargs", "classification"), [
    ({"status": 302}, "tool_error"),
    ({"status": 429}, "tool_error"),
    ({"error": TimeoutError(SENTINEL)}, "timeout"),
    ({"error": OSError(SENTINEL)}, "tool_error"),
    ({"body": b"x" * 16_385}, "malformed_output"),
    ({"body": b"{"}, "malformed_output"),
    ({"body": b'{"jsonrpc":"2.0","id":1,"result":NaN}'}, "malformed_output"),
    ({"body": b'{"jsonrpc":"2.0","id":1,"id":1,"result":1000}'}, "malformed_output"),
    ({"body": b'{"jsonrpc":"2.0","id":true,"result":1000}'}, "malformed_output"),
    ({"body": b'{"jsonrpc":"2.0","id":1,"error":{"message":"' + SENTINEL.encode() + b'"}}'},
     "tool_error"),
    ({"encoding": "gzip"}, "malformed_output"),
])
def test_rpc_limits_errors_and_redirects_never_leak(monkeypatch, kwargs, classification):
    state = install_connection(monkeypatch, **kwargs)
    client = RecordingClient()
    result = check(example.DevnetRPC(), client)
    assert result["failure_type"] == client.events[0]["failure_type"] == classification
    assert state["closed"] and len(state["requests"]) == 1  # no retries/redirects
    assert all(0 < size <= 4096 for size in state["read_sizes"])
    assert SENTINEL not in json.dumps([result, client.events])


def test_body_deadline_stops_trickled_responses(monkeypatch):
    state = install_connection(monkeypatch, body=b" " * 16_000)
    times = iter([0, 0, 4])
    monkeypatch.setattr(example.time, "monotonic", lambda: next(times))
    assert check(example.DevnetRPC())["failure_type"] == "timeout"
    assert state["read_sizes"] == [4096]


def test_fixture_drives_real_api_detection_and_recovery_with_safe_selected_reports(tmp_path):
    # SDK batching/event validation, real FastAPI endpoints, SQLite and detector;
    # only the HTTP transport is bridged to ASGI for this deterministic test.
    key = new_key()
    store = SQLiteStore(str(tmp_path / "fixture.db"))
    store.create_tenant("fixture", digest_key(key), datetime.now(UTC))
    api = TestClient(create_app(Settings(global_requests_per_second=10000), store))
    client = Client(endpoint="http://127.0.0.1:8080", api_key=key, failure_mode="raise")
    requests = []

    def request(path, payload=None):
        response = api.request("GET" if payload is None else "POST", path,
                               json=payload, headers={"Authorization": "Bearer " + key})
        assert response.status_code == 200, response.text
        requests.append((path, payload))
        value = response.json()
        if path == "/v1/summary":
            # Unrelated tenant data must never get copied to the example output.
            value["streams"].append({"dimensions": {"deployment_id": SENTINEL}})
            value["incidents"].append({"stream_id": SENTINEL})
            value["secret"] = SENTINEL
        return value

    client._request = request
    result = example.run_fixture(client)
    assert result["evidence"] == {
        "accepted": 200, "duplicates": 0, "sent": 200, "dropped": 0, "queued": 0,
        "incident_opened": True, "incident_recovered": True,
    }
    batches = [payload["events"] for path, payload in requests if path == "/v1/events"]
    assert [len(batch) for batch in batches] == [50, 50, 100]
    events = [event for batch in batches for event in batch]
    assert len({event["event_id"] for event in events}) == 200
    assert all(event["synthetic"] is True and event["operation"] == "tool" for event in events)
    assert sum(event["outcome"] == "failure" for event in events) == 50
    assert all(event["failure_type"] == "tool_error" for event in batches[1])
    regression = report_from_summary(result["regression_summary"], synthetic=True,
                                     server_version="0.2.0", generated_at=datetime.now(UTC))
    recovery = report_from_summary(result["recovery_summary"], synthetic=True,
                                   server_version="0.2.0", generated_at=datetime.now(UTC))
    assert regression.stream_count == 1 and regression.current_failures == 50
    assert regression.open_incidents == 1 and recovery.resolved_incidents == 1
    assert recovery.current_failures == recovery.open_incidents == 0
    assert SENTINEL not in json.dumps(result) and key not in json.dumps(result)


def test_failed_collection_is_not_reported_as_success(capsys, monkeypatch):
    class BrokenClient(Client):
        def summary(self):
            raise DeliveryError(SENTINEL)

    monkeypatch.setattr(example, "Client", BrokenClient)
    monkeypatch.setenv("RELIAMESH_API_KEY", SENTINEL)
    assert example.main(["--fixture", "--endpoint", "http://127.0.0.1:8080"]) == 1
    output = capsys.readouterr()
    assert not output.out and SENTINEL not in output.err
    assert json.loads(output.err) == {"error": "example_or_collection_failed"}


def test_summary_file_is_opt_in_exclusive_and_runs_have_unique_deployments(tmp_path, capsys):
    path = tmp_path / "evidence.json"
    assert example.main(["--fixture", "--summary-output", str(path)]) == 0
    first = json.loads(path.read_text())
    second = example.run_fixture()
    assert first["deployment_id"] != second["deployment_id"]
    assert example.main(["--fixture", "--summary-output", str(path)]) == 1
    assert json.loads(path.read_text()) == first
    capsys.readouterr()


def test_endpoint_requires_explicit_credential_and_modes_are_exclusive(monkeypatch, capsys):
    monkeypatch.delenv("RELIAMESH_API_KEY", raising=False)
    for args in (["--endpoint", "http://127.0.0.1:8080"], ["--live", "--fixture"],
                 ["--live", "--summary-output", "file.json"]):
        with pytest.raises(SystemExit) as error:
            example.main(args)
        assert error.value.code == 2
    capsys.readouterr()
