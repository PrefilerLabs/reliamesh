# ReliaMesh technical dossier

Owner: **Prefiler Labs Private Limited**. Public contact: **founder@prefiler.com**.
Evidence is dated; synthetic exercises are not customer adoption or an SLA.

**Technical readiness:** version 0.2.0 is published and deployed, and two synthetic
reliability-report commitments are finalized on Solana Devnet. The code, installable
packages, production checks and reproducible ledger evidence are ready for a
technical grant application. This does not establish grant eligibility, independent
adoption, audited telemetry or mainnet readiness.

## Product and public entry points

ReliaMesh is open-source reliability infrastructure for AI agents. A standard-library
Python SDK emits classified execution outcomes and numerical measurements. The service
detects high failure rates and deterioration in failures, latency, token use and retries,
associates observations with versions, and tracks incident recovery. It does not infer
task success from HTTP status or inspect prompts, model outputs or tool content.

| Resource | Public URL |
|---|---|
| Website | https://reliamesh.com |
| Managed API | https://api.reliamesh.com |
| Health / HTTP contract | https://api.reliamesh.com/health · https://api.reliamesh.com/openapi.json |
| Company repository | https://github.com/PrefilerLabs/reliamesh |
| 0.2.0 release and final verification assets | https://github.com/PrefilerLabs/reliamesh/releases/tag/v0.2.0 |
| Released baseline | https://github.com/PrefilerLabs/reliamesh/releases/tag/v0.1.0 |
| SDK package | https://pypi.org/project/reliamesh-sdk/0.2.0/ |
| Server package | https://pypi.org/project/reliamesh-server/0.2.0/ |
| Solana agent example | [Runnable read-only integration](solana-agent-example.md) |
| Devnet specification and proof | [Commitment/verifier](solana-attestation.md) · [exact evidence](evidence/solana-devnet/README.md) |

Managed tenant access is operator-provisioned. Self-hosting requires no company
account, cloud service, wallet or telemetry export. The public repository and core
packages use **Apache-2.0**, with company copyright/NOTICE and a reviewed dependency
license inventory. The SDK has **zero runtime dependencies**. Security reporting:
[SECURITY.md](../SECURITY.md). No token or commercial billing product exists.

## Architecture and Solana's role

```mermaid
flowchart LR
    A[Agent or Solana RPC tool] --> B[Explicit Python SDK]
    B --> C[Authenticated FastAPI ingestion]
    C --> D[Bounded deterministic detection]
    D --> E[SQLite or Firestore]
    E --> F[Tenant summary and incidents]
    F --> G[Operator selects count-only report]
    G --> H[Salted off-chain bundle]
    H --> I[Optional signed Solana Devnet Memo]
    H --> J[Independent verifier]
    I --> J
```

The Solana example evaluates RPC readiness for an agent before a potential downstream
action. It classifies RPC errors, malformed responses and stale/expired state without
storing account or transaction contents. It performs no trade, transfer or signing.
Labeled offline fixtures exercise regression and recovery without pretending that
Solana itself suffered an outage.

The optional adapter publishes a signed hash commitment to an explicitly selected
report. A developer can compare a disclosed report with a shared ledger reference
outside the publisher's server. Verification checks the expected signing key, exact
commitment transaction, and finalized Devnet inclusion through the official RPC.
An ordinary signed off-chain report remains sufficient when no ledger reference is
needed. Core detection and ingestion continue without Solana.

This uses the **existing Memo program**, not a custom deployed contract or program-owned
report account. It is an early public-good integration, not proof of a decentralized
reliability network. The dedicated signer is
`CZvbnEgAZGfbJn2LxgK9xCSLGRc4fzboLyaduLUjphR5`; the program is
`MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr`. Both transactions use genesis
`EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG` and publish only the signed
commitment memo. The off-chain bundles intentionally disclose synthetic counts and
random salts; no customer content was published.

| Finalized report | Devnet transaction | Slot | Fee |
|---|---|---:|---:|
| Regression | [4PEYeeFi…BUzf4Khz](https://explorer.solana.com/tx/4PEYeeFiF6HxX8TPVd9mvcRrxHCub2oV4NBZkwUU1oXxvCKRsMGP6hgih5sDd3Mn4XehRh8mwp88Foe9BUzf4Khz?cluster=devnet) | 508201584 | 5,000 lamports |
| Recovery | [48n9Bfqu…3gLYobxA](https://explorer.solana.com/tx/48n9Bfqu3sM8UYWgSavK8Lkj7vvTwPi8FfBbuw8yehJd1jV8bu8HMDAos9TsMxMNeqt8CDEdEZeaZeoP3gLYobxA?cluster=devnet) | 508201716 | 5,000 lamports |

The [exact evidence record](evidence/solana-devnet/README.md) contains complete
signatures, block times, report bundles, commitments, receipts and verification
commands. The official free faucet supplied 0.5 Devnet SOL; the two commitments
consumed 0.00001 test SOL in total. No real SOL was purchased or spent.

## Privacy and security

Strict event validation rejects arbitrary attributes, prompts, responses, tool
arguments/results, exception messages and unknown fields. Allowed opaque labels are
still the integrator's responsibility: a secret hidden in an allowed label is not
automatically detected. Tenant operational aggregates are protected data.

API controls include high-entropy bearer keys stored as digests, scoped ingest/read/manage
permissions, revocation, tenant isolation, transactional quotas, duplicate/conflict checks,
bounded parsing and fixed error messages. Remote SDK connections require HTTPS and reject
redirects. There is no third-party analytics SDK or automatic network contribution.

The service suppresses observations older than seven days, bounds incident history and
uses asynchronous Firestore TTL for inactive state after seven days plus five minutes.
Tenant deletion removes active state and keys;
PITR, backups and platform logs have separate retention. Managed logs retain seven days
and can include IP/path/timing metadata. TLS minimum is 1.2. The runtime is non-root,
has no Python package installer, and uses project-scoped service accounts and secret references.

The on-chain memo reveals a commitment, payer and timing. Salt limits guessing before
disclosure; publishing the bundle reveals the selected counts. The ledger cannot erase
a published memo and does not attest telemetry truth, independent cohorts, completeness,
causality or real-world observation time. RPC-reported finality is trusted; the verifier
is not a consensus light client. Devnet may reset or prune history.

## Deployment, operating limits and costs

Only GCP project **`reliamesh`** is used. Cloud Run in **asia-south1** runs the API with
Firestore Standard Native storage, seven-day PITR and deletion protection. HTTPS serves
`reliamesh.com`, `www.reliamesh.com` and `api.reliamesh.com` through a global load balancer.
All three returned version **0.2.0** on 6 October 2026, with hostname-verified TLS 1.3
and a certificate expiring 21 December 2026. At that release verification, Cloud Run revision
`reliamesh-api-00005-mkb` received 100% of service traffic. Its deployed image was
`asia-south1-docker.pkg.dev/reliamesh/reliamesh/api@sha256:43e253084e69fb21882c691d5e8ef77b1f5018b1f33335948cde068326c753d8`,
built from release commit `158dfd825130a2f95926a6844049dbdf4e2a84ba`.
Monitoring covers API
availability, server errors and unusual request volume, routed to founder@prefiler.com;
email delivery has not been validated through an induced outage.

| Technical limit | Current value |
|---|---:|
| Cloud Run resources / scaling | 1 vCPU, 512 MiB; 0–2 instances |
| Concurrency / request timeout | 16 / 30 seconds |
| Maximum batch / body | 100 events / 128 KiB |
| Per-tenant daily accepted-event quota | 10,000 |
| Per-process request throttle | 10 per second overall / 120 per minute per tenant |
| Retained baseline / current windows | 50 / 50 samples per stream |
| Streams / version sets per stream | 16 / 8 |
| Dedup entries / incident history | 2,048 / 32 |
| Detection state bound / retention | 650 KB / seven days |

The seven-day Monitoring query ending 29 September 2026 08:36:53 UTC returned **45,009
HTTP requests**, including 12,653 2xx, 32,356 4xx and **zero recorded 5xx**. The first
returned sample is seven hours after the query start; the gap is not verified zero
traffic. Most traffic was
404/429 rejection; probes, tests and possible bots are included. These are not users,
customers, accepted events or adoption. Mean platform latency of 3.018 ms mostly describes
probes/rejections and is **not an ingestion benchmark**. No uptime SLA is asserted.

Cost estimate, USD before credits/taxes: the existing forwarding-rule bundle is
**$0.025/hour, or $18.25 per 730-hour month**. Its attached static IPv4 has no separate
address charge. Observed compute/request use extrapolates to approximately $0.40/month
gross and sampled registry storage to $0.09/month: **about $18.74/month for those measured
components**, plus traffic/egress, Firestore/PITR/TTL, logging/monitoring, secrets,
domain and other unmeasured charges. Actual invoiced spend and remaining credits were
not available within project-only authorization. A two-instance cap is not a spending cap.
[Network pricing](https://cloud.google.com/vpc/network-pricing),
[Cloud Run pricing](https://cloud.google.com/run/pricing),
[registry pricing](https://cloud.google.com/artifact-registry/pricing),
[Firestore pricing](https://cloud.google.com/firestore/pricing).

## Verification and demonstrations

The final 0.2.0 commit passed **392 tests with one optional emulator test skipped**
on each of Python 3.12 and 3.13; deployment independently reran the same suite.
The runtime inventory covers 34 packages, including two first-party packages,
with no license-policy errors. Public checks cover lint, locked dependency
vulnerabilities, Git-history secrets and container execution.
[Exact-commit CI](https://github.com/PrefilerLabs/reliamesh/actions/runs/37320057826),
[deployment](https://github.com/PrefilerLabs/reliamesh/actions/runs/37320895699),
[Trusted Publishing](https://github.com/PrefilerLabs/reliamesh/actions/runs/37320914593).
The release's attached verification record supplies deployment and package evidence.
All four 0.2.0 distributions match PyPI SHA-256 hashes, and their PyPI attestations
verify the company repository, exact release commit, tag, workflow and expected
publishing environments. A clean Python 3.12 install accepted 200 synthetic events,
detected and resolved a regression, dropped zero events, and restored an identical
SQLite summary from backup. The published optional attestation CLI passed report,
key-generation, commitment preparation and offline signature checks; altered
signatures, commitments and signers were rejected. The default install has no
cryptography dependency.
Offline interoperability against the
official Solana JavaScript SDK passed 13 checks, including independent construction
of byte-identical transaction messages and rejection of signature/memo mutations.
The 6 October deployed 0.2.0 synthetic Solana-tool exercise accepted 200 observations,
opened and recovered the same incident, and dropped zero SDK events; a separate
live read-only Devnet readiness check passed. Both this execution record and the
earlier 0.1.0 exercise are preserved in the [Solana evidence](evidence/solana-devnet/README.md).
A separate 201-event production check passed deduplication, privacy rejection,
tenant isolation, key scopes/revocation, storage readiness and test-tenant cleanup.

The 0.1.0 baseline passed **190 tests, with one optional emulator test skipped**;
separate deployed Firestore verification passed. Public CI tested Python 3.12/3.13,
lint, locked dependency vulnerabilities/licenses, secret history and the container.
[Baseline CI](https://github.com/PrefilerLabs/reliamesh/actions/runs/36330730983),
[deployment](https://github.com/PrefilerLabs/reliamesh/actions/runs/36330742249),
[Trusted Publishing](https://github.com/PrefilerLabs/reliamesh/actions/runs/36423989541).

The baseline release's four PyPI artifacts matched GitHub assets and all four PyPI
provenance attestations verified against this company repository. A fresh published
package install ingested 200 synthetic events, opened a regression, resolved that
same incident, and dropped zero SDK events. A consistent SQLite backup restored an
identical summary. Deployed checks ingested 201 synthetic events and verified replay
deduplication, privacy schema, tenant isolation, key scopes/revocation, readiness and
test-tenant deletion. Source-archive installation passed 102 targeted tests.

```sh
python -m pip install reliamesh-server==0.2.0 reliamesh-sdk==0.2.0
reliamesh tenant create grant-demo --output .local/grant-demo.json
reliamesh serve
# Second terminal, from the release source archive:
python examples/synthetic_regression.py --endpoint http://127.0.0.1:8080 --credentials .local/grant-demo.json
python examples/solana_rpc_agent.py --fixture
```

The [release assets](https://github.com/PrefilerLabs/reliamesh/releases/tag/v0.2.0)
include machine-readable verification, this dossier, a website screenshot and the
Devnet evidence bundle. The linked Solana example and evidence provide live-read
and ledger-verification commands. Use the example output, repository architecture
diagram, website and finalized Explorer references as application/demo evidence.
Clearly label synthetic scenes and disclose the released 0.1.0 baseline when
describing later work. The immutable 0.2.0 source tag precedes final ledger
publication; this dated evidence supplement records the subsequent verification.

## Honest limitations and reusable wording

No independent customer pilots, organic adoption, paid usage, calibrated detection
accuracy, verified cross-company cohorts, mainnet deployment or independent security
audit are established. The system has bounded single-region analysis, manual managed
onboarding, and no dashboard or billing product. Devnet anchoring alone is a modest
Solana-specific contribution; useful integrations and actual developer feedback are
needed to support a stronger ecosystem-benefit claim.

The exact deployed 0.2.0 image scan (6 October 2026 UTC) had zero critical, zero fixable
HIGH/CRITICAL and zero Python HIGH/CRITICAL findings. It fixes newly reported OpenSSL
and PCRE2 issues. **44 HIGH OS-package records across eight distinct CVEs with no
advertised fixes** remain. Reachability analysis and mitigations are documented
in [container security](container-security.md); this is not a clean-bill-of-health
claim. The same image passed non-root API ingestion, deduplication and summary
checks with networking disabled, no Python package installer and no set-ID files.
Full image and scanner evidence is recorded with the release. Managed-service customer legal notices,
founder/residency/prior-funding facts, budgets and application eligibility remain owner
business responsibilities, separate from technical execution.

**Reusable technical description:**

> ReliaMesh is Apache-2.0 reliability infrastructure for AI agents, developed by
> Prefiler Labs Private Limited. Its dependency-free Python SDK records classified
> execution outcomes and numerical measurements without collecting prompts, model
> responses or tool contents. A portable service detects tenant-local regressions
> and recovery using bounded statistical windows. The optional Solana Devnet
> integration lets developers publish and verify signed commitments to disclosed,
> privacy-minimized reliability reports; a read-only Solana RPC agent example
> demonstrates how to instrument tool readiness. Collection and detection work
> independently of the blockchain. Current evidence is reproducible synthetic
> testing and deployed technical verification, not a claim of independent adoption
> or independently audited telemetry.
