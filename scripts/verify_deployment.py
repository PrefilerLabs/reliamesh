"""Exercise disposable synthetic tenants through a real deployed HTTPS API.

Outputs only evidence, never credentials. Deletes its synthetic tenants on exit.
"""

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from reliamesh_sdk import Client, event


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def verify(endpoint, admin_path):
    parsed = urlsplit(endpoint)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("Expected a trusted HTTPS API endpoint")
    admin = json.loads(Path(admin_path).read_text())["key"]
    tenants = []
    opener = build_opener(NoRedirect())

    def request(method, path, key=None, body=None, extra=None):
        headers = {"Content-Type": "application/json", **(extra or {})}
        if key:
            headers["Authorization"] = "Bearer " + key
        payload = None if body is None else json.dumps(body).encode()
        req = Request(endpoint + path, data=payload, method=method, headers=headers)  # noqa: S310
        try:
            with opener.open(req, timeout=30) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            status = error.code
            error.close()
            return status, None

    try:
        for _ in range(2):
            tenant_id = "verification-" + uuid4().hex[:16]
            status, credentials = request("POST", "/v1/admin/tenants", admin, {"tenant_id": tenant_id})
            assert status == 201, f"Tenant provisioning failed: {status}"
            tenants.append(credentials)
        key = tenants[0]["key"]
        client = Client(endpoint=endpoint, api_key=key, timeout=15, failure_mode="raise")
        second = Client(endpoint=endpoint, api_key=tenants[1]["key"], timeout=15, failure_mode="raise")

        def emit(count, outcome):
            for _ in range(count):
                client.emit(event(deployment_id="synthetic-production-check", agent_id="synthetic-agent",
                                  operation="validation", outcome=outcome, synthetic=True,
                                  failure_type="malformed_output" if outcome == "failure" else None,
                                  provider="synthetic-provider", model="synthetic-model",
                                  model_version="bad-v2" if outcome == "failure" else "good-v1",
                                  latency_ms=100, input_tokens=20, output_tokens=10))
            assert client.flush() == count

        emit(50, "success")
        emit(50, "failure")
        incidents = client.incidents()["incidents"]
        assert len(incidents) == 1 and incidents[0]["status"] == "open"
        assert incidents[0]["synthetic"] and "failure_rate_regression" in incidents[0]["signals"]
        assert second.summary()["totals"]["accepted"] == 0
        emit(100, "success")
        resolved = client.incidents()["incidents"][0]
        assert resolved["incident_id"] == incidents[0]["incident_id"]
        assert resolved["status"] == "resolved"
        sample = event(deployment_id="synthetic-production-check", agent_id="synthetic-agent",
                       operation="validation", outcome="success", synthetic=True)
        status, ack = request("POST", "/v1/events", key, {"events": [sample]})
        assert status == 200 and ack["accepted"] == 1
        status, ack = request("POST", "/v1/events", key, {"events": [sample]})
        assert status == 200 and ack["duplicates"] == 1
        assert request("POST", "/v1/events", key, {"events": [{**sample, "prompt": "synthetic-disallowed-content"}]})[0] == 422
        time.sleep(1.1)  # Stay below documented per-instance request cap during verification.
        assert request("GET", "/v1/summary")[0] == 401
        status, issued = request("POST", "/v1/keys", key, {"scopes": ["ingest"]})
        assert status == 201
        assert request("GET", "/v1/summary", issued["key"])[0] == 403
        assert request("DELETE", "/v1/keys/" + issued["key_id"], key)[0] == 200
        assert request("POST", "/v1/events", issued["key"], {"events": [sample]})[0] == 401
        assert request("GET", "/ready", key)[0] == 200
        assert request("GET", "/v1/network", key)[1]["status"] == "disabled"
        return {"verified_at": datetime.now(UTC).isoformat(), "endpoint": endpoint,
                "synthetic": True, "accepted": 201, "duplicate_replay": "deduplicated",
                "regression": "detected", "recovery": "resolved", "tenant_isolation": "passed",
                "privacy_schema": "passed", "scope_enforcement": "passed", "revocation": "passed",
                "storage_readiness": "passed", "network_intelligence": "disabled"}
    finally:
        time.sleep(1.1)
        for tenant in tenants:
            status, _ = request("DELETE", "/v1/tenant", tenant["key"],
                                extra={"X-ReliaMesh-Confirm-Delete": tenant["tenant_id"]})
            assert status == 200, f"Synthetic tenant cleanup failed: {status}"
            assert request("GET", "/v1/summary", tenant["key"])[0] == 401


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--admin-file", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = verify(args.endpoint.rstrip("/"), args.admin_file)
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))

