# Solana integration evidence

**Two report commitments finalized on Devnet.** The 0.2.0 regression and recovery
reports below were anchored in the existing Memo program on **6 October 2026 at
19:41 UTC**. Their signatures, exact commitments, expected signer and finalized
status were independently checked through a separate implementation on 7 October.
This establishes signed report commitments and RPC-reported inclusion, not the
truth of telemetry or independent customer adoption. No custom program was deployed.

## Free funding and finalized transactions

The dedicated test signer received **500,000,000 lamports (0.5 Devnet SOL)** through
the operator-reported official Solana Foundation web faucet. The funding transaction
finalized at slot **508201210**, with block time **6 October 2026, 19:39:38 UTC**;
its recipient balance changed from zero to 500,000,000 lamports. The on-chain check
establishes that transfer; website attribution comes from the observed faucet workflow.
[Funding transaction](https://explorer.solana.com/tx/2c65JQ8t1i7cxRbRVfgmKfZKZtZxYZXuPaVVCpky4hmf8TWsBU843yRFXjV6d2y4Jq7CNgF8q7vKFs1jipc7F9rc?cluster=devnet).

The [funding record](funding-status.json) preserves the earlier failed official CLI
attempts on 5 and 6 October and the reviewed PoW bootstrap constraint as dated
history. No mainnet SOL, paid faucet or token purchase was used.

| Snapshot | Receipt / Explorer | Finalized slot | Block time (UTC, 6 October 2026) | Fee (lamports) |
|---|---|---:|---|---:|
| Regression | [Receipt](release-0.2.0/regression-receipt.json) · [Explorer](https://explorer.solana.com/tx/4PEYeeFiF6HxX8TPVd9mvcRrxHCub2oV4NBZkwUU1oXxvCKRsMGP6hgih5sDd3Mn4XehRh8mwp88Foe9BUzf4Khz?cluster=devnet) | 508201584 | 19:41:08 | 5,000 |
| Recovery | [Receipt](release-0.2.0/recovery-receipt.json) · [Explorer](https://explorer.solana.com/tx/48n9Bfqu3sM8UYWgSavK8Lkj7vvTwPi8FfBbuw8yehJd1jV8bu8HMDAos9TsMxMNeqt8CDEdEZeaZeoP3gLYobxA?cluster=devnet) | 508201716 | 19:41:39 | 5,000 |

[Separate implementation verification](release-0.2.0/independent-verification.json)
recomputed each canonical salted hash, used official `@solana/web3.js` 1.98.4 to
verify Ed25519 signatures and reconstruct the exact single signed Memo transaction,
and checked finalized transaction/history responses at matching slots. Wrong
commitments, wrong signers and altered signatures were rejected. This was performed
by the publisher using a separate code path; it is not a third-party security audit.

## Verified 0.2.0 application exercise

On **6 October 2026 at 06:08 UTC**, the Solana RPC readiness example exercised the
deployed **0.2.0 managed API** at `https://api.reliamesh.com` with a disposable
synthetic tenant. It injected 50 healthy checks, 50 RPC errors, then 100 healthy
checks. All 200 observations were accepted with zero duplicates or SDK drops;
the same incident opened and recovered. The tenant was deleted afterward. A
separate read-only check of the actual official Devnet RPC passed; it did not
produce the injected failures. [0.2.0 execution evidence](release-0.2.0/execution.json).

| Snapshot | Baseline failures/samples | Current failures/samples | Incidents | Commitment SHA-256 |
|---|---:|---:|---|---|
| [0.2.0 regression bundle](release-0.2.0/regression-bundle.json) | 0/50 | 50/50 | 1 open | `995bc2d37dfaa63890d370c1d051e85b46dd7b9aade1bcfc9d4b799a84a94c9c` |
| [0.2.0 recovery bundle](release-0.2.0/recovery-bundle.json) | 0/50 | 0/50 | 1 resolved | `abc716249fca4e1a3c316cbe506f76de8c6772956a1240a8287dfa2de5b6e8cf` |

These disclosed synthetic reports match the two finalized commitments above.
The original 0.1.0 fixtures below remain unchanged and were not submitted.

## Original 0.1.0 exercise and fixed test vectors

On **30 September 2026 at 13:38 UTC**, the new Solana RPC readiness example exercised
the released **0.1.0 managed API** at `https://api.reliamesh.com` with a disposable
synthetic tenant. The scenario injected 50 healthy checks, 50 RPC errors, then 100
healthy checks. All 200 events were accepted, no SDK events were dropped, a regression
opened and the same incident recovered. The tenant was deleted afterward. A separate
read-only check of the real official Devnet RPC succeeded; it was not the source of
the injected failures. [Execution evidence](execution.json).

The deliberately disclosed reports contain selected retained-window counts only:

| Snapshot | Baseline failures/samples | Current failures/samples | Incidents | Commitment SHA-256 |
|---|---:|---:|---|---|
| [Regression bundle](regression-bundle.json) | 0/50 | 50/50 | 1 open | `d15649d438b863606f4b77fb490573136fc5492fe9603db97e572385aa26e10e` |
| [Recovery bundle](recovery-bundle.json) | 0/50 | 0/50 | 1 resolved | `512ecf91d5266a360cfdf4b6107275f1c17beef7debc2da32075c87acb31b7ef` |

These are synthetic functional observations, not provider reliability, independent
adoption, an incident affecting Solana, or an accuracy/throughput benchmark. Reports
are publisher assertions. Their generation timestamps are not ledger timestamps.

## Intended Devnet identity and exact program

- Cluster: `solana-devnet`; official RPC `https://api.devnet.solana.com`.
- Pinned genesis: `EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG`.
- Company-controlled dedicated test signer: `CZvbnEgAZGfbJn2LxgK9xCSLGRc4fzboLyaduLUjphR5`.
  This repository is the publisher-identity reference; do not trust a key from an
  arbitrary receipt. [Signer Explorer](https://explorer.solana.com/address/CZvbnEgAZGfbJn2LxgK9xCSLGRc4fzboLyaduLUjphR5?cluster=devnet).
  Its funding transfer and the earlier zero-balance checks are recorded in the
  [dated funding evidence](funding-status.json).
- Existing Memo program: `MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr`.
  [Program Explorer](https://explorer.solana.com/address/MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr?cluster=devnet).
- No custom program or program-owned report account is deployed. The two receipts
  above identify transactions invoking this existing Memo program.

The signing key is private and is never included here, in packages, or in the API
runtime. Bundles intentionally disclose their random salts and synthetic reports.

## Offline interoperability and reproducibility

[Wire interoperability evidence](wire-interop.json) records 13 independent checks
using the official `@solana/web3.js` 1.98.4 SDK: deserialization, Ed25519 signature,
exact Memo/payer/account layout, byte-identical roundtrip and independent message
construction, plus rejection of signature and memo mutations. That disposable
offline fixture has an intentionally unusable blockhash and **was not submitted**.
Its signature is not a Devnet transaction reference.

From the release source checkout or server source archive:

```sh
python -m pip install 'reliamesh-server[attest]==0.2.0' reliamesh-sdk==0.2.0
python examples/solana_rpc_agent.py --fixture
python examples/solana_rpc_agent.py --live
```

The first command after installation makes no network calls and does not claim
server detection without explicit collection. The second uses four bounded,
read-only official Devnet RPC calls at most. See the [collection demo](../../solana-agent-example.md)
to reproduce actual regression/recovery against your own disposable tenant.

The original public regression bundle is a stable canonical-hash test vector in
`tests/test_attestation.py`. The 0.2.0 bundles have distinct hashes and actual ledger
receipts. Recheck a receipt using the signer obtained from this repository's identity
record, rather than trusting a signer supplied by an arbitrary receipt:

```sh
reliamesh-attest verify --bundle docs/evidence/solana-devnet/release-0.2.0/regression-bundle.json --signature 4PEYeeFiF6HxX8TPVd9mvcRrxHCub2oV4NBZkwUU1oXxvCKRsMGP6hgih5sDd3Mn4XehRh8mwp88Foe9BUzf4Khz --expected-signer CZvbnEgAZGfbJn2LxgK9xCSLGRc4fzboLyaduLUjphR5
reliamesh-attest verify --bundle docs/evidence/solana-devnet/release-0.2.0/recovery-bundle.json --signature 48n9Bfqu3sM8UYWgSavK8Lkj7vvTwPi8FfBbuw8yehJd1jV8bu8HMDAos9TsMxMNeqt8CDEdEZeaZeoP3gLYobxA --expected-signer CZvbnEgAZGfbJn2LxgK9xCSLGRc4fzboLyaduLUjphR5
```

These commands read Devnet and do not submit transactions. Altered reports and wrong
publishers must fail. Future checks depend on retained Devnet history and trust the
official RPC's finality report; the verifier is not a consensus light client.
