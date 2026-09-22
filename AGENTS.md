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
- No blockchain, billing product, paid external services, or fabricated adoption.
- Secrets and operational credentials belong outside Git and public artifacts.
- Keep implementations portable, bounded, deterministic, and simple.

Use Python 3.12+, FastAPI/Pydantic for the service, a standard-library Python SDK,
SQLite for self-hosting, and Firestore behind a storage interface for Cloud Run.
