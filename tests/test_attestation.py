import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from reliamesh_sdk import event

from reliamesh.attestation import (
    CommitmentBundle,
    WindowReport,
    canonical_bytes,
    commitment,
    prepare,
    read_document,
    report_from_summary,
)
from reliamesh.detection import process_events, summarize
from reliamesh.protocol import Event

SIGNER = "11111111111111111111111111111111"


def report():
    return {"server_version": "0.2.0", "generated_at": "2026-09-29T00:00:00.000000Z",
            "synthetic": True, "stream_count": 1, "baseline_samples": 50,
            "baseline_failures": 0, "current_samples": 50, "current_failures": 50,
            "open_incidents": 1, "resolved_incidents": 0, "expired_incidents": 0}


def test_salt_domain_and_canonicalization():
    first, second = prepare(report(), SIGNER), prepare(report(), SIGNER)
    assert first.salt_hex != second.salt_hex
    assert commitment(first) != commitment(second)
    reordered = dict(reversed(list(first.model_dump().items())))
    assert commitment(CommitmentBundle.model_validate(reordered)) == commitment(first)
    assert canonical_bytes(first).isascii()
    modified = first.model_dump()
    modified["report"]["current_failures"] = 49
    assert commitment(CommitmentBundle.model_validate(modified)) != commitment(first)
    modified = first.model_dump()
    modified["expected_signer"] = "21111111111111111111111111111111"
    assert commitment(CommitmentBundle.model_validate(modified)) != commitment(first)


@pytest.mark.parametrize("changes", [
    {"prompt": "forbidden"}, {"current_failures": 51}, {"current_samples": 51},
    {"stream_count": True}, {"synthetic": "true"}, {"current_samples": 50.0},
    {"resolved_incidents": 32}, {"generated_at": "2026-09-29T00:00:00"},
    {"generated_at": "2026-09-29T01:00:00.000000+01:00"},
    {"server_version": "customer-label"},
])
def test_report_strict_privacy_contract(changes):
    with pytest.raises(ValidationError):
        WindowReport.model_validate(report() | changes)


@pytest.mark.parametrize("changes", [
    {"network": "mainnet-beta"}, {"genesis_hash": "other"}, {"salt_hex": "0" * 63},
    {"expected_signer": "http://attacker"}, {"rpc": "https://attacker.invalid"},
])
def test_bundle_rejects_different_network_or_extra_input(changes):
    bundle = prepare(report(), SIGNER).model_dump()
    with pytest.raises(ValidationError):
        CommitmentBundle.model_validate(bundle | changes)


@pytest.mark.parametrize("content", [b'{"x":1,"x":2}', b'{"x":NaN}', b'[]', b'{', b'\xff', b' ' * 16385])
def test_document_rejects_ambiguous_or_large_json(tmp_path, content):
    path = tmp_path / "input.json"
    path.write_bytes(content)
    with pytest.raises(ValueError):
        read_document(path)


def test_projection_separates_real_and_synthetic_and_ignores_lifetime_totals():
    now = datetime.now(UTC)
    state = {}
    for synthetic, outcome, count in ((False, "success", 20), (True, "success", 50), (True, "failure", 50)):
        events = [Event.model_validate(event(deployment_id="private-deployment", agent_id="private-agent",
                   operation="tool", outcome=outcome, synthetic=synthetic, timestamp=now,
                   failure_type="timeout" if outcome == "failure" else None)) for _ in range(count)]
        state, _ = process_events(state, events, now)
    summary = summarize(state, now)
    summary["totals"]["accepted"] = 99999
    synthetic_report = report_from_summary(summary, synthetic=True, server_version="0.2.0", generated_at=now)
    assert synthetic_report.baseline_samples == 50
    assert synthetic_report.current_failures == 50
    assert synthetic_report.open_incidents == 1
    real_report = report_from_summary(summary, synthetic=False, server_version="0.2.0", generated_at=now)
    assert real_report.current_samples == 20
    assert real_report.open_incidents == 0
    encoded = json.dumps(synthetic_report.model_dump())
    assert "private-" not in encoded and "99999" not in encoded


def test_cli_keeps_bad_input_out_of_error_output(tmp_path, capsys):
    from reliamesh.attest_cli import main

    path = tmp_path / "report.json"
    path.write_text(json.dumps(report() | {"prompt": "DO-NOT-ECHO-THIS"}))
    with pytest.raises(SystemExit) as error:
        main(["prepare", "--report", str(path), "--expected-signer", SIGNER,
              "--output", str(tmp_path / "out.json")])
    assert error.value.code == 1
    assert "DO-NOT-ECHO-THIS" not in capsys.readouterr().err
    assert not (tmp_path / "out.json").exists()


def test_public_disclosure_is_a_stable_commitment_vector():
    root = Path(__file__).resolve().parents[1] / "docs/evidence/solana-devnet"
    bundle = CommitmentBundle.model_validate(read_document(root / "regression-bundle.json"))
    assert bundle.report.synthetic is True
    assert commitment(bundle) == "d15649d438b863606f4b77fb490573136fc5492fe9603db97e572385aa26e10e"
    altered = bundle.model_dump()
    altered["report"]["current_failures"] = 49
    assert commitment(CommitmentBundle.model_validate(altered)) != commitment(bundle)
