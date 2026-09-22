# Verification evidence for 0.1.0

Verified on22 September2026. These are technical checks, not evidence of external
adoption, provider reliability or production customer outcomes.

- GitHub CI: Python3.12 and3.13 tests, runtime dependency audit, license inventory,
  Git history secret scanning, package builds and non-root container checks.
- Local suite:182 passed; one explicitly optional Firestore-emulator check skipped.
  Actual managed Firestore was tested separately through the deployed API.
- Zero-dependency SDK wheel installed in a clean Python3.12 environment:
  50 synthetic baseline successes,50 malformed-output failures,100 successes;
  a regression incident opened and then resolved, with no SDK drops.
- SQLite live backup restored to a separate database and returned an identical
  authenticated summary. Missing-source and overwrite failures are tested.
- Docker Compose: read-only root filesystem, unprivileged UID10001, persistent
  data volume and healthy HTTP endpoint. Default listening address is loopback.
- Managed API at `api.reliamesh.com`:201 accepted synthetic events, duplicate
  replay deduplicated, regression detected, recovery resolved, tenant separation,
  schema rejection, scopes, revocation, storage readiness and deletion verified.
  Both disposable verification tenants were deleted after the run.
- Google-managed TLS certificate active for root, `www` and `api`; HTTPS health
  requests and the full SDK path succeeded with normal certificate verification.

The deployed test observations are explicitly synthetic. They are separated from
real streams by the event contract and are not eligible for network intelligence.
No LLM/provider requests or paid external APIs were used to manufacture evidence.

Important limits: the detector uses bounded count windows, does not establish
causality, and has no externally calibrated false-positive rate. SDK delivery is
best effort. Global network publication is disabled. Firestore recovery is enabled
but no managed point-in-time restore drill is claimed here. Outbound operator
notification channels require an approved destination.
