# Releases

The server and zero-dependency Python SDK currently share version0.1.0. Event
schema1.0 and HTTP `/v1` are versioned separately. A package version must never be
reused with different contents after publication.

1. Run CI on the exact release commit: Python3.12/3.13, protocol and SDK tests,
   tenant-isolation tests, dependency/license audit, secret scan and Docker build.
2. Exercise the installed SDK and SQLite backup/restore. Run the synthetic
   deployed verification script; preserve its sanitized evidence separately from
   credentials. Verify custom-domain HTTPS and runtime permissions.
3. Update CHANGELOG and package versions, tag `v<version>`, and create a GitHub
   release with server/SDK wheels, source distributions, checksums and license
   inventory. Synthetic results must remain explicitly labeled.
4. Deploy via the main-branch `Deploy` workflow using workload identity. Verify
   the immutable image digest and end-to-end behavior after deployment.
5. Once PyPI trusted publishers are configured, dispatch `Publish Python packages`
   **against the release tag**. It builds both distributions and publishes with
   OIDC attestations. No long-lived PyPI token belongs in repository secrets.

Prepared PyPI publisher identities:

| Field | Value |
|---|---|
| Projects | `reliamesh-sdk`, `reliamesh-server` |
| GitHub owner | `PrefilerLabs` |
| Repository | `reliamesh` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

PyPI ownership/authorization must be controlled by Prefiler Labs Private Limited.
GitHub release artifacts are independently installable while PyPI authorization
is pending. Documentation must not claim registry availability before a real
download/install has been verified.
