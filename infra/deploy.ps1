param(
    [Parameter(Mandatory = $true)][string]$Image,
    [string]$Region = 'asia-south1'
)
$ErrorActionPreference = 'Stop'
$projectId = 'reliamesh'
if ($Image -notmatch '^asia-south1-docker\.pkg\.dev/reliamesh/reliamesh/api@sha256:[a-f0-9]{64}$') {
    throw 'Deploy requires a digest-pinned image from the reliamesh registry.'
}
$verifiedProject = gcloud projects describe $projectId --project=$projectId --format='value(projectId)'
if ($LASTEXITCODE -ne 0 -or $verifiedProject -ne 'reliamesh') { throw 'Project boundary verification failed.' }
gcloud run deploy reliamesh-api --project=$projectId --region=$Region `
    --image=$Image --service-account=reliamesh-runtime@reliamesh.iam.gserviceaccount.com `
    --port=8080 --cpu=1 --memory=512Mi --concurrency=16 --timeout=30 `
    --min=0 --max=2 --min-instances=0 --max-instances=2 --cpu-throttling `
    --ingress=all `
    --set-env-vars='RM_STORE=firestore,RM_GCP_PROJECT=reliamesh,RM_DATABASE=(default),RM_DAILY_EVENTS=10000' `
    --set-secrets='RM_ADMIN_HASH=reliamesh-admin-hash:1' `
    --labels='app=reliamesh,owner=prefiler-labs,stage=production' --quiet
if ($LASTEXITCODE -ne 0) { throw 'Cloud Run deployment failed.' }
$serviceUrl = gcloud run services describe reliamesh-api --project=$projectId --region=$Region --format='value(status.url)'
if ($LASTEXITCODE -ne 0) { throw 'Service lookup failed.' }
$response = Invoke-RestMethod -Uri "$serviceUrl/health" -TimeoutSec 30
if ($response.status -ne 'ok') { throw 'Deployed health check failed.' }
Write-Output "Verified $serviceUrl"

