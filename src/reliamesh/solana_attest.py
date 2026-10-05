"""Optional, explicit Devnet Memo commitments. No dependency on the core data path.

Only a constrained legacy transaction is supported: one payer, one signature,
and one signed Memo instruction containing a SHA256 commitment. Finality is
reported by the fixed HTTPS RPC; this module is not a consensus light client.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
import urllib.error
import urllib.request

DEVNET_RPC = "https://api.devnet.solana.com"
DEVNET_GENESIS = "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG"
MEMO_PROGRAM = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
MEMO_PREFIX = b"reliamesh:attest:v1:sha256:"
KEY_FORMAT = "reliamesh-solana-devnet-key/v1"
MAX_RESPONSE_BYTES = 128 * 1024
MAX_TRANSACTION_BYTES = 1232
MAX_FEE_LAMPORTS = 10_000
_BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_VALUES = {character: index for index, character in enumerate(_BASE58)}
_RPC_METHODS = frozenset({
    "getGenesisHash", "getBalance", "getLatestBlockhash", "getFeeForMessage",
    "requestAirdrop", "sendTransaction", "getSignatureStatuses", "getTransaction",
    "getBlockHeight",
})


class AttestationError(ValueError):
    """Fixed public error codes, never key material or provider error payloads."""


def _crypto():
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
    except ImportError:
        raise AttestationError("solana_optional_dependency_missing") from None
    return Ed25519PrivateKey, Ed25519PublicKey, InvalidSignature


def _encode58(value: bytes) -> str:
    leading_zeroes = len(value) - len(value.lstrip(b"\0"))
    number = int.from_bytes(value, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = _BASE58[remainder] + encoded
    return "1" * leading_zeroes + encoded


def _decode58(value: str, size: int) -> bytes:
    if type(value) is not str or not 1 <= len(value) <= (88 if size == 64 else 44):
        raise AttestationError("invalid_base58")
    number = 0
    for character in value:
        digit = _BASE58_VALUES.get(character)
        if digit is None:
            raise AttestationError("invalid_base58")
        number = number * 58 + digit
    decoded = b"\0" * (len(value) - len(value.lstrip("1")))
    if number:
        decoded += number.to_bytes((number.bit_length() + 7) // 8, "big")
    if len(decoded) != size or _encode58(decoded) != value:
        raise AttestationError("invalid_base58")
    return decoded


def _decode64(value: str, limit: int) -> bytes:
    if type(value) is not str or len(value) > 4 * ((limit + 2) // 3):
        raise AttestationError("invalid_base64")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise AttestationError("invalid_base64") from None
    if len(decoded) > limit or base64.b64encode(decoded).decode("ascii") != value:
        raise AttestationError("invalid_base64")
    return decoded


def _commitment(value: str) -> bytes:
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise AttestationError("invalid_commitment")
    return value.encode("ascii")


def _message(commitment: str, signer: bytes, blockhash: bytes) -> bytes:
    memo = MEMO_PREFIX + _commitment(commitment)
    if len(signer) != 32 or len(blockhash) != 32:
        raise AttestationError("invalid_transaction")
    # All compact-u16 values are below 128 and have exactly one canonical byte.
    return (
        b"\x01\x00\x01"  # one writable signer, one readonly unsigned program
        + b"\x02" + signer + _decode58(MEMO_PROGRAM, 32)
        + blockhash
        + b"\x01\x01\x01\x00"  # one instruction, program index 1, accounts [0]
        + bytes([len(memo)]) + memo
    )


def _load_key(keypair: dict):
    if type(keypair) is not dict or set(keypair) != {
        "format", "genesis_hash", "public_key", "secret_key_base64",
    }:
        raise AttestationError("invalid_keypair")
    if keypair["format"] != KEY_FORMAT or keypair["genesis_hash"] != DEVNET_GENESIS:
        raise AttestationError("invalid_keypair")
    secret = _decode64(keypair["secret_key_base64"], 32)
    if len(secret) != 32:
        raise AttestationError("invalid_keypair")
    public = _decode58(keypair["public_key"], 32)
    private_class, _, _ = _crypto()
    key = private_class.from_private_bytes(secret)
    if key.public_key().public_bytes_raw() != public:
        raise AttestationError("keypair_public_key_mismatch")
    return key


def generate_keypair() -> dict:
    """Return private key material for an owner-controlled private file only."""
    private_class, _, _ = _crypto()
    key = private_class.generate()
    return {
        "format": KEY_FORMAT,
        "genesis_hash": DEVNET_GENESIS,
        "public_key": _encode58(key.public_key().public_bytes_raw()),
        "secret_key_base64": base64.b64encode(key.private_bytes_raw()).decode("ascii"),
    }


def signer_public_key(keypair: dict) -> str:
    return _encode58(_load_key(keypair).public_key().public_bytes_raw())


def build_transaction(commitmentHex: str, keypair: dict, blockhash: str) -> tuple[str, str]:
    """Build and sign one exact Memo-only legacy transaction, without networking."""
    key = _load_key(keypair)
    message = _message(commitmentHex, key.public_key().public_bytes_raw(), _decode58(blockhash, 32))
    signature = key.sign(message)
    wire = b"\x01" + signature + message
    return base64.b64encode(wire).decode("ascii"), _encode58(signature)


def _verify_wire(encoded: str, commitment: str, signature: str, signer: str) -> None:
    wire = _decode64(encoded, MAX_TRANSACTION_BYTES)
    expected_signature = _decode58(signature, 64)
    signer_bytes = _decode58(signer, 32)
    # Signature count + signature + fixed header/accounts + 32-byte blockhash.
    if len(wire) < 165 or wire[0] != 1 or wire[1:65] != expected_signature:
        raise AttestationError("transaction_signature_mismatch")
    message = wire[65:]
    expected_message = _message(commitment, signer_bytes, message[68:100])
    if message != expected_message:
        raise AttestationError("transaction_message_mismatch")
    _, public_class, invalid_signature = _crypto()
    try:
        public_class.from_public_bytes(signer_bytes).verify(expected_signature, message)
    except invalid_signature:
        raise AttestationError("transaction_signature_invalid") from None


def _validate_write(method: str, params: list) -> None:
    if method == "requestAirdrop":
        if len(params) not in (2, 3):
            raise AttestationError("invalid_airdrop_request")
        _decode58(params[0], 32)
        if type(params[1]) is not int or not 1 <= params[1] <= 2_000_000_000:
            raise AttestationError("invalid_airdrop_request")
        if len(params) == 3 and params[2] not in (
            {"commitment": "confirmed"}, {"commitment": "finalized"},
        ):
            raise AttestationError("invalid_airdrop_request")
    elif method == "sendTransaction":
        if len(params) != 2 or type(params[1]) is not dict:
            raise AttestationError("invalid_send_request")
        config = params[1]
        if (
            set(config) - {"encoding", "skipPreflight", "preflightCommitment", "maxRetries"}
            or config.get("encoding") != "base64"
            or config.get("skipPreflight", False) is not False
            or config.get("preflightCommitment") not in ("confirmed", "finalized")
            or type(config.get("maxRetries", 2)) is not int
            or not 0 <= config.get("maxRetries", 2) <= 3
        ):
            raise AttestationError("invalid_send_request")
        wire = _decode64(params[0], MAX_TRANSACTION_BYTES)
        # The exact message has a fixed-size prefix followed by the fixed memo.
        prefix_size = 65 + 105
        if len(wire) != prefix_size + len(MEMO_PREFIX) + 64:
            raise AttestationError("invalid_send_transaction")
        try:
            commitment = wire[prefix_size + len(MEMO_PREFIX):].decode("ascii")
        except UnicodeDecodeError:
            raise AttestationError("invalid_send_transaction") from None
        _verify_wire(params[0], commitment, _encode58(wire[1:65]), _encode58(wire[69:101]))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AttestationError("rpc_redirect_rejected")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AttestationError("invalid_rpc_response")
        result[key] = value
    return result


def _invalid_number(value):
    raise AttestationError("invalid_rpc_response")


class DevnetRPC:
    """Bounded JSON-RPC on the official Devnet endpoint only; no redirects/proxies."""

    def __init__(self, endpoint: str = DEVNET_RPC, timeout: float = 10):
        if endpoint != DEVNET_RPC:
            raise AttestationError("devnet_endpoint_required")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise AttestationError("invalid_rpc_timeout")
        self.endpoint, self.timeout = endpoint, timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def assert_devnet(self) -> str:
        if self.call("getGenesisHash", []) != DEVNET_GENESIS:
            raise AttestationError("devnet_genesis_mismatch")
        return DEVNET_GENESIS

    def call(self, method: str, params: list):
        if type(method) is not str or method not in _RPC_METHODS:
            raise AttestationError("rpc_method_not_allowed")
        if type(params) is not list or len(params) > 8:
            raise AttestationError("invalid_rpc_parameters")
        if method in {"requestAirdrop", "sendTransaction"}:
            _validate_write(method, params)
            self.assert_devnet()
        try:
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError, RecursionError):
            raise AttestationError("invalid_rpc_parameters") from None
        if len(body) > 8192:
            raise AttestationError("rpc_request_too_large")
        # The transport destination is pinned independently of mutable instance
        # attributes; redirects and environment proxy routing are disabled above.
        request = urllib.request.Request(DEVNET_RPC, data=body, headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")  # noqa: S310 -- fixed HTTPS URL, redirects disabled
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                if response.status != 200 or response.geturl() != DEVNET_RPC:
                    raise AttestationError("rpc_response_rejected")
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise AttestationError("rpc_encoding_rejected")
                encoded = response.read(MAX_RESPONSE_BYTES + 1)
        except AttestationError:
            raise
        except (OSError, urllib.error.URLError, ValueError):
            raise AttestationError("rpc_unavailable") from None
        if len(encoded) > MAX_RESPONSE_BYTES:
            raise AttestationError("rpc_response_too_large")
        try:
            decoded = json.loads(encoded, object_pairs_hook=_unique_object, parse_constant=_invalid_number)
        except (ValueError, UnicodeError, RecursionError):
            raise AttestationError("invalid_rpc_response") from None
        if type(decoded) is not dict or decoded.get("jsonrpc") != "2.0" or type(decoded.get("id")) is not int or decoded["id"] != 1:
            raise AttestationError("invalid_rpc_response")
        if "error" in decoded:
            raise AttestationError("rpc_request_failed")
        if "result" not in decoded:
            raise AttestationError("invalid_rpc_response")
        return decoded["result"]


def _integer(value, maximum: int = 2**63 - 1) -> bool:
    return type(value) is int and 0 <= value <= maximum


def submission_status(signature: str, last_valid_block_height: int, rpc: DevnetRPC) -> dict:
    """Read pending submission status without authorizing another transaction.

    Missing history remains ambiguous even after blockhash expiry: an RPC may
    have pruned a transaction that landed. Finalized status alone does not
    verify the commitment or signer; use verify_finalized for that assertion.
    """
    _decode58(signature, 64)
    if not _integer(last_valid_block_height) or last_valid_block_height == 0:
        raise AttestationError("invalid_last_valid_block_height")
    rpc.assert_devnet()
    statuses = rpc.call("getSignatureStatuses", [[signature], {"searchTransactionHistory": True}])
    if type(statuses) is not dict or type(statuses.get("value")) is not list or len(statuses["value"]) != 1:
        raise AttestationError("invalid_signature_status")
    status = statuses["value"][0]
    confirmation_status, transaction_error = "missing", None
    if status is not None:
        if (
            type(status) is not dict
            or status.get("confirmationStatus") not in ("processed", "confirmed", "finalized")
            or not _integer(status.get("slot"))
            or "err" not in status
            or (status["err"] is not None and type(status["err"]) not in (str, dict))
        ):
            raise AttestationError("invalid_signature_status")
        confirmation_status = status["confirmationStatus"]
        # Only a boolean classification leaves this function, never error data.
        transaction_error = status["err"] is not None
    height = rpc.call("getBlockHeight", [{"commitment": "finalized"}])
    if not _integer(height):
        raise AttestationError("invalid_block_height")
    return {
        "signature": signature,
        "confirmation_status": confirmation_status,
        "transaction_error": transaction_error,
        "current_block_height": height,
        "last_valid_block_height": last_valid_block_height,
        "blockhash_expired": height > last_valid_block_height,
        "automatic_retry_allowed": False,
    }


def verify_finalized(commitmentHex: str, signature: str, expected_signer: str, rpc: DevnetRPC) -> dict:
    """Verify payload binding/signature locally and finalized inclusion via Devnet RPC.

    expected_signer is caller-trusted input, not an identity learned from a receipt.
    Missing/pruned history and pending transactions never produce verified=True.
    """
    _commitment(commitmentHex)
    _decode58(signature, 64)
    _decode58(expected_signer, 32)
    rpc.assert_devnet()
    transaction = rpc.call("getTransaction", [signature, {
        "encoding": "base64", "commitment": "finalized", "maxSupportedTransactionVersion": 0,
    }])
    if transaction is None:
        raise AttestationError("transaction_not_finalized_or_unavailable")
    if type(transaction) is not dict or transaction.get("version") != "legacy":
        raise AttestationError("unsupported_transaction")
    meta = transaction.get("meta")
    if type(meta) is not dict or "err" not in meta or meta["err"] is not None:
        raise AttestationError("transaction_failed_or_unavailable")
    encoded = transaction.get("transaction")
    if type(encoded) is not list or len(encoded) != 2 or encoded[1] != "base64":
        raise AttestationError("invalid_transaction_response")
    _verify_wire(encoded[0], commitmentHex, signature, expected_signer)
    slot, block_time, fee = transaction.get("slot"), transaction.get("blockTime"), meta.get("fee")
    if not _integer(slot) or not _integer(fee, MAX_FEE_LAMPORTS) or (block_time is not None and not _integer(block_time)):
        raise AttestationError("invalid_transaction_metadata")
    statuses = rpc.call("getSignatureStatuses", [[signature], {"searchTransactionHistory": True}])
    if type(statuses) is not dict or type(statuses.get("value")) is not list or len(statuses["value"]) != 1:
        raise AttestationError("invalid_signature_status")
    status = statuses["value"][0]
    if (
        type(status) is not dict or status.get("confirmationStatus") != "finalized"
        or "err" not in status or status["err"] is not None
        or not _integer(status.get("slot")) or status["slot"] != slot
    ):
        raise AttestationError("transaction_not_finalized_or_unavailable")
    return {
        "slot": slot, "block_time": block_time, "fee_lamports": fee,
        "signer": expected_signer, "signature": signature, "genesis_hash": DEVNET_GENESIS,
        "program_id": MEMO_PROGRAM,
        "explorer_url": f"https://explorer.solana.com/tx/{signature}?cluster=devnet",
        "verified": True,
    }
