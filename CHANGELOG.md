# Changelog

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
