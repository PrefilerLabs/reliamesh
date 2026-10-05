# Releases

The server and zero-dependency Python SDK currently share version 0.2.0. Event
schema 1.0 and HTTP `/v1` are versioned separately. A package version must never be
reused with different contents after publication.

1. Run CI on the exact release commit: Python 3.12/3.13, protocol and SDK tests,
   tenant-isolation tests, dependency/license audit, secret scan and Docker build.
2. Exercise the installed SDK and SQLite backup/restore. Run the synthetic
   deployed verification script; preserve its sanitized evidence separately from
   credentials. Verify custom-domain HTTPS and runtime permissions.
3. Update CHANGELOG and package versions before the final CI run, then tag that
   verified main-branch commit `v<version>`. Never move a published version tag.
4. Deploy via the main-branch `Deploy` workflow using workload identity. Verify
   the immutable image digest and end-to-end behavior after deployment.
5. Once PyPI trusted publishers are configured, dispatch `Publish Python packages`
   **against the release tag**. It builds both distributions and publishes with
   OIDC attestations. It requires successful CI on that exact commit, validates
   package metadata, and installs the built wheels into a clean environment.
   The package-specific environments permit only `v*` tags. No long-lived PyPI token belongs
   in repository secrets.
6. Download the workflow's `python-distributions` artifact, verify its hashes
   against PyPI, and attach those exact files to the GitHub release with checksums
   and the CI license inventory. Install from PyPI in a clean environment and
   exercise the synthetic detection/recovery path before announcing availability.
   Synthetic results must remain explicitly labeled.

Repository actions require full commit pins. Workflow tokens default to read-only;
publishing grants OIDC only to the isolated upload job. Dependabot security updates
and private vulnerability reporting are enabled; updates are not auto-merged.

Prepared PyPI publisher identities:

| Field | Value |
|---|---|
| Projects | `reliamesh-sdk`, `reliamesh-server` |
| GitHub owner | `PrefilerLabs` |
| Repository | `reliamesh` |
| Workflow filename | `publish.yml` |
| SDK environment | `pypi` |
| Server environment | `pypi-server` |

Separate publisher environments avoid PyPI's restriction on registering multiple
new projects under one pending publisher identity and limit each upload job to
one package.

PyPI ownership/authorization must be controlled by Prefiler Labs Private Limited.
GitHub release artifacts are independently installable while PyPI authorization
is pending. Documentation must not claim registry availability before a real
download/install has been verified.
