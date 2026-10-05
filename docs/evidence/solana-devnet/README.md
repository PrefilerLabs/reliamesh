# Solana integration evidence

**Ledger proof pending.** The adapter and verifier are implemented and tested, but
no ReliaMesh transaction has yet been submitted or finalized on Devnet. Free faucet
funding is blocked by the official web faucet's human verification; the public RPC
faucet has returned errors. No mainnet SOL, paid faucet or token purchase is used.
Do not describe these files as a completed on-chain deployment.

## Verified application exercise

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
  The key has been generated but the account is not yet funded.
- Existing Memo program: `MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr`.
  [Program Explorer](https://explorer.solana.com/address/MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr?cluster=devnet).
- No custom program or program-owned report account is deployed. There is no
  finalized transaction signature, fee, slot or block time to claim yet.

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

The public regression bundle is a stable canonical-hash test vector in
`tests/test_attestation.py`. Once the owner verification allows free funding, the
prepared publisher can submit these two commitments and record receipts; a fresh
verifier must then check the real signatures, correct publisher, exact bytes and
finalized inclusion. Altered reports and wrong publishers must fail. Until those
steps actually succeed, ledger verification remains pending.
