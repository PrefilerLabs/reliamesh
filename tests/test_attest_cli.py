import json

import pytest

from reliamesh.attest_cli import publish
from reliamesh.attestation import prepare
from reliamesh.solana_attest import DEVNET_GENESIS, generate_keypair


def make_bundle(key):
    return prepare({"server_version": "0.2.0", "generated_at": "2026-09-29T00:00:00.000000Z",
                    "synthetic": True, "stream_count": 0, "baseline_samples": 0,
                    "baseline_failures": 0, "current_samples": 0, "current_failures": 0,
                    "open_incidents": 0, "resolved_incidents": 0, "expired_incidents": 0}, key["public_key"])


class RPC:
    def __init__(self, output, *, fee=5000, balance=10000, failed_send=False, status="finalized"):
        self.output, self.fee, self.balance = output, fee, balance
        self.failed_send, self.status = failed_send, status
        self.calls = []
        self.signature = None

    def assert_devnet(self):
        return DEVNET_GENESIS

    def call(self, method, params):
        self.calls.append(method)
        if method == "getLatestBlockhash":
            return {"value": {"blockhash": "11111111111111111111111111111111", "lastValidBlockHeight": 100}}
        if method == "getFeeForMessage":
            return {"value": self.fee}
        if method == "getBalance":
            return {"value": self.balance}
        if method == "sendTransaction":
            pending = self.output.with_name(self.output.name + ".pending.json")
            assert pending.exists() and not self.output.exists()
            self.signature = json.loads(pending.read_text())["signature"]
            self.wire = params[0]
            if self.failed_send:
                raise ValueError("network failure after potential submission")
            return self.signature
        if method == "getSignatureStatuses":
            return {"value": [{"confirmationStatus": self.status, "err": None, "slot": 99}]}
        if method == "getTransaction":
            return {"version": "legacy", "slot": 99, "blockTime": 1790630000,
                    "meta": {"err": None, "fee": self.fee}, "transaction": [self.wire, "base64"]}
        raise AssertionError(method)


def test_publish_receipt_is_written_only_after_real_signature_verification(tmp_path):
    key = generate_keypair()
    output = tmp_path / "receipt.json"
    rpc = RPC(output)
    receipt = publish(make_bundle(key), key, output, rpc=rpc)
    assert receipt["verified"] is True
    assert json.loads(output.read_text()) == receipt
    assert rpc.calls.count("sendTransaction") == 1


@pytest.mark.parametrize("fee,balance", [(None, 10000), (True, 10000), (10001, 100000), (0, 10000), (5000, 4999)])
def test_preflight_budget_rejection_sends_nothing(tmp_path, fee, balance):
    key = generate_keypair()
    output = tmp_path / "receipt.json"
    rpc = RPC(output, fee=fee, balance=balance)
    with pytest.raises(ValueError):
        publish(make_bundle(key), key, output, rpc=rpc)
    assert "sendTransaction" not in rpc.calls
    assert list(tmp_path.iterdir()) == []


def test_ambiguous_submission_preserves_signature_and_refuses_implicit_retry(tmp_path):
    key = generate_keypair()
    output = tmp_path / "receipt.json"
    rpc = RPC(output, failed_send=True)
    bundle = make_bundle(key)
    with pytest.raises(ValueError):
        publish(bundle, key, output, rpc=rpc)
    assert not output.exists()
    assert json.loads(output.with_name(output.name + ".pending.json").read_text())["signature"] == rpc.signature
    with pytest.raises(ValueError, match="receipt already exists"):
        publish(bundle, key, output, rpc=rpc)
    assert rpc.calls.count("sendTransaction") == 1


def test_unfinalized_submission_never_claims_success(tmp_path):
    key = generate_keypair()
    output = tmp_path / "receipt.json"
    rpc = RPC(output, status="confirmed")
    with pytest.raises(ValueError, match="finalization pending"):
        publish(make_bundle(key), key, output, rpc=rpc, wait_seconds=0)
    assert not output.exists()
    assert output.with_name(output.name + ".pending.json").exists()


def test_wrong_key_rejected_before_any_rpc(tmp_path):
    key, wrong = generate_keypair(), generate_keypair()
    output = tmp_path / "receipt.json"
    rpc = RPC(output)
    with pytest.raises(ValueError, match="signer"):
        publish(make_bundle(key), wrong, output, rpc=rpc)
    assert rpc.calls == []


@pytest.mark.parametrize("malformed", [None, [], {}, {"value": []}, {"value": [None, None]}, {"value": ["private response"]}])
def test_malformed_status_leaves_only_pending_receipt(tmp_path, malformed):
    key = generate_keypair()
    output = tmp_path / "receipt.json"
    rpc = RPC(output)
    original = rpc.call

    def call(method, params):
        return malformed if method == "getSignatureStatuses" else original(method, params)

    rpc.call = call
    with pytest.raises(ValueError, match="invalid signature status"):
        publish(make_bundle(key), key, output, rpc=rpc)
    assert not output.exists()
    assert output.with_name(output.name + ".pending.json").exists()


def test_cli_status_is_read_only_and_missing_history_is_ambiguous(tmp_path, monkeypatch, capsys):
    from reliamesh.attest_cli import main
    from reliamesh.solana_attest import build_transaction

    key = generate_keypair()
    _, signature = build_transaction("a" * 64, key, "11111111111111111111111111111111")
    path = tmp_path / "pending.json"
    path.write_text(json.dumps({"signature": signature, "last_valid_block_height": 100}))
    calls = []

    class StatusRPC:
        def assert_devnet(self):
            return DEVNET_GENESIS

        def call(self, method, params):
            calls.append(method)
            if method == "getSignatureStatuses":
                return {"value": [None]}
            assert method == "getBlockHeight"
            return 101

    monkeypatch.setattr("reliamesh.solana_attest.DevnetRPC", StatusRPC)
    assert main(["status", "--pending", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["confirmation_status"] == "missing"
    assert result["blockhash_expired"] is True
    assert result["automatic_retry_allowed"] is False
    assert calls == ["getSignatureStatuses", "getBlockHeight"]
    assert path.exists()


def test_core_has_no_solana_import_or_network_requirement(monkeypatch, tmp_path):
    import builtins

    original = builtins.__import__

    def blocked(name, *args, **kwargs):
        if "solana" in name or name.startswith("cryptography"):
            raise AssertionError("core attempted to load optional chain code")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    from fastapi.testclient import TestClient

    from reliamesh.api import create_app
    from reliamesh.config import Settings

    with TestClient(create_app(Settings(sqlite_path=str(tmp_path / "core.db")))) as client:
        assert client.get("/health").status_code == 200
