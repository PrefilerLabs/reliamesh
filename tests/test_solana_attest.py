"""Offline, independently signed transaction and hostile RPC response checks."""

import base64
import copy
import json
import urllib.error

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from reliamesh import solana_attest as adapter

COMMITMENT = "a5" * 32
BLOCKHASH = "1" * 32


@pytest.fixture
def keypair():
    seed = bytes(range(32))
    key = Ed25519PrivateKey.from_private_bytes(seed)
    return {
        "format": adapter.KEY_FORMAT,
        "genesis_hash": adapter.DEVNET_GENESIS,
        "public_key": adapter._encode58(key.public_key().public_bytes_raw()),
        "secret_key_base64": base64.b64encode(seed).decode("ascii"),
    }


@pytest.fixture
def wire(keypair):
    return adapter.build_transaction(COMMITMENT, keypair, BLOCKHASH)


class FakeRPC:
    def __init__(self, wire):
        self.calls = []
        self.genesis = adapter.DEVNET_GENESIS
        self.transaction = {
            "version": "legacy", "slot": 1234, "blockTime": 1_750_000_000,
            "meta": {"err": None, "fee": 5000},
            "transaction": [wire[0], "base64"],
        }
        self.status = {"value": [{"confirmationStatus": "finalized", "err": None, "slot": 1234}]}

    def assert_devnet(self):
        self.calls.append(("getGenesisHash", []))
        if self.genesis != adapter.DEVNET_GENESIS:
            raise adapter.AttestationError("devnet_genesis_mismatch")
        return self.genesis

    def call(self, method, params):
        self.calls.append((method, params))
        return {"getTransaction": self.transaction, "getSignatureStatuses": self.status}[method]


class Response:
    def __init__(self, body, *, status=200, url=adapter.DEVNET_RPC, encoding="identity"):
        self.body, self.status, self.url = body, status, url
        self.headers = {"Content-Encoding": encoding}
        self.read_limit = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return self.url

    def read(self, limit):
        self.read_limit = limit
        return self.body[:limit]


class Opener:
    def __init__(self, *results):
        self.results = list(results)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def rpc_with(*results):
    rpc = adapter.DevnetRPC()
    rpc._opener = Opener(*results)
    return rpc


def result(value):
    return Response(json.dumps({"jsonrpc": "2.0", "id": 1, "result": value}).encode())


def signed_mutation(encoded, keypair, mutate):
    message = bytearray(base64.b64decode(encoded)[65:])
    mutate(message)
    private = Ed25519PrivateKey.from_private_bytes(base64.b64decode(keypair["secret_key_base64"]))
    signature = private.sign(bytes(message))
    wire = b"\x01" + signature + message
    return base64.b64encode(wire).decode(), adapter._encode58(signature)


def test_key_generation_and_canonical_transaction(keypair, wire):
    generated = adapter.generate_keypair()
    assert set(generated) == set(keypair)
    assert len(base64.b64decode(generated["secret_key_base64"])) == 32
    assert adapter.signer_public_key(generated) == generated["public_key"]
    raw = base64.b64decode(wire[0])
    memo = b"reliamesh:attest:v1:sha256:" + COMMITMENT.encode()
    public = Ed25519PrivateKey.from_private_bytes(bytes(range(32))).public_key()
    expected_message = (
        bytes([1, 0, 1, 2]) + public.public_bytes_raw()
        + adapter._decode58(adapter.MEMO_PROGRAM, 32) + bytes(32)
        + bytes([1, 1, 1, 0, len(memo)]) + memo
    )
    assert raw[0] == 1
    assert raw[65:] == expected_message
    public.verify(raw[1:65], expected_message)
    assert adapter._decode58(wire[1], 64) == raw[1:65]
    assert adapter.build_transaction(COMMITMENT, keypair, BLOCKHASH) == wire


@pytest.mark.parametrize("mutation", [
    {"format": "other"}, {"genesis_hash": "mainnet"}, {"public_key": "1" * 32},
    {"secret_key_base64": "c2VjcmV0"}, {"secret_key_base64": "!"}, {"extra": "value"},
])
def test_rejects_invalid_key_files(keypair, mutation):
    with pytest.raises(adapter.AttestationError):
        adapter.signer_public_key(keypair | mutation)


@pytest.mark.parametrize("commitment", ["", "a" * 63, "A" * 64, "g" * 64, 12, None])
def test_rejects_noncanonical_commitment(keypair, commitment):
    with pytest.raises(adapter.AttestationError, match="invalid_commitment"):
        adapter.build_transaction(commitment, keypair, BLOCKHASH)


def test_finalized_verification_returns_only_public_metadata(keypair, wire):
    rpc = FakeRPC(wire)
    receipt = adapter.verify_finalized(COMMITMENT, wire[1], keypair["public_key"], rpc)
    assert receipt == {
        "slot": 1234, "block_time": 1_750_000_000, "fee_lamports": 5000,
        "signer": keypair["public_key"], "signature": wire[1],
        "genesis_hash": adapter.DEVNET_GENESIS, "program_id": adapter.MEMO_PROGRAM,
        "explorer_url": f"https://explorer.solana.com/tx/{wire[1]}?cluster=devnet",
        "verified": True,
    }
    assert rpc.calls == [
        ("getGenesisHash", []),
        ("getTransaction", [wire[1], {"encoding": "base64", "commitment": "finalized", "maxSupportedTransactionVersion": 0}]),
        ("getSignatureStatuses", [[wire[1]], {"searchTransactionHistory": True}]),
    ]


@pytest.mark.parametrize("kind", ["commitment", "signer", "signature", "invalid_signature"])
def test_rejects_wrong_binding_and_invalid_signature(keypair, wire, kind):
    rpc = FakeRPC(wire)
    commitment, signature, signer = COMMITMENT, wire[1], keypair["public_key"]
    if kind == "commitment":
        commitment = "ff" * 32
    elif kind == "signer":
        signer = adapter.generate_keypair()["public_key"]
    elif kind == "signature":
        signature = adapter._encode58(bytes(64))
    else:
        raw = bytearray(base64.b64decode(wire[0]))
        raw[1] ^= 1
        rpc.transaction["transaction"][0] = base64.b64encode(raw).decode()
        signature = adapter._encode58(bytes(raw[1:65]))
    with pytest.raises(adapter.AttestationError, match="transaction_(message_mismatch|signature_mismatch|signature_invalid)"):
        adapter.verify_finalized(commitment, signature, signer, rpc)


@pytest.mark.parametrize("mutate", [
    lambda message: message.__setitem__(0, 2),  # extra required signer
    lambda message: message.__setitem__(2, 0),  # writable program
    lambda message: message.__setitem__(36, message[36] ^ 1),  # different program
    lambda message: message.__setitem__(100, 2),  # extra instruction
    lambda message: message.__setitem__(103, 1),  # unsigned memo account
    lambda message: message.extend(b"\x01\x00\x00"),  # trailing instruction/data
])
def test_even_validly_signed_noncanonical_transactions_rejected(keypair, wire, mutate):
    altered = signed_mutation(wire[0], keypair, mutate)
    with pytest.raises(adapter.AttestationError, match="transaction_message_mismatch"):
        adapter.verify_finalized(COMMITMENT, altered[1], keypair["public_key"], FakeRPC(altered))


@pytest.mark.parametrize("change", [
    {"version": 0}, {"version": None}, {"meta": None},
    {"meta": {"err": {"InstructionError": [0, "InvalidArgument"]}, "fee": 5000}},
    {"meta": {"fee": 5000}}, {"meta": {"err": None, "fee": 10_001}},
    {"meta": {"err": None, "fee": True}}, {"slot": True}, {"slot": -1},
    {"blockTime": "today"}, {"blockTime": float("inf")},
    {"transaction": ["", "json"]}, {"transaction": ["A" * 2000, "base64"]},
])
def test_rejects_invalid_transaction_metadata(keypair, wire, change):
    rpc = FakeRPC(wire)
    rpc.transaction.update(change)
    with pytest.raises(adapter.AttestationError):
        adapter.verify_finalized(COMMITMENT, wire[1], keypair["public_key"], rpc)


@pytest.mark.parametrize("status", [
    None, {}, {"value": []}, {"value": [None]}, {"value": [{"confirmationStatus": "confirmed", "err": None, "slot": 1234}]},
    {"value": [{"confirmationStatus": "finalized", "err": None, "slot": 1235}]},
    {"value": [{"confirmationStatus": "finalized", "err": "failure", "slot": 1234}]},
    {"value": [{"confirmationStatus": "finalized", "slot": 1234}]},
])
def test_rejects_nonfinalized_failed_mismatched_or_missing_status(keypair, wire, status):
    rpc = FakeRPC(wire)
    rpc.status = status
    with pytest.raises(adapter.AttestationError):
        adapter.verify_finalized(COMMITMENT, wire[1], keypair["public_key"], rpc)


def test_missing_history_and_wrong_network_are_not_verified(keypair, wire):
    rpc = FakeRPC(wire)
    rpc.transaction = None
    with pytest.raises(adapter.AttestationError, match="transaction_not_finalized_or_unavailable"):
        adapter.verify_finalized(COMMITMENT, wire[1], keypair["public_key"], rpc)
    rpc.genesis = "not-devnet"
    with pytest.raises(adapter.AttestationError, match="devnet_genesis_mismatch"):
        adapter.verify_finalized(COMMITMENT, wire[1], keypair["public_key"], rpc)
    assert rpc.calls[-1] == ("getGenesisHash", [])


@pytest.mark.parametrize("endpoint", ["http://api.devnet.solana.com", "https://api.mainnet-beta.solana.com", "http://127.0.0.1", "https://api.devnet.solana.com@evil.test", adapter.DEVNET_RPC + "/"])
def test_rpc_endpoint_is_exactly_pinned(endpoint):
    with pytest.raises(adapter.AttestationError, match="devnet_endpoint_required"):
        adapter.DevnetRPC(endpoint)


@pytest.mark.parametrize("timeout", [True, 0, -1, 31, float("inf"), float("nan"), "10"])
def test_rpc_timeout_is_bounded(timeout):
    with pytest.raises(adapter.AttestationError, match="invalid_rpc_timeout"):
        adapter.DevnetRPC(timeout=timeout)


def test_transport_pins_url_even_if_attribute_mutated_and_bounds_read():
    response = result(adapter.DEVNET_GENESIS)
    rpc = rpc_with(response)
    rpc.endpoint = "https://evil.test"
    assert rpc.assert_devnet() == adapter.DEVNET_GENESIS
    request, timeout = rpc._opener.requests[0]
    assert request.full_url == adapter.DEVNET_RPC
    assert request.method == "POST"
    assert json.loads(request.data) == {"jsonrpc": "2.0", "id": 1, "method": "getGenesisHash", "params": []}
    assert timeout == 10
    assert response.read_limit == adapter.MAX_RESPONSE_BYTES + 1


@pytest.mark.parametrize("response,error", [
    (Response(b"{}", status=302), "rpc_response_rejected"),
    (Response(b"{}", url="https://evil.test"), "rpc_response_rejected"),
    (Response(b"{}", encoding="gzip"), "rpc_encoding_rejected"),
    (Response(b"x" * (adapter.MAX_RESPONSE_BYTES + 1)), "rpc_response_too_large"),
    (Response(b"not-json"), "invalid_rpc_response"),
    (Response(b'{"jsonrpc":"2.0","id":1,"id":1,"result":null}'), "invalid_rpc_response"),
    (Response(b'{"jsonrpc":"2.0","id":1,"result":NaN}'), "invalid_rpc_response"),
    (Response(b'{"jsonrpc":"2.0","id":true,"result":null}'), "invalid_rpc_response"),
    (Response(b'{"jsonrpc":"2.0","id":2,"result":null}'), "invalid_rpc_response"),
    (Response(b'{"jsonrpc":"2.0","id":1}'), "invalid_rpc_response"),
    (Response(b'{"jsonrpc":"2.0","id":1,"error":{"message":"secret-provider-payload"}}'), "rpc_request_failed"),
    (urllib.error.URLError("secret-provider-payload"), "rpc_unavailable"),
])
def test_rpc_rejects_hostile_responses_without_payload_echo(response, error):
    with pytest.raises(adapter.AttestationError) as failure:
        rpc_with(response).call("getGenesisHash", [])
    assert str(failure.value) == error
    assert "secret-provider-payload" not in str(failure.value)


def test_redirect_handler_rejects_following_other_location():
    with pytest.raises(adapter.AttestationError, match="rpc_redirect_rejected"):
        adapter._NoRedirect().redirect_request(None, None, 302, "secret", {}, "https://evil.test")


@pytest.mark.parametrize("method,params", [
    ("getClusterNodes", []), ("getBalance", {}), ("getBalance", [0] * 9),
    ("getBalance", [float("nan")]), ("getBalance", ["x" * 8192]),
])
def test_rejects_unsupported_or_unbounded_requests_before_transport(method, params):
    rpc = rpc_with()
    with pytest.raises(adapter.AttestationError):
        rpc.call(method, params)
    assert rpc._opener.requests == []


def test_send_requires_devnet_and_exact_memo_transaction(keypair, wire):
    config = {"encoding": "base64", "skipPreflight": False, "preflightCommitment": "finalized", "maxRetries": 0}
    rpc = rpc_with(result(adapter.DEVNET_GENESIS), result(wire[1]))
    assert rpc.call("sendTransaction", [wire[0], config]) == wire[1]
    assert [json.loads(request.data)["method"] for request, _ in rpc._opener.requests] == ["getGenesisHash", "sendTransaction"]
    wrong_network = rpc_with(result("other-genesis"))
    with pytest.raises(adapter.AttestationError, match="devnet_genesis_mismatch"):
        wrong_network.call("sendTransaction", [wire[0], config])
    assert len(wrong_network._opener.requests) == 1
    changed = signed_mutation(wire[0], keypair, lambda message: message.__setitem__(36, message[36] ^ 1))
    rejected = rpc_with()
    with pytest.raises(adapter.AttestationError, match="transaction_message_mismatch"):
        rejected.call("sendTransaction", [changed[0], config])
    assert rejected._opener.requests == []


@pytest.mark.parametrize("change", [{"skipPreflight": True}, {"encoding": "base58"}, {"preflightCommitment": "processed"}, {"maxRetries": 4}, {"maxRetries": True}, {"extra": 1}])
def test_send_rejects_unsafe_configuration(wire, change):
    rpc = rpc_with()
    config = {"encoding": "base64", "skipPreflight": False, "preflightCommitment": "finalized"} | change
    with pytest.raises(adapter.AttestationError, match="invalid_send_request"):
        rpc.call("sendTransaction", [wire[0], config])
    assert rpc._opener.requests == []


def test_airdrop_requires_genesis_check_and_bounded_amount(keypair):
    rpc = rpc_with(result(adapter.DEVNET_GENESIS), result("fake-signature"))
    assert rpc.call("requestAirdrop", [keypair["public_key"], 1_000_000]) == "fake-signature"
    assert [json.loads(request.data)["method"] for request, _ in rpc._opener.requests] == ["getGenesisHash", "requestAirdrop"]
    for amount in [True, 0, -1, 2_000_000_001]:
        rejected = rpc_with()
        with pytest.raises(adapter.AttestationError, match="invalid_airdrop_request"):
            rejected.call("requestAirdrop", [keypair["public_key"], amount])
        assert rejected._opener.requests == []


def test_missing_optional_dependency_is_fixed_error(monkeypatch, keypair):
    import builtins

    original_import = builtins.__import__

    def without_crypto(name, *args, **kwargs):
        if name.startswith("cryptography"):
            raise ImportError("private installation details")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_crypto)
    with pytest.raises(adapter.AttestationError, match="^solana_optional_dependency_missing$"):
        adapter.signer_public_key(copy.deepcopy(keypair))


@pytest.mark.parametrize("confirmation", ["processed", "confirmed", "finalized"])
@pytest.mark.parametrize("error", [None, "AccountNotFound", {"InstructionError": [0, "private-provider-detail"]}])
def test_submission_status_sanitizes_outcome_and_uses_only_pinned_reads(wire, confirmation, error):
    rpc = rpc_with(result(adapter.DEVNET_GENESIS), result({"value": [{
        "confirmationStatus": confirmation, "slot": 99, "err": error,
    }]}), result(101))
    actual = adapter.submission_status(wire[1], 100, rpc)
    assert actual == {
        "signature": wire[1], "confirmation_status": confirmation,
        "transaction_error": error is not None,
        "current_block_height": 101, "last_valid_block_height": 100,
        "blockhash_expired": True, "automatic_retry_allowed": False,
    }
    assert "private-provider-detail" not in json.dumps(actual)
    requests = [json.loads(request.data) for request, _ in rpc._opener.requests]
    assert [(request["method"], request["params"]) for request in requests] == [
        ("getGenesisHash", []),
        ("getSignatureStatuses", [[wire[1]], {"searchTransactionHistory": True}]),
        ("getBlockHeight", [{"commitment": "finalized"}]),
    ]


@pytest.mark.parametrize("height,expired", [(99, False), (100, False), (101, True)])
def test_missing_submission_history_stays_ambiguous_even_after_expiry(wire, height, expired):
    rpc = rpc_with(result(adapter.DEVNET_GENESIS), result({"value": [None]}), result(height))
    status = adapter.submission_status(wire[1], 100, rpc)
    assert status["confirmation_status"] == "missing"
    assert status["transaction_error"] is None
    assert status["blockhash_expired"] is expired
    assert status["automatic_retry_allowed"] is False
    assert "verified" not in status


@pytest.mark.parametrize("height", [None, True, 0, -1, 1.5, "100", 2**63])
def test_submission_status_rejects_invalid_pending_height_before_rpc(wire, height):
    rpc = rpc_with()
    with pytest.raises(adapter.AttestationError, match="^invalid_last_valid_block_height$"):
        adapter.submission_status(wire[1], height, rpc)
    assert rpc._opener.requests == []


@pytest.mark.parametrize("signature", [None, 1, "", "0" * 88, "1" * 63, "1" * 65])
def test_submission_status_rejects_invalid_signature_before_rpc(signature):
    rpc = rpc_with()
    with pytest.raises(adapter.AttestationError, match="^invalid_base58$"):
        adapter.submission_status(signature, 100, rpc)
    assert rpc._opener.requests == []


def test_submission_status_rejects_wrong_genesis_before_status_request(wire):
    rpc = rpc_with(result("not-devnet"))
    with pytest.raises(adapter.AttestationError, match="^devnet_genesis_mismatch$"):
        adapter.submission_status(wire[1], 100, rpc)
    assert len(rpc._opener.requests) == 1


@pytest.mark.parametrize("statuses", [
    None, [], {}, {"value": []}, {"value": [None, None]},
    {"value": ["private-provider-detail"]},
    {"value": [{"confirmationStatus": "unknown", "slot": 1, "err": None}]},
    {"value": [{"confirmationStatus": "confirmed", "slot": True, "err": None}]},
    {"value": [{"confirmationStatus": "confirmed", "slot": 1}]},
    {"value": [{"confirmationStatus": "confirmed", "slot": 1, "err": False}]},
    {"value": [{"confirmationStatus": "confirmed", "slot": 1, "err": ["private-provider-detail"]}]},
])
def test_submission_status_rejects_malformed_status_without_echo(wire, statuses):
    rpc = rpc_with(result(adapter.DEVNET_GENESIS), result(statuses))
    with pytest.raises(adapter.AttestationError, match="^invalid_signature_status$"):
        adapter.submission_status(wire[1], 100, rpc)
    assert len(rpc._opener.requests) == 2


@pytest.mark.parametrize("height", [None, True, -1, 1.5, "private-provider-detail", {}, 2**63])
def test_submission_status_rejects_malformed_current_height_without_echo(wire, height):
    rpc = rpc_with(result(adapter.DEVNET_GENESIS), result({"value": [None]}), result(height))
    with pytest.raises(adapter.AttestationError, match="^invalid_block_height$"):
        adapter.submission_status(wire[1], 100, rpc)
