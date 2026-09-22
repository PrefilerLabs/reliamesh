"""Exercise real ingestion, regression detection, and recovery with synthetic data.

Use a disposable tenant. This script does not claim provider or customer evidence.
"""

import argparse
import json
from pathlib import Path
from uuid import uuid4

from reliamesh_sdk import Client, event


def demonstrate(endpoint: str, credentials: Path) -> dict:
    key = json.loads(credentials.read_text(encoding="utf-8"))["key"]
    client = Client(endpoint=endpoint, api_key=key, failure_mode="raise")
    deployment = "synthetic-" + uuid4().hex[:12]

    def send(count, outcome, version):
        for _ in range(count):
            client.emit(event(
                deployment_id=deployment, agent_id="synthetic-agent", operation="validation",
                outcome=outcome, failure_type="malformed_output" if outcome == "failure" else None,
                provider="synthetic-provider", model="synthetic-model", model_version=version,
                prompt_version="synthetic-prompt-v1", latency_ms=100,
                input_tokens=50, output_tokens=20, synthetic=True,
            ))
        if client.flush() != count:
            raise RuntimeError("synthetic event acknowledgement count did not match")

    send(50, "success", "synthetic-v1")
    send(50, "failure", "synthetic-v2")
    summary = client.summary()
    stream = next(item for item in summary["streams"]
                  if item["dimensions"]["deployment_id"] == deployment)
    opened = [item for item in client.incidents()["incidents"]
              if item["stream_id"] == stream["stream_id"] and item["status"] == "open"]
    if not opened or "failure_rate_regression" not in opened[0]["signals"] or not opened[0]["synthetic"]:
        raise RuntimeError("expected labeled synthetic regression was not detected")
    print("SYNTHETIC: 50 baseline successes + 50 validation failures opened a regression incident.")
    send(100, "success", "synthetic-v1")
    recovered = next(item for item in client.incidents()["incidents"]
                     if item["incident_id"] == opened[0]["incident_id"])
    if recovered["status"] != "resolved" or recovered["resolution"] != "recovered":
        raise RuntimeError("expected synthetic incident recovery was not detected")
    print("SYNTHETIC: 100 subsequent successes resolved that incident.")
    result = {"synthetic": True, "deployment_id": deployment,
              "incident_id": recovered["incident_id"], "status": recovered["status"],
              "signals": sorted(recovered["signals"]), "sdk_counters": client.counters}
    print(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True, help="Explicit HTTPS or loopback HTTP API URL")
    parser.add_argument("--credentials", type=Path, required=True, help="Tenant credentials JSON file")
    args = parser.parse_args()
    demonstrate(args.endpoint, args.credentials)


if __name__ == "__main__":
    main()
