<#
.SYNOPSIS
    Attach the portable build zip as a GitHub release asset (token never passes through anyone).

.DESCRIPTION
    Reads a Fine-grained PAT only on your machine (SecureString, no echo). It then:
      1) resolves owner/repo from the 'origin' remote,
      2) finds (or creates) the GitHub release for the given tag,
      3) uploads ac-dl-portable-v2.0.0.zip as a release asset (idempotent: an
         existing asset with the same name is deleted first).
    The token is used only for the API calls and is never written to disk.

    Prerequisites in the GitHub web UI:
      - A Fine-grained PAT with Repository permissions -> Contents: Read and write.
      - The repo must already exist (github.com/<owner>/AnimationCrazy-Video-Downloader).
      - The zip must be present next to this script (default: ac-dl-portable-v2.0.0.zip).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File upload_asset.ps1
#>
[CmdletBinding()]
param(
    [string]$RemoteName = 'origin',
    [string]$Tag = 'v2.0.0',
    [string]$Asset = ''
)

$ErrorActionPreference = 'Stop'

# ---- Locate git.exe (may not be on PATH in this PowerShell session) ----
function Find-Git {
    $c = Get-Command git -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $candidates = @(
        "$env:ProgramFiles\Git\cmd\git.exe",
        "$env:ProgramFiles\Git\bin\git.exe",
        "${env:ProgramFiles(x86)}\Git\cmd\git.exe",
        "${env:ProgramFiles(x86)}\Git\bin\git.exe",
        "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe",
        "$env:LOCALAPPDATA\Programs\Git\bin\git.exe"
    )
    foreach ($p in $candidates) {
        if ($p -and (Test-Path $p)) { return $p }
    }
    # WorkBuddy bundled PortableGit lives under USERPROFILE\.workbuddy (NOT LOCALAPPDATA)
    $pgBase = "$env:USERPROFILE\.workbuddy\binaries\PortableGit\versions"
    if (Test-Path $pgBase) {
        foreach ($v in (Get-ChildItem -Path $pgBase -Directory -ErrorAction SilentlyContinue)) {
            foreach ($sub in @('cmd', 'bin', 'mingw64\bin')) {
                $g = Join-Path (Join-Path $v.FullName $sub) 'git.exe'
                if (Test-Path $g) { return $g }
            }
        }
    }
    $wb = "$env:USERPROFILE\.workbuddy\binaries"
    if (Test-Path $wb) {
        $found = Get-ChildItem -Path $wb -Recurse -Filter git.exe -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    foreach ($dir in ($env:PATH -split ';')) {
        if (-not $dir) { continue }
        $g = Join-Path $dir 'git.exe'
        if ($g -and (Test-Path $g)) { return $g }
    }
    return $null
}

$gitExe = Find-Git
if (-not $gitExe) {
    Write-Error 'git not found. Install Git for Windows (https://git-scm.com) or add its bin directory to PATH.'
    exit 1
}
$env:PATH = "$(Split-Path -Parent $gitExe);$env:PATH"
Write-Host "Using git: $gitExe"

# SecureString -> plaintext (only in this process memory; never written to disk or printed)
function Unsecure([System.Security.SecureString]$s) {
    $ptr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { return [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($ptr) }
    finally { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

# ---- Collect input (token is not echoed) ----
$user = Read-Host 'GitHub username'
$tokenSecure = Read-Host -AsSecureString 'GitHub token (paste ONLY the github_pat_... string, NOT a URL)'
$token = Unsecure $tokenSecure
if (-not $user -or -not $token) {
    Write-Error 'Username and token are both required. Aborted.'
    exit 1
}
if ($token -match 'https?://' -or $token -match '@') {
    Write-Error 'Token looks like a URL. Paste ONLY the github_pat_... string, not https://...@github.com/... . Aborted.'
    exit 1
}

# ---- Resolve the real owner/repo of the target remote ----
$owner = $user
$repoName = 'AnimationCrazy-Video-Downloader'
$existing = (git remote get-url $RemoteName 2>$null)
if ($existing -and $existing -match 'github\.com[/:]([^/]+)/(.+?)(\.git)?$') {
    $owner = $matches[1]
    $repoName = $matches[2]
    Write-Host "Target repo: $owner/$repoName"
}

# ---- Resolve the asset path ----
if (-not $Asset) { $Asset = Join-Path $PSScriptRoot 'ac-dl-portable-v2.0.0.zip' }
if (-not (Test-Path $Asset)) {
    Write-Error "Asset not found: $Asset. Place the zip next to this script or pass -Asset <path>. Aborted."
    exit 1
}
$assetName = Split-Path -Leaf $Asset
$assetSizeMB = [math]::Round((Get-Item $Asset).Length / 1MB, 1)
Write-Host "Asset: $assetName ($assetSizeMB MB)"

$apiHeaders = @{
    Authorization = "Bearer $token"
    Accept        = 'application/vnd.github+json'
    'User-Agent'  = 'ac-dl-upload-script'
}

# ---- 1) Find or create the release for this tag ----
$relUrl = "https://api.github.com/repos/$owner/$repoName/releases/tags/$Tag"
$releaseId = $null
try {
    $rel = Invoke-RestMethod -Uri $relUrl -Headers $apiHeaders -Method Get
    $releaseId = $rel.id
    Write-Host "Found existing release for $Tag (id $releaseId)."
}
catch {
    $status = $null
    if ($_.Exception.Response) { $status = $_.Exception.Response.StatusCode.value__ }
    if ($status -ne 404) {
        Write-Error "Failed to query release (HTTP $status). $_"
        exit 1
    }
    # Create the release (reuse the markdown notes if present)
    Write-Host "Release for $Tag not found; creating it..."
    $notes = ''
    $nf = Join-Path $PSScriptRoot 'RELEASE_v2.0.0.md'
    if (Test-Path $nf) { $notes = (Get-Content -Path $nf -Encoding utf8 -Raw).Trim() }
    if (-not $notes) { $notes = "Release $Tag" }
    $payload = @{
        tag_name    = $Tag
        name        = $Tag
        body        = $notes
        draft       = $false
        prerelease  = $false
    } | ConvertTo-Json -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($payload)
    $created = Invoke-RestMethod -Uri "https://api.github.com/repos/$owner/$repoName/releases" `
        -Method Post -Headers $apiHeaders -Body $bytes -ContentType 'application/json; charset=utf-8'
    $releaseId = $created.id
    Write-Host "Release created: $($created.html_url)"
}

# ---- 2) Remove any existing asset with the same name (idempotent re-upload) ----
$listUrl = "https://api.github.com/repos/$owner/$repoName/releases/$releaseId/assets"
$assets = Invoke-RestMethod -Uri $listUrl -Headers $apiHeaders -Method Get
foreach ($a in $assets) {
    if ($a.name -eq $assetName) {
        Write-Host "Deleting existing asset '$($a.name)' (id $($a.id))..."
        Invoke-RestMethod -Uri $a.url -Headers $apiHeaders -Method Delete | Out-Null
    }
}

# ---- 3) Upload the asset (GitHub asset uploads go to uploads.github.com) ----
$encName = [System.Uri]::EscapeDataString($assetName)
$upUrl = "https://uploads.github.com/repos/$owner/$repoName/releases/$releaseId/assets?name=$encName"
$upHeaders = @{
    Authorization = "Bearer $token"
    'User-Agent'  = 'ac-dl-upload-script'
}
Write-Host "Uploading $assetName ..."
try {
    $result = Invoke-RestMethod -Uri $upUrl -Headers $upHeaders -Method Post `
        -InFile $Asset -ContentType 'application/zip'
    Write-Host ''
    Write-Host "Uploaded: $($result.browser_download_url)"
}
catch {
    $status = $null
    if ($_.Exception.Response) { $status = $_.Exception.Response.StatusCode.value__ }
    Write-Error "Upload failed (HTTP $status). $_"
    exit 1
}

Write-Host ''
Write-Host 'Done. The portable build is now attached to the v2.0.0 release.'
