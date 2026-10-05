# Changelog

## 0.2.0

- Optional Devnet-only signed Memo commitments to salted, count-only reliability
  reports, with strict schemas, canonical hashing and a finalized-transaction
  verifier. No automatic publication or change to the tenant ingestion path.
- Explicit operator CLI with dedicated Devnet keys, bounded fees and transport,
  pending-signature recovery, and trusted-signer verification.
- Runnable Solana RPC agent integration with labeled synthetic failure/recovery
  fixtures; SDK runtime dependencies remain empty and event schema remains 1.0.
- Grant-readiness evidence, public Devnet references, privacy/threat-model notes,
  and reproducible commands. Synthetic tests do not establish independent demand.

## 0.1.0

Initial release implementation:

- Schema 1.0 reliability events, privacy-safe classifications, version labels,
  canonical fingerprints, and bounded deterministic analysis.
- Authenticated ingestion, tenant isolation, scoped key lifecycle, quota controls,
  summary/incident APIs, deletion, and retention cleanup.
- SQLite storage and a project-restricted Firestore adapter.
- Standard-library Python SDK with explicit exports, bounded queue/retries,
  generic exception classification, and an OpenTelemetry whitelist adapter.
- Synthetic end-to-end regression and recovery example, test suite, and public
  architecture/privacy/security documentation.

Network publication remains disabled. This release does not establish independent
adoption, detection accuracy on customer workloads, or a service availability SLA.
Package publication and deployment status must be verified from actual release
and operations evidence; the version number alone does not imply either.
