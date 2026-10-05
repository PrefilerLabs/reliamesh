"""Read-only Devnet readiness tool with opt-in, content-minimized telemetry.

Run with --fixture (the default) for 200 deterministic, synthetic checks, or
--live for one check against the fixed official Devnet RPC. No wallet is used.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from http.client import HTTPException, HTTPSConnection
from pathlib import Path
from uuid import uuid4

from reliamesh_sdk import Client, SDKError, __version__, event

DEVNET_HOST = "api.devnet.solana.com"
DEVNET_GENESIS = "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG"
RPC_TIMEOUT = 3.0
MAX_RESPONSE_BYTES = 16_384
MIN_REMAINING_BLOCKS = 20
AGENT_ID = "solana-devnet-planner"
TOOL = "solana-rpc-readiness"
TOOL_VERSION = "1.0.0"
METHODS = frozenset({"getGenesisHash", "getSlot", "getLatestBlockhash", "getBlockHeight"})
BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class ReadinessFailure(Exception):
    """Only fixed classifications and reasons may be placed in this exception."""

    def __init__(self, failure_type: str, reason: str):
        self.failure_type = failure_type
        self.reason = reason
        super().__init__(reason)


def _malformed():
    raise ReadinessFailure("malformed_output", "invalid_rpc_response")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _malformed()
        result[key] = value
    return result


def _result(body: bytes):
    try:
        response = json.loads(body, object_pairs_hook=_object,
                              parse_constant=lambda _: _malformed())
    except (ValueError, RecursionError):
        _malformed()
    if (not isinstance(response, dict) or response.get("jsonrpc") != "2.0"
            or type(response.get("id")) is not int or response["id"] != 1):
        _malformed()
    if set(response) == {"jsonrpc", "id", "error"} and isinstance(response["error"], dict):
        # Never inspect the remote error message or copy it into an exception.
        raise ReadinessFailure("tool_error", "rpc_error")
    if set(response) != {"jsonrpc", "id", "result"}:
        _malformed()
    return response["result"]


class DevnetRPC:
    """Four allowlisted read methods; fixed TLS host, no proxies or redirects.

    Each call has a three-second socket timeout and a 16 KiB response limit.
    There are no retries. DNS resolution uses the operating system resolver.
    """

    def call(self, method: str, config: dict | None = None):
        if method not in METHODS:
            raise ValueError("unsupported read-only method")
        payload = {"jsonrpc": "2.0", "id": 1, "method": method}
        if config is not None:
            payload["params"] = [config]
        connection = HTTPSConnection(DEVNET_HOST, timeout=RPC_TIMEOUT)
        try:
            connection.request("POST", "/", body=json.dumps(payload).encode("ascii"),
                               headers={"Content-Type": "application/json",
                                        "Accept": "application/json"})
            response = connection.getresponse()
            # http.client never follows Location. Treat all non-200s as failures.
            if response.status != 200:
                raise ReadinessFailure("tool_error", "http_error")
            if response.getheader("Content-Encoding", "identity") != "identity":
                _malformed()
            # read1 returns after one buffered/socket read, allowing a deadline
            # check even when a peer keeps trickling small response fragments.
            deadline = time.monotonic() + RPC_TIMEOUT
            body = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise ReadinessFailure("timeout", "rpc_timeout")
                chunk = response.read1(min(4096, MAX_RESPONSE_BYTES + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    _malformed()
            return _result(bytes(body))
        except TimeoutError:
            raise ReadinessFailure("timeout", "rpc_timeout") from None
        except (OSError, HTTPException):
            raise ReadinessFailure("tool_error", "rpc_transport_error") from None
        finally:
            connection.close()


class FixtureRPC:
    """Deterministic RPC outcomes, never a network connection or real outage."""

    def __init__(self, failing: bool = False):
        self.failing = failing

    def call(self, method: str, config: dict | None = None):
        if method == "getGenesisHash":
            return DEVNET_GENESIS
        if method == "getSlot":
            return 1000
        if method == "getLatestBlockhash":
            if self.failing:
                return _result(b'{"jsonrpc":"2.0","id":1,"error":{"code":-32005}}')
            return {"context": {"slot": 1001}, "value": {
                "blockhash": "11111111111111111111111111111111", "lastValidBlockHeight": 1200,
            }}
        if method == "getBlockHeight":
            return 1100
        raise ValueError("unsupported fixture method")


def _u64(value):
    if type(value) is not int or not 0 <= value < 2**64:
        _malformed()
    return value


def _hash32(value):
    if not isinstance(value, str) or not 32 <= len(value) <= 44:
        _malformed()
    number = 0
    for character in value:
        if character not in BASE58:
            _malformed()
        number = number * 58 + BASE58.index(character)
    length = (number.bit_length() + 7) // 8 + len(value) - len(value.lstrip("1"))
    if length != 32:
        _malformed()


def _check(rpc):
    genesis = rpc.call("getGenesisHash")
    _hash32(genesis)
    if genesis != DEVNET_GENESIS:
        raise ReadinessFailure("validation_failure", "wrong_cluster")
    slot = _u64(rpc.call("getSlot", {"commitment": "confirmed"}))
    latest = rpc.call("getLatestBlockhash", {"commitment": "confirmed", "minContextSlot": slot})
    if (not isinstance(latest, dict) or not isinstance(latest.get("context"), dict)
            or not isinstance(latest.get("value"), dict)):
        _malformed()
    context_slot = _u64(latest["context"].get("slot"))
    _hash32(latest["value"].get("blockhash"))
    last_valid = _u64(latest["value"].get("lastValidBlockHeight"))
    if context_slot < slot:
        raise ReadinessFailure("validation_failure", "stale_context")
    height = _u64(rpc.call("getBlockHeight", {
        "commitment": "confirmed", "minContextSlot": context_slot,
    }))
    if last_valid - height < MIN_REMAINING_BLOCKS:
        raise ReadinessFailure("validation_failure", "blockhash_expiring")


def check_readiness(rpc, *, client: Client | None = None, deployment_id: str,
                    synthetic: bool = False, agent_version: str = "planner-v1") -> dict:
    """One tool outcome; defer planning on failure. Does not execute an action.

    Emit a classified event explicitly: HTTP success does not imply readiness,
    and observe() alone cannot classify semantic expiry or cluster mismatches.
    """
    started = time.perf_counter()
    outcome, failure_type, reason = "success", None, "readiness_checks_passed"
    try:
        _check(rpc)
    except ReadinessFailure as error:
        outcome, failure_type, reason = "failure", error.failure_type, error.reason
    if client is not None:
        client.emit(event(
            deployment_id=deployment_id, agent_id=AGENT_ID, operation="tool",
            outcome=outcome, failure_type=failure_type, synthetic=synthetic,
            provider="solana-devnet", tool=TOOL, tool_version=TOOL_VERSION,
            agent_version=agent_version, sdk="reliamesh-python", sdk_version=__version__,
            retry_count=0,
            latency_ms=1.0 if synthetic else min((time.perf_counter() - started) * 1000, 86_400_000),
        ))
    return {"decision": "continue_planning" if outcome == "success" else "defer",
            "outcome": outcome, "failure_type": failure_type, "reason": reason,
            "synthetic": synthetic}


def _require(condition):
    if not condition:
        raise ValueError("synthetic collection evidence did not match expectations")


def _count(value, maximum=1_000_000_000):
    _require(type(value) is int and 0 <= value <= maximum)
    return value


def _identifier(value):
    _require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None)
    return value


def _selected_summary(summary, deployment_id):
    """Project only this generated synthetic stream; never serialize tenant-wide data."""
    dimensions = {"deployment_id": deployment_id, "agent_id": AGENT_ID,
                  "operation": "tool", "synthetic": True}
    _require(isinstance(summary, dict) and summary.get("schema_version") == "1.0")
    matches = [s for s in summary["streams"] if s.get("dimensions") == dimensions]
    _require(len(matches) == 1)
    source = matches[0]
    stream = {"stream_id": _identifier(source["stream_id"]), "dimensions": dimensions}
    for window in ("baseline", "current"):
        stream[window] = {key: _count(source[window][key], 50) for key in ("count", "failures")}
        _require(stream[window]["failures"] <= stream[window]["count"])
    incidents = []
    for incident in summary["incidents"]:
        if incident.get("stream_id") != stream["stream_id"] or incident.get("synthetic") is not True:
            continue
        _require(incident["status"] in {"open", "resolved", "expired"})
        incidents.append({"incident_id": _identifier(incident["incident_id"]),
                          "stream_id": stream["stream_id"], "synthetic": True,
                          "status": incident["status"],
                          "recovered": incident.get("resolution") == "recovered",
                          "regression": "failure_rate_regression" in incident["signals"]})
    return {"schema_version": "1.0", "streams": [stream], "incidents": incidents}


def run_fixture(client: Client | None = None) -> dict:
    """Run 200 synthetic tool checks and optionally assert actual API detection.

    Supply a fresh SDK client with ingest/read scopes and a disposable tenant.
    Returned selected summaries can feed report_from_summary; no export is implicit.
    """
    deployment = "solana-fixture-" + uuid4().hex
    result = {"synthetic": True, "deployment_id": deployment, "checks": 200,
              "ready": 0, "deferred": 0, "collection_enabled": client is not None,
              "regression_summary": None, "recovery_summary": None, "evidence": None}
    if client is not None:
        _require(all(value == 0 for value in client.counters.values()))
        before = client.summary()["totals"]
        accepted_before = _count(before["accepted"])
        duplicates_before = _count(before["duplicates"])
    for phase, count, failing in (("baseline", 50, False), ("regression", 50, True),
                                  ("recovery", 100, False)):
        rpc = FixtureRPC(failing)
        for _ in range(count):
            outcome = check_readiness(
                rpc, client=client, deployment_id=deployment, synthetic=True,
                agent_version="fixture-error-v2" if failing else "fixture-healthy-v1",
            )
            result["deferred" if outcome["outcome"] == "failure" else "ready"] += 1
        if client is not None:
            _require(client.flush() == count)
            if phase != "baseline":
                summary = client.summary()
                result[phase + "_summary"] = _selected_summary(summary, deployment)
                if phase == "regression":
                    opened = [i for i in result["regression_summary"]["incidents"]
                              if i["status"] == "open" and i["regression"]]
                    _require(len(opened) == 1)
    if client is not None:
        closed = [i for i in result["recovery_summary"]["incidents"]
                  if i["incident_id"] == opened[0]["incident_id"]
                  and i["status"] == "resolved" and i["recovered"]]
        accepted = _count(summary["totals"]["accepted"]) - accepted_before
        duplicates = _count(summary["totals"]["duplicates"]) - duplicates_before
        counters = client.counters
        _require(len(closed) == 1 and accepted == 200 and duplicates == 0
                 and counters["sent"] == 200 and counters["dropped"] == 0
                 and counters["queued"] == 0)
        result["evidence"] = {"accepted": accepted, "duplicates": duplicates, "sent": counters["sent"],
                              "dropped": counters["dropped"], "queued": counters["queued"],
                              "incident_opened": True, "incident_recovered": True}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="One read-only official Devnet RPC check")
    mode.add_argument("--fixture", action="store_true", help="Synthetic local RPC fixtures (default)")
    parser.add_argument("--endpoint", help="Explicit ReliaMesh endpoint; key from RELIAMESH_API_KEY")
    parser.add_argument("--summary-output", type=Path, help="Create fixture evidence JSON without overwriting")
    args = parser.parse_args(argv)
    if args.live and args.summary_output:
        parser.error("--summary-output requires fixture mode")
    if args.endpoint and not os.environ.get("RELIAMESH_API_KEY"):
        parser.error("--endpoint requires RELIAMESH_API_KEY with ingest and read scopes")
    try:
        client = (Client(endpoint=args.endpoint, api_key=os.environ["RELIAMESH_API_KEY"],
                         timeout=3, max_retries=1, queue_capacity=100, failure_mode="raise")
                  if args.endpoint else None)
        if args.live:
            result = check_readiness(DevnetRPC(), client=client,
                                     deployment_id="solana-live-" + uuid4().hex)
            if client is not None:
                _require(client.flush() == 1)
            print(json.dumps({**result, "collection_enabled": client is not None}))
            return 0 if result["outcome"] == "success" else 2
        result = run_fixture(client)
        if args.summary_output:
            with args.summary_output.open("x", encoding="utf-8") as handle:
                json.dump(result, handle, indent=2, allow_nan=False)
                handle.write("\n")
        print(json.dumps({key: value for key, value in result.items()
                          if key not in {"regression_summary", "recovery_summary"}}))
        return 0
    except (SDKError, ValueError, OSError, KeyError, TypeError):
        print('{"error":"example_or_collection_failed"}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
