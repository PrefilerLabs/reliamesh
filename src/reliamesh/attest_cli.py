"""Explicit operator-only Solana Devnet publication. Never imported by the API."""

import argparse
import base64
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from reliamesh import __version__
from reliamesh.attestation import (
    CommitmentBundle,
    commitment,
    prepare,
    read_document,
    report_from_summary,
)
from reliamesh.cli import write_private


def publish(bundle, keypair, output, *, rpc=None, wait_seconds=45):
    from reliamesh.solana_attest import (
        DevnetRPC,
        build_transaction,
        signer_public_key,
        verify_finalized,
    )

    signer = signer_public_key(keypair)
    if signer != bundle.expected_signer:
        raise ValueError("signer does not match commitment")
    output = Path(output)
    pending = output.with_name(output.name + ".pending.json")
    if output.exists() or pending.exists():
        raise ValueError("receipt already exists; verify its signature instead of resubmitting")
    rpc = rpc or DevnetRPC()
    rpc.assert_devnet()
    block_response = rpc.call("getLatestBlockhash", [{"commitment": "finalized"}])
    if type(block_response) is not dict or type(block_response.get("value")) is not dict:
        raise ValueError("invalid blockhash response")
    block = block_response["value"]
    if (type(block.get("lastValidBlockHeight")) is not int
            or not 0 <= block["lastValidBlockHeight"] < 2**63
            or type(block.get("blockhash")) is not str):
        raise ValueError("invalid blockhash metadata")
    digest = commitment(bundle)
    transaction, signature = build_transaction(digest, keypair, block["blockhash"])
    message = base64.b64encode(base64.b64decode(transaction)[65:]).decode("ascii")
    fee_response = rpc.call("getFeeForMessage", [message, {"commitment": "finalized"}])
    fee = fee_response.get("value") if type(fee_response) is dict else None
    if type(fee) is not int or not 0 < fee <= 10_000:
        raise ValueError("fee exceeds Devnet publication limit or blockhash expired")
    balance_response = rpc.call("getBalance", [signer, {"commitment": "finalized"}])
    balance = balance_response.get("value") if type(balance_response) is dict else None
    if type(balance) is not int or balance < fee:
        raise ValueError("insufficient free Devnet funds")
    # Save the deterministic signature BEFORE sending. A network timeout cannot
    # cause an unnoticed second transaction; this path refuses to be reused.
    write_private(pending, {"status": "pending", "commitment_sha256": digest,
                            "signer": signer, "signature": signature,
                            "last_valid_block_height": block["lastValidBlockHeight"]})
    sent = rpc.call("sendTransaction", [transaction, {"encoding": "base64",
                    "skipPreflight": False, "preflightCommitment": "finalized", "maxRetries": 0}])
    if sent != signature:
        raise ValueError("RPC returned a different transaction signature")
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        response = rpc.call("getSignatureStatuses", [[signature], {"searchTransactionHistory": True}])
        if (type(response) is not dict or type(response.get("value")) is not list
                or len(response["value"]) != 1):
            raise ValueError("invalid signature status response")
        status = response["value"][0]
        if status is not None and (type(status) is not dict or "err" not in status):
            raise ValueError("invalid signature status")
        if status is not None and status["err"] is not None:
            raise ValueError("Devnet transaction failed; see pending signature")
        if status is not None and status.get("confirmationStatus") == "finalized":
            receipt = verify_finalized(digest, signature, signer, rpc)
            receipt["commitment_sha256"] = digest
            write_private(output, receipt)
            return receipt
        time.sleep(1.5)
    raise ValueError("finalization pending; verify the saved signature later")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    keygen = commands.add_parser("keygen", help="Generate a dedicated private Devnet key")
    keygen.add_argument("--output", required=True)
    project = commands.add_parser("report", help="Project retained-window counts from a local summary")
    project.add_argument("--summary", required=True)
    project.add_argument("--scope", choices=("synthetic", "real"), required=True)
    project.add_argument("--output", required=True)
    make = commands.add_parser("prepare", help="Create a salted off-chain commitment bundle")
    make.add_argument("--report", required=True)
    make.add_argument("--expected-signer", required=True)
    make.add_argument("--output", required=True)
    send = commands.add_parser("publish", help="Publish ONLY a hash on Solana Devnet")
    send.add_argument("--bundle", required=True)
    send.add_argument("--keypair", required=True)
    send.add_argument("--output", required=True)
    send.add_argument("--confirm-public-commitment", action="store_true", required=True)
    verify = commands.add_parser("verify", help="Verify hash, trusted signer, and finalized inclusion")
    verify.add_argument("--bundle", required=True)
    verify.add_argument("--signature", required=True)
    verify.add_argument("--expected-signer", required=True,
                        help="A signer obtained through a trusted channel, not the receipt alone")
    status = commands.add_parser("status", help="Inspect a pending signature without resubmitting")
    status.add_argument("--pending", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "keygen":
            from reliamesh.solana_attest import generate_keypair

            keypair = generate_keypair()
            write_private(args.output, keypair)
            print(json.dumps({"public_key": keypair["public_key"], "network": "solana-devnet"}))
        elif args.command == "status":
            from reliamesh.solana_attest import DevnetRPC, submission_status

            pending = read_document(args.pending)
            result = submission_status(pending["signature"], pending["last_valid_block_height"], DevnetRPC())
            print(json.dumps(result, indent=2))
        elif args.command == "report":
            report = report_from_summary(read_document(args.summary, max_bytes=900_000), synthetic=args.scope == "synthetic",
                                         server_version=__version__, generated_at=datetime.now(UTC))
            write_private(args.output, report.model_dump())
            print("Content-minimized report written; no publication performed.")
        elif args.command == "prepare":
            bundle = prepare(read_document(args.report), args.expected_signer)
            write_private(args.output, bundle.model_dump())
            print(json.dumps({"commitment_sha256": commitment(bundle)}))
        else:
            bundle = CommitmentBundle.model_validate(read_document(args.bundle))
            if args.command == "publish":
                receipt = publish(bundle, read_document(args.keypair), args.output)
            else:
                from reliamesh.solana_attest import DevnetRPC, verify_finalized

                if args.expected_signer != bundle.expected_signer:
                    raise ValueError("trusted signer differs from bundle")
                receipt = verify_finalized(commitment(bundle), args.signature, args.expected_signer, DevnetRPC())
            print(json.dumps(receipt, indent=2))
    except (ValueError, OSError, KeyError, TypeError, ImportError):
        # Validation and transport errors can contain input values. Do not echo
        # reports, key material, RPC bodies, or customer data on error.
        parser.exit(1, "Attestation failed: check input, optional attest dependencies, Devnet connectivity/funds, and any pending receipt. No success is claimed.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
