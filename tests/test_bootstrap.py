"""Run the provisioning script against an in-process fake gcloud; never call GCP."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(PWSH is None, reason="PowerShell 7.4+ is needed for provisioning script tests")

HARNESS = r"""
param([string]$Bootstrap, [string]$Fixture, [string]$HashFile, [string]$LogFile, [string]$Mode)
$ErrorActionPreference = 'Stop'
$global:FixtureData = Get-Content -LiteralPath $Fixture -Raw | ConvertFrom-Json -AsHashtable
$global:Calls = [System.Collections.Generic.List[object]]::new()
$global:ProjectBindings = [System.Collections.Generic.List[object]]::new()
$global:SecretBindings = [System.Collections.Generic.List[object]]::new()
$global:CleanupCopies = [System.Collections.Generic.List[object]]::new()
function global:gcloud {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
    $global:LASTEXITCODE = 0
    $global:Calls.Add(@($Arguments))
    if ($Arguments -notcontains '--project=reliamesh' -or $Arguments -notcontains '--billing-project=reliamesh') { throw 'Missing explicit project boundary.' }
    if (($Arguments -join ' ') -match '[a-f0-9]{64}') { throw 'Secret digest appeared in command arguments.' }
    $argumentsOnly = @($Arguments | Where-Object { $_ -notlike '--project=*' -and $_ -notlike '--billing-project=*' -and $_ -ne '--quiet' -and $_ -ne '--format=json' })
    $command = $argumentsOnly -join ' '
    if ($Mode -eq 'progress') {
        Write-Output ([System.Management.Automation.ErrorRecord]::new(
            [System.Exception]::new('Simulated provider progress'), 'progress',
            [System.Management.Automation.ErrorCategory]::NotSpecified, $null
        ))
    }
    if ($Mode -eq 'permission-error' -and $command.StartsWith('firestore databases describe')) {
        $global:LASTEXITCODE = 1
        return 'PERMISSION_DENIED'
    }
    foreach ($item in $global:FixtureData['resources']) {
        if ($command -eq $item['describe']) {
            if (-not $item['exists']) { $global:LASTEXITCODE = 1; return 'NOT_FOUND' }
            return (ConvertTo-Json -InputObject $item['value'] -Depth 30 -Compress)
        }
        if ($item['create'] -and $command.StartsWith($item['create'])) {
            $item['exists'] = $true
            return (ConvertTo-Json -InputObject $item['value'] -Depth 30 -Compress)
        }
    }
    if ($command.StartsWith('secrets versions list')) { return '[]' }
    if ($command.StartsWith('secrets versions access')) {
        $path = ($argumentsOnly | Where-Object { $_ -like '--out-file=*' }) -replace '^--out-file=', ''
        $digest = if ($Mode -eq 'digest-mismatch') { 'b' * 64 } else { 'a' * 64 }
        [System.IO.File]::WriteAllText($path, $digest)
        return
    }
    if ($command -eq 'projects get-iam-policy reliamesh') { return (ConvertTo-Json -InputObject @{bindings=@($global:ProjectBindings)} -Depth 20 -Compress) }
    if ($command -eq 'secrets get-iam-policy reliamesh-admin-hash') { return (ConvertTo-Json -InputObject @{bindings=@($global:SecretBindings)} -Depth 20 -Compress) }
    if ($command.StartsWith('projects add-iam-policy-binding') -or $command.StartsWith('secrets add-iam-policy-binding')) {
        $role = ($argumentsOnly | Where-Object { $_ -like '--role=*' }) -replace '^--role=', ''
        $member = ($argumentsOnly | Where-Object { $_ -like '--member=*' }) -replace '^--member=', ''
        $binding = @{role=$role;members=@($member)}
        if ($command.StartsWith('projects')) { $global:ProjectBindings.Add($binding) } else { $global:SecretBindings.Add($binding) }
        return '{}'
    }
    if ($command.StartsWith('artifacts repositories set-cleanup-policies')) {
        $path = ($argumentsOnly | Where-Object { $_ -like '--policy=*' }) -replace '^--policy=', ''
        $global:CleanupCopies.Add((Get-Content -LiteralPath $path -Raw | ConvertFrom-Json -AsHashtable))
        return '{}'
    }
    if ($command.StartsWith('compute backend-services add-backend')) {
        $backend = $global:FixtureData['resources'] | Where-Object { $_['describe'].StartsWith('compute backend-services describe') }
        $neg = $global:FixtureData['resources'] | Where-Object { $_['describe'].StartsWith('compute network-endpoint-groups describe') }
        $backend['value']['backends'] = @(@{group=$neg['value']['selfLink']})
        return '{}'
    }
    if ($command.StartsWith('services enable') -or $command.StartsWith('firestore fields ttls update') -or $command.StartsWith('firestore indexes fields update') -or $command.StartsWith('firestore databases update')) { return '{}' }
    throw "Unmocked gcloud command: $command"
}
$failure = $null
try {
    if ($Mode -eq 'bad-project') { & $Bootstrap -AdminHashFile $HashFile -Project unrelated }
    else {
        & $Bootstrap -AdminHashFile $HashFile -WithHttps:($Mode -eq 'https')
        if ($Mode -in @('new', 'https')) { & $Bootstrap -AdminHashFile $HashFile -WithHttps:($Mode -eq 'https') }
    }
} catch { $failure = $_.Exception.Message }
[System.IO.File]::WriteAllText($LogFile, (ConvertTo-Json -InputObject @{calls=@($global:Calls);cleanup=@($global:CleanupCopies);failure=$failure} -Depth 40))
if ($failure) { Write-Error $failure }
"""


def fixture(new=False):
    base = "https://www.googleapis.com/compute/v1/projects/reliamesh"
    resources = []

    def add(describe, create, value, always=False):
        resources.append({"describe": describe, "create": create, "value": value, "exists": always or not new})

    add("projects describe reliamesh", None, {"projectId": "reliamesh", "lifecycleState": "ACTIVE"}, always=True)
    add("firestore databases describe --database=(default)", "firestore databases create", {
        "locationId": "asia-south1", "type": "FIRESTORE_NATIVE", "databaseEdition": "STANDARD",
        "deleteProtectionState": "DELETE_PROTECTION_ENABLED", "pointInTimeRecoveryEnablement": "POINT_IN_TIME_RECOVERY_ENABLED",
    })
    add("iam service-accounts describe reliamesh-runtime@reliamesh.iam.gserviceaccount.com", "iam service-accounts create", {"email": "reliamesh-runtime@reliamesh.iam.gserviceaccount.com"})
    add("secrets describe reliamesh-admin-hash", "secrets create", {"name": "projects/reliamesh/secrets/reliamesh-admin-hash"})
    add("secrets versions describe 1 --secret=reliamesh-admin-hash", "secrets versions add", {"name": "projects/reliamesh/secrets/reliamesh-admin-hash/versions/1", "state": "ENABLED"})
    add("artifacts repositories describe reliamesh --location=asia-south1", "artifacts repositories create", {
        "format": "DOCKER", "cleanupPolicies": {"operator-keep": {"id": "operator-keep", "action": "KEEP", "mostRecentVersions": {"keepCount": 20}}},
    })
    add("run services describe reliamesh-api --region=asia-south1", None, {"status": {"url": "https://service.example.test", "conditions": [{"type": "Ready", "status": "True"}]}}, always=True)
    add("compute addresses describe reliamesh-web-ip --global", "compute addresses create", {"addressType": "EXTERNAL", "ipVersion": "IPV4", "address": "192.0.2.19"})
    neg_link = base + "/regions/asia-south1/networkEndpointGroups/reliamesh-neg"
    add("compute network-endpoint-groups describe reliamesh-neg --region=asia-south1", "compute network-endpoint-groups create", {"networkEndpointType": "SERVERLESS", "cloudRun": {"service": "reliamesh-api"}, "selfLink": neg_link})
    backend_link = base + "/global/backendServices/reliamesh-backend"
    add("compute backend-services describe reliamesh-backend --global", "compute backend-services create", {"loadBalancingScheme": "EXTERNAL_MANAGED", "protocol": "HTTP", "backends": [] if new else [{"group": neg_link}], "selfLink": backend_link})
    map_link = base + "/global/urlMaps/reliamesh-map"
    add("compute url-maps describe reliamesh-map --global", "compute url-maps create", {"defaultService": backend_link, "selfLink": map_link})
    cert_link = base + "/global/sslCertificates/reliamesh-cert"
    add("compute ssl-certificates describe reliamesh-cert --global", "compute ssl-certificates create", {"type": "MANAGED", "managed": {"domains": ["reliamesh.com", "www.reliamesh.com", "api.reliamesh.com"], "status": "PROVISIONING"}, "selfLink": cert_link})
    tls_link = base + "/global/sslPolicies/reliamesh-tls"
    add("compute ssl-policies describe reliamesh-tls --global", "compute ssl-policies create", {"profile": "MODERN", "minTlsVersion": "TLS_1_2", "selfLink": tls_link})
    proxy_link = base + "/global/targetHttpsProxies/reliamesh-https"
    add("compute target-https-proxies describe reliamesh-https --global", "compute target-https-proxies create", {"urlMap": map_link, "sslPolicy": tls_link, "sslCertificates": [cert_link], "selfLink": proxy_link})
    add("compute forwarding-rules describe reliamesh-https-rule --global", "compute forwarding-rules create", {"loadBalancingScheme": "EXTERNAL_MANAGED", "networkTier": "PREMIUM", "IPAddress": "192.0.2.19", "target": proxy_link, "portRange": "443-443"})
    return {"resources": resources}


def run_bootstrap(tmp_path, mode, data=None):
    harness, config, digest, log = [tmp_path / name for name in ("harness.ps1", "fixture.json", "digest.txt", "calls.json")]
    harness.write_text(HARNESS)
    config.write_text(json.dumps(data or fixture(new=mode in {"new", "https"})))
    digest.write_text("a" * 64)
    result = subprocess.run(  # noqa: S603
        [PWSH, "-NoProfile", "-File", str(harness), "-Bootstrap", str(ROOT / "infra/bootstrap.ps1"),
         "-Fixture", str(config), "-HashFile", str(digest), "-LogFile", str(log), "-Mode", mode],
        capture_output=True, text=True, timeout=45,
    )
    return result, json.loads(log.read_text())


@pytest.mark.parametrize("mode", ["new", "https"])
def test_bootstrap_create_then_reuse_is_idempotent_and_project_scoped(tmp_path, mode):
    result, log = run_bootstrap(tmp_path, mode)
    assert result.returncode == 0, result.stdout + result.stderr
    assert log["failure"] is None
    calls = [" ".join(call) for call in log["calls"]]
    assert sum(call.startswith("secrets versions add ") for call in calls) == 1
    assert sum(call.startswith("firestore databases create ") for call in calls) == 1
    assert sum(call.startswith("projects add-iam-policy-binding ") for call in calls) == 2
    assert sum(call.startswith("secrets add-iam-policy-binding ") for call in calls) == 1
    assert all("--project=reliamesh" in call and "--billing-project=reliamesh" in call for call in calls)
    assert all("a" * 64 not in call for call in calls)
    assert all(" delete " not in call and "auth application-default" not in call for call in calls)
    assert any("--expiration-offset=0s" in call for call in calls)
    assert any("--disable-indexes" in call for call in calls)
    assert any("cloudresourcemanager.googleapis.com" in call for call in calls)
    assert {entry["name"] for entry in log["cleanup"][0]} == {"operator-keep", "delete-old-untagged", "keep-latest-releases"}
    assert not list(tmp_path.glob(".reliamesh-bootstrap-*"))
    if mode == "https":
        assert "192.0.2.19" in result.stdout
        assert sum(call.startswith("compute forwarding-rules create ") for call in calls) == 1
        assert not any("--ports=80" in call for call in calls)


@pytest.mark.parametrize("mode,expected", [
    ("bad-project", "ValidateSet"), ("permission-error", "operation failed"),
    ("digest-mismatch", "does not match"),
])
def test_bootstrap_fails_closed_without_replacing_resources(tmp_path, mode, expected):
    result, log = run_bootstrap(tmp_path, mode)
    assert result.returncode != 0
    if mode == "bad-project":
        assert log["calls"] == []
    else:
        assert expected in log["failure"]
    assert not any("create" in call or "add" in call for call in log["calls"] if mode == "permission-error")
    assert not list(tmp_path.glob(".reliamesh-bootstrap-*"))


def test_existing_wrong_database_location_is_not_changed(tmp_path):
    data = fixture()
    data["resources"][1]["value"]["locationId"] = "another-location"
    result, log = run_bootstrap(tmp_path, "wrong-location", data)
    assert result.returncode != 0
    assert "Firestore location differs" in log["failure"]
    assert not any("create" in call or "delete" in call for call in log["calls"])


def test_provider_stderr_progress_does_not_corrupt_json_output(tmp_path):
    result, log = run_bootstrap(tmp_path, "progress")
    assert result.returncode == 0, result.stdout + result.stderr
    assert log["failure"] is None


def test_https_requires_ready_service_before_creating_load_balancer(tmp_path):
    data = fixture()
    service = next(item for item in data["resources"] if item["describe"].startswith("run services describe"))
    service["value"]["status"]["conditions"][0]["status"] = "False"
    result, log = run_bootstrap(tmp_path, "https", data)
    assert result.returncode != 0
    assert "Deploy reliamesh-api successfully" in log["failure"]
    assert not any(call[0] == "compute" for call in log["calls"])
