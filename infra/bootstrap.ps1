#requires -Version 7.4
<#
Creates or verifies ReliaMesh's project-scoped managed infrastructure.
Requires an existing private UTF-8 file containing only the administrator SHA256
digest, not the raw administrator key. Does not deploy an image, rotate a secret,
configure GitHub federation, modify DNS, or change public Cloud Run invocation.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$AdminHashFile,
    [ValidateSet('reliamesh')][string]$Project = 'reliamesh',
    [ValidateSet('asia-south1')][string]$Region = 'asia-south1',
    [switch]$WithHttps
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$projectId = $Project
if ($projectId -cne 'reliamesh') { throw 'Only Google Cloud project reliamesh is authorized.' }
$runtimeAccount = 'reliamesh-runtime@reliamesh.iam.gserviceaccount.com'
$secretName = 'reliamesh-admin-hash'
$secretVersion = '1'
$cleanupPolicyPath = Join-Path $PSScriptRoot 'registry-cleanup.json'
$hashPath = (Resolve-Path -LiteralPath $AdminHashFile).Path
if (-not [System.IO.File]::Exists($hashPath)) { throw 'AdminHashFile must be an existing private file.' }
$adminDigest = [System.IO.File]::ReadAllText($hashPath).Trim()
if ($adminDigest -cnotmatch '^[0-9a-f]{64}$') {
    throw 'AdminHashFile must contain one lowercase SHA256 digest, not a credential JSON document.'
}
Get-Command gcloud -ErrorAction Stop | Out-Null

function Invoke-RmGcloud {
    param([Parameter(Mandatory = $true)][string[]]$Arguments, [switch]$AllowNotFound)
    # Both resource scope and quota attribution are explicit on every invocation.
    # Never set CLI defaults, switch ADC, or echo payloads/credential file contents.
    $result = & gcloud @Arguments --project=$projectId --billing-project=$projectId --quiet --format=json 2>&1
    $exitCode = $LASTEXITCODE
    $providerErrors = ($result | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } | Out-String).Trim()
    $output = ($result | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | Out-String).Trim()
    if ($exitCode -ne 0) {
        if ($AllowNotFound -and ($providerErrors + $output) -match '\bNOT_FOUND\b') { return $null }
        $operation = ($Arguments | Select-Object -First 3) -join ' '
        throw "Project-scoped gcloud operation failed: $operation (exit $exitCode). No provider payload was printed."
    }
    if ($output) { return ($output | ConvertFrom-Json -AsHashtable) }
    return $null
}

function Assert-RmValue {
    param($Actual, $Expected, [string]$Resource)
    if ($Actual -cne $Expected) {
        throw "Existing $Resource differs from the expected configuration; it was not replaced."
    }
}

function Ensure-RmResource {
    param([string[]]$Describe, [string[]]$Create)
    $resource = Invoke-RmGcloud -Arguments $Describe -AllowNotFound
    if ($null -eq $resource) {
        Invoke-RmGcloud -Arguments $Create | Out-Null
        $resource = Invoke-RmGcloud -Arguments $Describe
    }
    return $resource
}

function Ensure-RmBinding {
    param([string[]]$GetPolicy, [string[]]$AddBinding, [string]$Role, [string]$Member)
    $policy = Invoke-RmGcloud -Arguments $GetPolicy
    $found = @($policy['bindings'] | Where-Object {
        $null -ne $_ -and $_['role'] -ceq $Role -and -not $_['condition'] -and $_['members'] -contains $Member
    })
    if ($found.Count -eq 0) {
        Invoke-RmGcloud -Arguments ($AddBinding + @("--role=$Role", "--member=$Member", '--condition=None')) | Out-Null
    }
}

# The project-describe guard itself needs this API. This one narrowly scoped
# enablement follows the literal project guard; all other provisioning follows
# the verified project resource read below.
Invoke-RmGcloud -Arguments @('services', 'enable', 'cloudresourcemanager.googleapis.com') | Out-Null
$verifiedProject = Invoke-RmGcloud -Arguments @('projects', 'describe', $projectId)
Assert-RmValue $verifiedProject['projectId'] 'reliamesh' 'project boundary'
Assert-RmValue $verifiedProject['lifecycleState'] 'ACTIVE' 'project lifecycle'

# The temporary directory inherits the private input directory's Windows ACL.
# POSIX installations additionally apply owner-only permissions before writing.
$privateParent = [System.IO.Path]::GetDirectoryName($hashPath)
$workPath = Join-Path $privateParent ('.reliamesh-bootstrap-' + [guid]::NewGuid().ToString('N'))
[System.IO.Directory]::CreateDirectory($workPath) | Out-Null
if (-not $IsWindows) {
    [System.IO.File]::SetUnixFileMode($workPath, [System.IO.UnixFileMode]::UserRead -bor [System.IO.UnixFileMode]::UserWrite -bor [System.IO.UnixFileMode]::UserExecute)
}
$normalizedHashPath = Join-Path $workPath 'admin-hash.txt'
$existingHashPath = Join-Path $workPath 'existing-hash.txt'
$mergedCleanupPath = Join-Path $workPath 'registry-cleanup.json'
try {
    Write-Host 'Ensuring core APIs in project reliamesh.'
    $apis = @(
        'run.googleapis.com', 'firestore.googleapis.com', 'artifactregistry.googleapis.com',
        'secretmanager.googleapis.com', 'iam.googleapis.com', 'serviceusage.googleapis.com',
        'logging.googleapis.com', 'monitoring.googleapis.com', 'cloudresourcemanager.googleapis.com'
    )
    if ($WithHttps) { $apis += 'compute.googleapis.com' }
    Invoke-RmGcloud -Arguments (@('services', 'enable') + $apis) | Out-Null

    Write-Host 'Ensuring Firestore Standard Native, deletion protection, PITR, and bounded-state retention.'
    $database = Ensure-RmResource -Describe @('firestore', 'databases', 'describe', '--database=(default)') -Create @(
        'firestore', 'databases', 'create', '--database=(default)', "--location=$Region",
        '--edition=standard', '--type=firestore-native', '--delete-protection', '--enable-pitr'
    )
    Assert-RmValue $database['locationId'] $Region 'Firestore location'
    Assert-RmValue $database['type'] 'FIRESTORE_NATIVE' 'Firestore mode'
    if ($database.ContainsKey('databaseEdition')) {
        Assert-RmValue $database['databaseEdition'] 'STANDARD' 'Firestore edition'
    }
    if ($database['deleteProtectionState'] -cne 'DELETE_PROTECTION_ENABLED' -or
        $database['pointInTimeRecoveryEnablement'] -cne 'POINT_IN_TIME_RECOVERY_ENABLED') {
        Invoke-RmGcloud -Arguments @('firestore', 'databases', 'update', '--database=(default)', '--delete-protection', '--enable-pitr') | Out-Null
    }
    # The application writes expires_at = last state update + 7 days + 5 minutes.
    # TTL therefore uses offset zero; adding seven days here would double retention.
    Invoke-RmGcloud -Arguments @('firestore', 'fields', 'ttls', 'update', 'expires_at', '--database=(default)', '--collection-group=rm_states', '--enable-ttl', '--expiration-offset=0s') | Out-Null
    Invoke-RmGcloud -Arguments @('firestore', 'indexes', 'fields', 'update', 'state_json', '--database=(default)', '--collection-group=rm_states', '--disable-indexes') | Out-Null

    Write-Host 'Ensuring the runtime principal and additive project IAM bindings.'
    $runtime = Ensure-RmResource -Describe @('iam', 'service-accounts', 'describe', $runtimeAccount) -Create @(
        'iam', 'service-accounts', 'create', 'reliamesh-runtime', '--display-name=ReliaMesh runtime'
    )
    Assert-RmValue $runtime['email'] $runtimeAccount 'runtime service account'
    foreach ($role in @('roles/datastore.user', 'roles/serviceusage.serviceUsageConsumer')) {
        Ensure-RmBinding -GetPolicy @('projects', 'get-iam-policy', $projectId) -AddBinding @('projects', 'add-iam-policy-binding', $projectId) -Role $role -Member "serviceAccount:$runtimeAccount"
    }

    Write-Host 'Ensuring the exact administrator-digest secret without rotating existing versions.'
    $secret = Ensure-RmResource -Describe @('secrets', 'describe', $secretName) -Create @(
        'secrets', 'create', $secretName, '--replication-policy=automatic', '--labels=app=reliamesh,owner=prefiler-labs'
    )
    $version = Invoke-RmGcloud -Arguments @('secrets', 'versions', 'describe', $secretVersion, "--secret=$secretName") -AllowNotFound
    if ($null -eq $version) {
        # Do not create version 2+ if version 1 was destroyed or removed.
        $versions = @(Invoke-RmGcloud -Arguments @('secrets', 'versions', 'list', "--secret=$secretName", '--limit=1'))
        if ($versions.Count -ne 0) { throw 'Secret history exists; bootstrap will not rotate or replace it.' }
        [System.IO.File]::WriteAllText($normalizedHashPath, $adminDigest, [System.Text.UTF8Encoding]::new($false))
        $version = Invoke-RmGcloud -Arguments @('secrets', 'versions', 'add', $secretName, "--data-file=$normalizedHashPath")
        if (-not $version['name'].EndsWith('/versions/1')) { throw 'Unexpected secret version; do not deploy until the pinned version is reviewed.' }
    }
    Assert-RmValue $version['state'] 'ENABLED' 'administrator secret version'
    Invoke-RmGcloud -Arguments @('secrets', 'versions', 'access', $secretVersion, "--secret=$secretName", "--out-file=$existingHashPath") | Out-Null
    $existingDigest = [System.IO.File]::ReadAllText($existingHashPath)
    if ($existingDigest -cne $adminDigest) {
        throw 'Existing secret version 1 does not match AdminHashFile. Bootstrap never rotates credentials.'
    }
    Ensure-RmBinding -GetPolicy @('secrets', 'get-iam-policy', $secretName) -AddBinding @('secrets', 'add-iam-policy-binding', $secretName) -Role 'roles/secretmanager.secretAccessor' -Member "serviceAccount:$runtimeAccount"

    Write-Host 'Ensuring the Docker registry and its approved cleanup policies.'
    $repository = Ensure-RmResource -Describe @('artifacts', 'repositories', 'describe', 'reliamesh', "--location=$Region") -Create @(
        'artifacts', 'repositories', 'create', 'reliamesh', "--location=$Region", '--repository-format=docker', '--description=ReliaMesh release images'
    )
    Assert-RmValue $repository['format'] 'DOCKER' 'Artifact Registry format'
    # Preserve unrelated policies already attached to this project-owned repository.
    $policies = @{}
    if ($repository['cleanupPolicies']) {
        foreach ($name in $repository['cleanupPolicies'].Keys) {
            $entry = $repository['cleanupPolicies'][$name]
            $entry.Remove('id') | Out-Null
            $entry['name'] = $name
            $policies[$name] = $entry
        }
    }
    foreach ($entry in (Get-Content -LiteralPath $cleanupPolicyPath -Raw | ConvertFrom-Json -AsHashtable)) {
        $policies[$entry['name']] = $entry
    }
    # API reads use action/tagState enums; CLI policy files use their documented spelling.
    foreach ($entry in $policies.Values) {
        if ($entry['action'] -is [string]) {
            $entry['action'] = @{ type = switch ($entry['action']) { 'DELETE' { 'Delete' } 'KEEP' { 'Keep' } default { throw 'Unknown existing cleanup action.' } } }
        }
        if ($entry['condition'] -and $entry['condition']['tagState']) {
            $entry['condition']['tagState'] = $entry['condition']['tagState'].ToLowerInvariant()
        }
    }
    [System.IO.File]::WriteAllText($mergedCleanupPath, (ConvertTo-Json -InputObject @($policies.Values) -Depth 20), [System.Text.UTF8Encoding]::new($false))
    Invoke-RmGcloud -Arguments @('artifacts', 'repositories', 'set-cleanup-policies', 'reliamesh', "--location=$Region", "--policy=$mergedCleanupPath", '--no-dry-run') | Out-Null

    if ($WithHttps) {
        Write-Host 'Ensuring optional HTTPS resources. Existing mismatched resources fail without replacement.'
        $service = Invoke-RmGcloud -Arguments @('run', 'services', 'describe', 'reliamesh-api', "--region=$Region")
        $readyConditions = @($service['status']['conditions'] | Where-Object {
            $null -ne $_ -and $_['type'] -ceq 'Ready' -and $_['status'] -ceq 'True'
        })
        if (-not $service['status']['url'] -or $readyConditions.Count -ne 1) {
            throw 'Deploy reliamesh-api successfully before enabling HTTPS resources.'
        }
        $address = Ensure-RmResource -Describe @('compute', 'addresses', 'describe', 'reliamesh-web-ip', '--global') -Create @('compute', 'addresses', 'create', 'reliamesh-web-ip', '--global', '--ip-version=IPV4', '--network-tier=PREMIUM')
        Assert-RmValue $address['addressType'] 'EXTERNAL' 'public address type'
        Assert-RmValue $address['ipVersion'] 'IPV4' 'public address family'
        $neg = Ensure-RmResource -Describe @('compute', 'network-endpoint-groups', 'describe', 'reliamesh-neg', "--region=$Region") -Create @('compute', 'network-endpoint-groups', 'create', 'reliamesh-neg', "--region=$Region", '--network-endpoint-type=serverless', '--cloud-run-service=reliamesh-api')
        Assert-RmValue $neg['networkEndpointType'] 'SERVERLESS' 'NEG endpoint type'
        Assert-RmValue $neg['cloudRun']['service'] 'reliamesh-api' 'NEG Cloud Run service'
        $backend = Ensure-RmResource -Describe @('compute', 'backend-services', 'describe', 'reliamesh-backend', '--global') -Create @('compute', 'backend-services', 'create', 'reliamesh-backend', '--global', '--load-balancing-scheme=EXTERNAL_MANAGED', '--protocol=HTTP')
        Assert-RmValue $backend['loadBalancingScheme'] 'EXTERNAL_MANAGED' 'backend load balancing scheme'
        Assert-RmValue $backend['protocol'] 'HTTP' 'backend protocol'
        $groups = @($backend['backends'] | Where-Object { $null -ne $_ } | ForEach-Object { $_['group'] })
        if ($groups.Count -eq 0) {
            Invoke-RmGcloud -Arguments @('compute', 'backend-services', 'add-backend', 'reliamesh-backend', '--global', '--network-endpoint-group=reliamesh-neg', "--network-endpoint-group-region=$Region") | Out-Null
        } elseif ($groups.Count -ne 1 -or $groups[0] -cne $neg['selfLink']) {
            throw 'Existing backend targets differ; bootstrap will not remove or replace them.'
        }
        $map = Ensure-RmResource -Describe @('compute', 'url-maps', 'describe', 'reliamesh-map', '--global') -Create @('compute', 'url-maps', 'create', 'reliamesh-map', '--global', '--default-service=reliamesh-backend')
        Assert-RmValue $map['defaultService'] $backend['selfLink'] 'URL map default backend'
        $certificate = Ensure-RmResource -Describe @('compute', 'ssl-certificates', 'describe', 'reliamesh-cert', '--global') -Create @('compute', 'ssl-certificates', 'create', 'reliamesh-cert', '--global', '--domains=reliamesh.com,www.reliamesh.com,api.reliamesh.com')
        Assert-RmValue $certificate['type'] 'MANAGED' 'certificate type'
        Assert-RmValue (($certificate['managed']['domains'] | Sort-Object) -join ',') 'api.reliamesh.com,reliamesh.com,www.reliamesh.com' 'certificate domains'
        $tls = Ensure-RmResource -Describe @('compute', 'ssl-policies', 'describe', 'reliamesh-tls', '--global') -Create @('compute', 'ssl-policies', 'create', 'reliamesh-tls', '--global', '--profile=MODERN', '--min-tls-version=1.2')
        Assert-RmValue $tls['profile'] 'MODERN' 'TLS profile'
        Assert-RmValue $tls['minTlsVersion'] 'TLS_1_2' 'TLS minimum version'
        $proxy = Ensure-RmResource -Describe @('compute', 'target-https-proxies', 'describe', 'reliamesh-https', '--global') -Create @('compute', 'target-https-proxies', 'create', 'reliamesh-https', '--global', '--url-map=reliamesh-map', '--ssl-certificates=reliamesh-cert', '--ssl-policy=reliamesh-tls')
        Assert-RmValue $proxy['urlMap'] $map['selfLink'] 'HTTPS proxy URL map'
        Assert-RmValue $proxy['sslPolicy'] $tls['selfLink'] 'HTTPS proxy TLS policy'
        Assert-RmValue (@($proxy['sslCertificates']) -join ',') $certificate['selfLink'] 'HTTPS proxy certificate'
        $forwarding = Ensure-RmResource -Describe @('compute', 'forwarding-rules', 'describe', 'reliamesh-https-rule', '--global') -Create @('compute', 'forwarding-rules', 'create', 'reliamesh-https-rule', '--global', '--load-balancing-scheme=EXTERNAL_MANAGED', '--network-tier=PREMIUM', '--address=reliamesh-web-ip', '--target-https-proxy=reliamesh-https', '--ports=443')
        Assert-RmValue $forwarding['loadBalancingScheme'] 'EXTERNAL_MANAGED' 'forwarding scheme'
        Assert-RmValue $forwarding['networkTier'] 'PREMIUM' 'forwarding network tier'
        Assert-RmValue $forwarding['IPAddress'] $address['address'] 'forwarding address'
        Assert-RmValue $forwarding['target'] $proxy['selfLink'] 'forwarding target'
        if ($forwarding['portRange'] -notin @('443', '443-443')) { throw 'Existing forwarding ports differ; no ports were replaced.' }
        Write-Host "DNS A records for @ and api must use the allocated address: $($address['address'])"
        Write-Host 'DNS CNAME for www: reliamesh.com. Existing DNS and certificate resources were not replaced.'
        Write-Host "Managed certificate status: $($certificate['managed']['status']). DNS and TLS validation may still be pending."
    }
    Write-Host 'Core infrastructure verified. Deploy an immutable image with infra/deploy.ps1; secret version 1 is pinned.'
} finally {
    # Remove only three exact files created in this invocation's private directory.
    foreach ($temporaryPath in @($normalizedHashPath, $existingHashPath, $mergedCleanupPath)) {
        if ([System.IO.File]::Exists($temporaryPath)) { [System.IO.File]::Delete($temporaryPath) }
    }
    [System.IO.Directory]::Delete($workPath, $false)
    $adminDigest = $existingDigest = $null
}
