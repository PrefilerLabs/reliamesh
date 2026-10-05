# ReliaMesh engineering boundaries

ReliaMesh is open-source reliability infrastructure for AI agents, owned by
Prefiler Labs Private Limited. Apache-2.0 is the approved core license.

- Cloud authorization is ONLY Google Cloud project `reliamesh`. Every GCP command
  must explicitly select that project. Never inspect or change another project,
  organization-wide settings, billing accounts, or shared infrastructure.
- Minimize data at collection: no prompts, responses, arbitrary attributes,
  exception messages, tool arguments/results, secrets, or customer content.
- Self-hosting has no outbound telemetry. Network participation requires explicit
  configuration and independently verified cohorts; synthetic evidence is labeled.
- Build/test/verify before claiming completion. Document limitations honestly.
- The owner authorized an optional Solana Devnet attestation adapter after the
  0.1.0 core release. Keep the core chain-independent; publish only explicit
  content-minimizing commitments. No token, mainnet spending, billing product,
  paid external service, or fabricated adoption is authorized.
- Secrets and operational credentials belong outside Git and public artifacts.
- Keep implementations portable, bounded, deterministic, and simple.

Use Python 3.12+, FastAPI/Pydantic for the service, a standard-library Python SDK,
SQLite for self-hosting, and Firestore behind a storage interface for Cloud Run.
