# Managed deployment inventory

The managed service lives exclusively in Google Cloud project `reliamesh`.
Open-source users can instead follow the SQLite and Docker instructions in the
[README](../README.md). Managed infrastructure is not required for self-hosting.

| Resource | Configuration |
|---|---|
| Cloud Run | `reliamesh-api`, `asia-south1`, scale0–2 |
| Database | Firestore Standard Native `(default)`, `asia-south1`, PITR7days |
| Images | Artifact Registry `reliamesh`, `asia-south1` |
| Runtime principal | `reliamesh-runtime@reliamesh.iam.gserviceaccount.com` |
| Deployment principal | `reliamesh-deploy@reliamesh.iam.gserviceaccount.com` |
| Workload identity | pool `github`, provider `reliamesh`; exact repository ID, owner ID, main branch and deploy workflow |
| Public HTTPS | Global external Application Load Balancer, serverless NEG `reliamesh-neg` |
| Domains | `reliamesh.com`, `www.reliamesh.com`, `api.reliamesh.com` |
| TLS | Google-managed certificate `reliamesh-cert`, TLS1.2 minimum, MODERN policy |
| Public address | `34.144.230.119`, global static address `reliamesh-web-ip` |
| Administrator digest | Secret Manager `reliamesh-admin-hash`, explicit version mount |

Public DNS: root and `api` A records point to the static address; `www` is a CNAME
to `reliamesh.com`. The load balancer serves HTTPS on443. Certificate validation
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
