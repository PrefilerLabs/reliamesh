# Container vulnerability review

Review date: **2026-10-05**. This is a dated assessment of the inspected image,
not a claim that future images are vulnerability-free.

The hardened local `linux/amd64` candidate uses the pinned Python 3.13.16 slim
base in the Dockerfile and the hash-locked runtime requirements. Its image
configuration digest is
`sha256:5cb32a0c51927e28925b85768a41242d26c8e74ac58baec0d59c5582b5c752d9`;
its runtime manifest is
`sha256:58aaa96451974dbced1fc4c96a20665612f268e122234631390bd1d94056d413`.
These identify the reviewed local candidate, not a deployed release.
Trivy 0.74.0 completed an OS and Python package vulnerability scan using the
official vulnerability database updated at `2026-10-05T01:10:31Z` and downloaded
from its official ECR mirror on the review date. The scan reports
**0 critical findings, 44 high package records covering 8 distinct Debian CVEs,
and 0 high/critical Python package findings**. None of the remaining records
advertises a fixed Debian 13 package version: **0 fixable high/critical findings**.
The complete report also retains 59 medium, 61 low, and 2 unknown-severity
records; no remaining record advertises a fixed version. Findings have not been
suppressed. This assessment covers the tested architecture only.

## Changes verified

The base is pinned to Python 3.13.16 slim, image index
`sha256:5434c2206183169a6c2b11d6156b775a02cce9a2fd00f9482bb8b9bb785e9b3f`.
It supplies `libssl3t64`, `openssl`, and `openssl-provider-legacy` version
`3.5.7-1~deb13u3`, fixing the six high package records for CVE-2026-75804 and
CVE-2026-84782 from the September 30 scan. See
[Debian DSA-6531-1](https://security-tracker.debian.org/tracker/DSA-6531-1).
The base still contains PCRE2 `10.46-1~deb13u2`, so the Dockerfile upgrades only
`libpcre2-8-0` to the exact security version `10.46-1~deb13u3` and removes apt's
downloaded package lists. This fixes
[CVE-2026-103111](https://security-tracker.debian.org/tracker/CVE-2026-103111),
which appeared as a fixable high finding in the October 2 database. Retire that
explicit package upgrade when a tested pinned base includes the fix.

The original candidate contained two fixable high findings in pip's vendored
dependencies: `msgpack==1.1.2` (GHSA-6v7p-g79w-8964) and
`setuptools==70.3.0` (CVE-2025-47273). Installing the application is the last step
that requires package installation tools, so the Dockerfile now removes pip,
setuptools, wheel, and Python's ensurepip bootstrap bundle afterward. It also
removes set-user-ID and set-group-ID bits from image files. This removes the
vulnerable tooling rather than overriding the scanner result.

Verification on the built image established:

- Runtime UID is 10001; no files with set-user-ID/set-group-ID bits remain.
- `pip`, `setuptools`, `wheel`, and `ensurepip` are unavailable for import.
- The Google Firestore SDK imports successfully without credentials or network.
- A real local HTTP server passes health, authenticated synthetic event ingestion,
  replay deduplication, and summary checks with container networking disabled.
- Neither `systemd-homed` nor Perl's `Archive/Tar.pm` is present in the image.
- The follow-up Trivy scan reports none of the remediated Python tooling,
  OpenSSL, or PCRE2 findings.

Container updates must rebuild the image. Installing packages interactively
inside the running container is intentionally unsupported.

## Remaining operating-system exposure

Debian tracks vulnerabilities at source-package granularity. The same CVE can
therefore appear against multiple installed binary packages even where the
vulnerable executable is absent. The following exposure assessment is based on
the image contents and application behavior; it is not a proof against every
possible exploit chain.

| CVE | Component and trigger | ReliaMesh exposure and mitigation |
| --- | --- | --- |
| [CVE-2026-76642](https://security-tracker.debian.org/tracker/CVE-2026-76642) | util-linux privileged post-mount hooks after helper failure | The API invokes no mount helpers. The image runs as a non-root user, has no set-ID executables, and has an unconfigured `/etc/fstab`. |
| [CVE-2026-78408](https://security-tracker.debian.org/tracker/CVE-2026-78408) | Privileged `nsenter --join-cgroup` passes cgroup authority to another process | No privileged namespace entry occurs in the service. Do not run this image privileged or use it for host administration. |
| [CVE-2026-78409](https://security-tracker.debian.org/tracker/CVE-2026-78409) | Authorized `X-mount.subdir` operation traverses intermediate symlinks | No fstab-authorized user mounts or mount operations are configured. |
| [CVE-2026-78410](https://security-tracker.debian.org/tracker/CVE-2026-78410) | Restricted bind mounts permit source redirection and privileged metadata changes | No set-ID mount helper or authorized fstab bind mount remains available to the runtime user. |
| [CVE-2026-54369](https://security-tracker.debian.org/tracker/CVE-2026-54369) | A privileged libacl caller follows attacker-controlled path symlinks | The HTTP API does not process user-supplied filesystem paths or modify ACLs and runs without root privileges. |
| [CVE-2025-69720](https://security-tracker.debian.org/tracker/CVE-2025-69720) | `infocmp` processes malicious terminal descriptions | The executable is installed by the base image, but the API does not invoke it or accept terminal-description inputs. |
| [CVE-2026-16742](https://security-tracker.debian.org/tracker/CVE-2026-16742) | systemd-homed user-record changes cause local privilege escalation | Libraries are installed, but the vulnerable `systemd-homed` service is absent. |
| [CVE-2026-9538](https://security-tracker.debian.org/tracker/CVE-2026-9538) | Perl Archive::Tar allocates memory from an untrusted archive size | The vulnerable Archive::Tar module is absent. ReliaMesh does not accept or extract archives. |

The reviewed Debian tracker entries classify several of these as minor issues
with stable-release fixes deferred. This does not erase the scanner's severity
or permit an unconditional clean-scan claim. Moving to Debian unstable only to
remove findings would introduce an unreviewed operating-system change; retain
the supported pinned base until a compatible, tested update is available.

Keep the runtime non-root, do not enable privileged mode or grant host mount or
namespace capabilities, and do not expose a shell or user-executable code path.
Self-hosted deployments that change these constraints must reassess these risks.

## Release and follow-up

The container review permits a release decision based on the documented reduced
exposure; it does not certify zero high findings. The release maintainer must
retain the findings, verify the exact release image, and own the residual risk.
Any newly reachable issue, critical finding, or available applicable fix requires
reassessment before release. No ignore list is configured for these findings.

Recheck the Debian fixes and pinned base by **2026-10-12**, and on every release.
When a supported fix is available, update the base digest, rebuild, rerun the
API/storage tests, and rescan the resulting image. A workflow that enforces zero
high findings would still fail this image; the documented assessment must never
be represented as a clean automated result.

The current full scan and dated verification evidence are private operational
artifacts under `.local/grant-readiness/hardened-container-scan.json` and
`.local/grant-readiness/hardened-container-evidence.json`; earlier reports remain
under `.local/audit/`. The reproducible scan uses
`trivy image --input <image-archive> --scanners vuln --format json`, retaining all
severities. Refresh the database before the scan. Use the [official Trivy database
locations](https://trivy.dev/docs/dev/configuration/db/) and retain the scan date,
image digest, scanner version, and database metadata with the release evidence.
