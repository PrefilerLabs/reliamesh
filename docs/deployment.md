# Managed deployment inventory

The managed service lives exclusively in Google Cloud project `reliamesh`.
Open-source users can instead follow the SQLite and Docker instructions in the
[README](../README.md). Managed infrastructure is not required for self-hosting.

| Resource | Configuration |
|---|---|
| Cloud Run | `reliamesh-api`, `asia-south1`, scale 0–2 |
| Database | Firestore Standard Native `(default)`, `asia-south1`, PITR 7 days |
| Images | Artifact Registry `reliamesh`, `asia-south1` |
| Runtime principal | `reliamesh-runtime@reliamesh.iam.gserviceaccount.com` |
| Deployment principal | `reliamesh-deploy@reliamesh.iam.gserviceaccount.com` |
| Workload identity | pool `github`, provider `reliamesh`; exact repository ID, owner ID, main branch and deploy workflow |
| Public HTTPS | Global external Application Load Balancer, serverless NEG `reliamesh-neg` |
| Domains | `reliamesh.com`, `www.reliamesh.com`, `api.reliamesh.com` |
| TLS | Google-managed certificate `reliamesh-cert`, TLS 1.2 minimum, MODERN policy |
| Public address | `34.144.230.119`, global static address `reliamesh-web-ip` |
| Administrator digest | Secret Manager `reliamesh-admin-hash`, explicit version mount |

Public DNS: root and `api` A records point to the static address; `www` is a CNAME
to `reliamesh.com`. The load balancer serves HTTPS on 443. Certificate validation
waits for public DNS; never disable TLS verification to work around provisioning.

Runtime IAM is limited to Firestore document access, service usage within this
project, and access to the single administrator-digest secret. GitHub deployment
IAM permits Cloud Run deployment, writing the project image repository and acting
as the runtime account. It does not grant Firestore data or secret payload access.
Anyone able to change a privileged deployment workflow or deploy arbitrary code
can indirectly use runtime permissions; protect repository ownership and main.

`Deploy` is an explicit GitHub workflow dispatch on main. It repeats release
checks, builds a container, authenticates using short-lived OIDC credentials,
pushes an image and deploys its immutable digest. The workflow intentionally
does not change public invocation IAM. One-time infrastructure provisioning uses
an operator's project-scoped privileges; no organization/billing permissions are
changed. See [operations](operations.md) for rollbacks and retention, and
[cost controls](cost-controls.md) for fixed and usage-based charges.

The direct Cloud Run URL remains available for SDK integration and diagnosis.
Both it and custom domains enforce the same application authentication and
quotas. `/health` is a liveness check and `/ready` is authenticated storage
readiness. Avoid probe paths ending in `z`, which can be intercepted by Cloud Run's
[reserved URL handling](https://docs.cloud.google.com/run/docs/known-issues).

## Reproducible infrastructure bootstrap

[`infra/bootstrap.ps1`](../infra/bootstrap.ps1) creates or verifies the core managed
resources in this inventory. Run it with PowerShell 7.4+ and a current Google Cloud
CLI using an already authorized operator account. It accepts only project
`reliamesh` and region `asia-south1`; every command explicitly sets the resource
project and quota project. It never changes CLI defaults, ADC, organization IAM,
billing accounts, DNS, or another project's resources.

Supply an existing private UTF-8 file containing the lowercase 64-character
administrator SHA256 digest. This is the digest, not the raw key or the JSON file
produced by `reliamesh admin-key`. Keep the input directory outside Git and private
to the operator; temporary payload files inherit its Windows ACL and use an
owner-only directory on POSIX. The script sends payloads through files, never
command arguments or standard output, and removes its exact temporary files.

```powershell
pwsh -NoProfile -File infra/bootstrap.ps1 -AdminHashFile '<private-admin-sha256-file>'
```

The script first checks the literal project boundary and enables Cloud Resource
Manager within that project, because the subsequent project-description guard
depends on that API. After verifying the project is active, it ensures the core
APIs, Standard Native database, deletion protection, seven-day PITR, runtime service
account, additive least-privilege bindings, the administrator-digest secret, Docker
repository and approved cleanup policies. Other repository cleanup policies are
preserved. Cleanup removes eligible untagged images after seven days while keeping
the latest ten versions; it is an asynchronous policy, not immediate deletion.

Firestore TTL uses `rm_states.expires_at` with **zero additional offset**. The
application already writes that field as its last update plus seven days and five
minutes. Adding that duration again to the TTL policy would double retention.
`rm_states.state_json` has single-field indexing disabled. The script uses the
official [database configuration](https://docs.cloud.google.com/sdk/gcloud/reference/firestore/databases/create)
and [TTL field](https://docs.cloud.google.com/sdk/gcloud/reference/firestore/fields/ttls/update) commands.

Existing resources are described before creation. Wrong database location/mode,
credential mismatches, permission failures, and conflicting HTTPS configuration
stop the script. It does not replace databases, rotate credentials, remove
backends, replace certificates or alter DNS. For a new secret it creates version
1; on a repeated run it verifies that exact existing version against the input
digest. Credential rotation follows the separate [operations procedure](operations.md).
Earlier successful steps remain if a later step fails; fix the reported cause and
rerun rather than deleting resources.

After deploying a ready `reliamesh-api` revision with `infra/deploy.ps1`, optionally
create or verify the HTTPS chain:

```powershell
pwsh -NoProfile -File infra/bootstrap.ps1 -AdminHashFile '<private-admin-sha256-file>' -WithHttps
```

This adds the Compute API and verifies/creates `reliamesh-web-ip`, `reliamesh-neg`,
`reliamesh-backend`, `reliamesh-map`, `reliamesh-cert`, `reliamesh-tls`,
`reliamesh-https`, and `reliamesh-https-rule`. The global Premium forwarding rule
serves only port 443 using an `EXTERNAL_MANAGED` backend, with MODERN TLS and a
TLS 1.2 minimum. The script reads the allocated address and prints the required
root/API A records and `www` CNAME; it does not hardcode the inventory's current IP.
New certificates can remain in provisioning until DNS validation completes.

The bootstrap is intentionally separate from release deployment, public Cloud Run
invocation policy, GitHub identity federation/deployment permissions, log retention,
Monitoring alert policies and notification channels. Those existing operational
resources are not recreated by this script. Operator provisioning requires broader
project-scoped administration permissions than the CI deployment account.

The script has offline PowerShell tests covering new/existing resources,
idempotence, project guards, permission failures, secret comparison, temporary-file
cleanup and optional HTTPS. These tests replace `gcloud` with an in-process fake
and perform no cloud calls. They establish script behavior, not a second live
provisioning validation: the inventory above records the separately verified
managed deployment.
