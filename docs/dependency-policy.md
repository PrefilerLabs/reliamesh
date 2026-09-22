# Dependencies and release evidence

ReliaMesh core is Apache-2.0, owned by Prefiler Labs Private Limited. Shipped
third-party dependencies retain their own licenses and notices. The Python SDK
has no runtime package dependencies; the service and optional Firestore adapter
use the audited runtime dependency set in `requirements.lock`.

## Locked installation and review

The runtime lock records exact versions and distribution hashes. CI and container
builds install it with `pip --require-hashes`; installing local ReliaMesh packages
then uses `--no-deps` so installation cannot silently replace that dependency set.
CI checks Python 3.12 and 3.13. Test/build tools are version-pinned in the workflow
and constrained against installed runtime versions; their transitive dependencies
are not covered by the runtime lock. The build environment and base image are
separate supply-chain inputs and must be reviewed when updated.

Update `pyproject.toml` and regenerate the lock together. Review package origin,
release notes, transitive additions, licenses, compatibility, and security advisories
before merging. Run the entire CI pipeline and container build on the resulting
lock. Dependabot opens weekly update proposals; these do not bypass review,
automatically merge, deploy, or publish packages. GitHub Actions are pinned to
commit SHAs. The secret scanner is pinned to an official container digest.

## License gate

`python scripts/check_licenses.py` walks the **installed runtime dependency graph**
from `reliamesh-server[gcp]` and `reliamesh-sdk`, evaluates environment markers and
requested extras, and verifies required versions are installed. It does not scan
every package in a developer's environment and label that the shipped product.
`--root` permits checking a selected installed distribution for local review.

The checker prefers `License-Expression`, validates SPDX syntax with
[PyPA packaging](https://packaging.pypa.io/en/latest/licenses.html), and otherwise
accepts specific recognized legacy declarations or an unambiguous classifier.
Missing metadata, unknown expressions, generic `BSD`, unapproved licenses,
unreviewed `WITH` exceptions, and unavailable dependencies fail the gate. A
permissive classifier cannot override a conflicting license declaration.

The normal allowlist is Apache-2.0, MIT, MIT-0, BSD-2-Clause, BSD-3-Clause, ISC,
PSF-2.0, 0BSD, and Unlicense. This gate conservatively requires **every license
identifier** in an `AND` or `OR` expression to be approved; it does not silently
choose a permissive branch of an otherwise unreviewed expression. Changes to
the allowlist or release-specific reviews require an explicit dependency review.

Two current dependency details have concrete review evidence:

- `pyasn1-modules==0.4.2` declares only `BSD` in legacy metadata. Its installed
  `LICENSE.txt` is a two-clause BSD license, verified by SHA-256
  `2aad5fc00f705c4a1addb83eed10a6a75d286a3779f0cf8519d87e62bc4735fd`.
  The check fails if the package version or license file changes; it does not
  broadly reinterpret arbitrary BSD metadata.
- `certifi==2026.7.22` is accepted specifically as an unmodified, separately
  distributed MPL-2.0 dependency. Preserve its license, notices, and access to
  source. This does not change ReliaMesh's core license. Mozilla describes the
  separate-file obligations for larger works in its [MPL 2.0 FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/).
  A different version or another MPL package requires another review.

The gate verifies package-level declared licenses; it does not prove that every
vendored source file or native-library subcomponent was correctly declared by its
publisher. Release review must preserve bundled third-party notices and assess
new native wheels or vendored components rather than treating metadata as a
substitute for provenance.

## Security and build gates

CI runs the application, SDK, privacy, isolation, storage, and regression tests;
Ruff checks; the runtime license gate; both Python distribution builds; and a
container import/non-root smoke check. Gitleaks scans complete checked-out Git
history with findings redacted. It is the actual scanner: no keyword-only
fallback or permissive replacement is used if installation or execution fails.

`pip-audit --require-hashes -r requirements.lock` checks the locked runtime
against published advisories and fails on findings or tool failure. Review and
fix advisories rather than globally ignoring failures. An exception, if ever
necessary, must name a specific advisory, affected runtime path, mitigation,
owner, and expiry; no exceptions are configured initially. An advisory scan is
not proof that dependencies are free of malicious or undiscovered behavior.

CI artifacts include built wheels/source distributions, the runtime license
inventory, and `pip inspect` metadata. The latter describes the entire installed
CI environment, including tools; the license inventory identifies the runtime
subset. Artifacts expire after 14 days and contain no credentials or tenant data.
`pip inspect` is machine-readable dependency evidence, not a claim of a complete
container SPDX or CycloneDX SBOM.

No workflow publishes to PyPI or deploys cloud infrastructure automatically.
Package publication requires an authorized namespace and a reviewed release.
