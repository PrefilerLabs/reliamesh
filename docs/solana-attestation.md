# Optional Solana Devnet attestation

ReliaMesh keeps collection, detection, storage and the zero-dependency SDK
independent of Solana. An operator can separately commit a selected reliability
report to Devnet and let another developer verify the disclosed report against
the expected publisher. This gives a shared publication reference outside the
publisher's web server. An ordinary signed off-chain report is simpler when no
shared ledger reference is needed; Solana is not required for core reliability.

The adapter uses the existing [Memo program](https://www.solana-program.com/docs/memo),
`MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr`. **No custom program is deployed,
no program-owned report account is created, and there is no token.** A dedicated
Devnet payer signs exactly one Memo instruction and pays a test-token fee.

## Report and commitment contract

`reliamesh-window-report/v1` includes schema and server versions, a UTC generation
time, an explicit synthetic flag, stream count, baseline/current sample and failure
counts, and open/resolved/expired incident counts. Counts describe retained windows,
not lifetime event totals, customers or an accuracy benchmark. The projection selects
either synthetic or real streams and only incidents belonging to those streams.
Each stream retains up to 50 baseline and 50 current samples. Recovery can promote
the current window to baseline; snapshots are not cumulative and cannot be subtracted
to infer lifetime traffic. Generation time is a publisher assertion.

No deployment/agent IDs, wallet addresses, version labels, prompts, model responses,
tool inputs/results, arbitrary fields or exception text enter the report. Unknown
fields, duplicate JSON keys, non-integer counts and invalid count relationships
are rejected. UTC timestamps have exactly six fractional digits and `Z` suffix.

`reliamesh-devnet-commitment/v1` contains that report, `network=solana-devnet`,
the pinned genesis hash `EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG`,
the expected publisher public key, and a fresh random 32-byte `salt_hex`.
Canonical bytes are Python-compatible JSON with lexically sorted object keys,
compact separators, ASCII escaping, no non-finite numbers, and all schema defaults
included. All allowed strings are ASCII. The commitment is:

```text
SHA256(ASCII("ReliaMesh Devnet commitment v1") || 0x00 || canonical_bundle_bytes)
```

The sole on-chain instruction is UTF-8
`reliamesh:attest:v1:sha256:<64 lowercase hex digits>`; its account list includes
the payer as a required signer. The transaction has one signature, one writable
signer, one readonly Memo program account, a recent blockhash, and no other
instruction. The verifier reconstructs and checks this exact legacy wire format,
locally validates the Ed25519 signature, checks the requested transaction ID, and
requires successful finalized transaction and matching signature-status slot.

## Reproduce and operate

```sh
python -m pip install 'reliamesh-server[attest]==0.2.0' reliamesh-sdk==0.2.0
reliamesh-attest keygen --output .local/devnet-key.json
```

Export a tenant summary through its existing authenticated SDK connection. This
example uses the private credential file created by `reliamesh tenant create`;
select the correct endpoint and keep the output private:

```python
import json
from pathlib import Path
from reliamesh_sdk import Client

key = json.loads(Path(".local/example.json").read_text())["key"]
client = Client(endpoint="http://127.0.0.1:8080", api_key=key)
with Path(".local/summary.json").open("x", encoding="utf-8") as output:
    json.dump(client.summary(), output)
```

Select the appropriate scope and explicitly prepare, publish and verify:

```sh
reliamesh-attest report --summary .local/summary.json --scope synthetic --output .local/report.json
reliamesh-attest prepare --report .local/report.json --expected-signer YOUR_PUBLIC_KEY --output .local/bundle.json
reliamesh-attest publish --bundle .local/bundle.json --keypair .local/devnet-key.json --output .local/receipt.json --confirm-public-commitment
reliamesh-attest verify --bundle .local/bundle.json --signature TRANSACTION_SIGNATURE --expected-signer TRUSTED_PUBLIC_KEY
```

Use a private `.local` directory (restrict its Windows ACL). Files are created
exclusively and never overwritten. Keygen outputs only the public key; the secret
is a dedicated 32-byte seed in the private key file. Never import a valuable wallet.
Obtain free Devnet funds through an [officially documented faucet](https://solana.com/developers/cookbook/development/airdrops-and-faucets).
There is no mainnet switch and the client rejects other RPC URLs. It checks the
actual genesis before writes and verification. The API runtime has no attestor key.

Publishing checks the fee (maximum 10,000 lamports), saves a `.pending.json` signature
before submission, sends once with preflight enabled, and waits at most approximately
45 seconds plus bounded RPC timeouts. A missing final receipt is not success. After
a timeout, inspect the saved pending receipt without submitting again:

```sh
reliamesh-attest status --pending .local/receipt.json.pending.json
```

The command checks Devnet genesis, historical signature status and finalized block
height. `blockhash_expired` means the current height exceeds the saved last-valid
height. Missing/pruned history remains ambiguous even then; it never means the
transaction was definitely absent. `automatic_retry_allowed` is always false.
If finalized without an error, run `verify` with the pending signature and trusted
signer. Preserve the pending file on errors or missing history. Do not resubmit
under a new filename based solely on a timeout or missing status. Resolve ambiguous
history through the ledger/operator before deciding on another publication; the
CLI does not automatically retry. Local history is not a global duplicate registry.

The verifier's expected signer must come from a trusted channel. A key provided by
an untrusted receipt establishes no identity. [Public synthetic evidence](evidence/solana-devnet/README.md)
pins Prefiler Labs' demonstration signer, bundle, receipt, and exact commands.

## Privacy and trust limits

- Salt prevents straightforward guessing of an undisclosed low-entropy report.
  Keep both report and salt private until intentional disclosure. Publishing the
  bundle reveals its report; a hash does not make disclosed information private.
- The payer, fee, commitment, slot and ledger timing are public and linkable.
  On-chain commitments cannot be removed by deleting a ReliaMesh tenant.
- Verification establishes matching artifact bytes, possession of the expected
  signing key, and finalized inclusion as reported by the official HTTPS RPC.
  It is not a consensus light client, an independent telemetry audit, a trusted
  clock, proof of completeness, or proof of causality/provider-wide reliability.
- Devnet has no monetary SOL value and may reset or prune history. Missing history
  returns failure, never historical success. This is a prototype integration, not
  a durable mainnet notarization service or production blockchain availability SLA.
- There is no multi-party report endorsement, signer revocation registry, custom
  contract, automated report publication, customer reporting service, or verified
  independent contributor cohort. These are not implied by the demo.

The [RPC and cluster documentation](https://solana.com/docs/references/clusters)
describes public endpoint limits. Submission acknowledgement alone is not finality;
see [sendTransaction](https://solana.com/docs/rpc/http/sendtransaction) and
[getTransaction](https://solana.com/docs/rpc/http/gettransaction).
