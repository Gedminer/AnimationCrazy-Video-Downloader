<#
.SYNOPSIS
    Securely tag and publish a GitHub release (token never passes through anyone).

.DESCRIPTION
    Scheme B (same idea as push-to-github.ps1): the Fine-grained PAT is entered only
    on your machine via -AsSecureString (no echo, not logged). It is used to:
      1) push the current branch,
      2) create + push the tag (default v2.0.0),
      3) create the GitHub release via the REST API using RELEASE_v2.0.0.md as notes.
    After completion the token is stripped from the remote URL (no plaintext in .git/config).

    Prerequisites in the GitHub web UI:
      - A Fine-grained PAT with Repository permissions -> Contents: Read and write
        (this covers both pushing and creating releases).
      - The repo must already exist (github.com/<owner>/AnimationCrazy-Video-Downloader).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File release.ps1
    powershell -ExecutionPolicy Bypass -File release.ps1 -Tag v2.0.0 -NotesFile RELEASE_v2.0.0.md
#>
[CmdletBinding()]
param(
    [string]$RemoteName = 'origin',
    [string]$Tag = 'v2.0.0',
    [string]$Branch = '',
    [string]$NotesFile = 'RELEASE_v2.0.0.md'
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

# ---- Determine branch ----
if (-not $Branch) {
    $Branch = (git rev-parse --abbrev-ref HEAD | Out-String).Trim()
}
Write-Host "Target branch: $Branch"

# ---- Resolve the real owner/repo of the target remote ----
$owner = $user
$repoName = 'AnimationCrazy-Video-Downloader'
$existing = (git remote get-url $RemoteName 2>$null)
if ($existing -and $existing -match 'github\.com[/:]([^/]+)/(.+?)(\.git)?$') {
    $owner = $matches[1]
    $repoName = $matches[2]
    Write-Host "Target repo: $owner/$repoName"
}

# ---- Helper: push a ref using the temporary token-bearing URL, then strip the token ----
function Push-With-Token {
    param([string]$Ref)
    $url = "https://$token@github.com/$owner/${repoName}.git"
    git remote set-url $RemoteName $url
    try {
        if ($Ref) { git push -u $RemoteName $Ref }
        else { git push -u $RemoteName $Branch }
    }
    finally {
        git remote set-url $RemoteName "https://github.com/$owner/${repoName}.git"
    }
}

# ---- 1) Push the branch (idempotent; safe if already up to date) ----
Write-Host "Pushing branch '$Branch'..."
Push-With-Token -Ref ''
if ($LASTEXITCODE -ne 0) {
    Write-Error "Branch push failed (exit $LASTEXITCODE). Push manually first, then retry. Aborted."
    exit 1
}
Write-Host 'Branch pushed.'

# ---- 2) Create the tag locally if missing ----
$haveTag = (git tag -l $Tag)
if (-not $haveTag) {
    Write-Host "Creating annotated tag '$Tag' at HEAD..."
    git tag -a $Tag -m "Release $Tag"
    if ($LASTEXITCODE -ne 0) { Write-Error "Tag creation failed. Aborted."; exit 1 }
} else {
    Write-Host "Tag '$Tag' already exists locally; reusing."
}

# ---- 3) Push the tag ----
Write-Host "Pushing tag '$Tag'..."
Push-With-Token -Ref $Tag
if ($LASTEXITCODE -ne 0) {
    Write-Error "Tag push failed (exit $LASTEXITCODE). Aborted."
    exit 1
}
Write-Host 'Tag pushed.'

# ---- 4) Read release notes ----
$notes = ''
if (Test-Path $NotesFile) {
    $notes = (Get-Content -Path $NotesFile -Encoding utf8 -Raw).Trim()
}
if (-not $notes) { $notes = "Release $Tag" }

# ---- 5) Create the GitHub release via REST API ----
$apiUrl = "https://api.github.com/repos/$owner/$repoName/releases"
$payload = @{
    tag_name               = $Tag
    name                   = $Tag
    body                   = $notes
    draft                  = $false
    prerelease             = $false
    generate_release_notes = $false
} | ConvertTo-Json -Compress
$bytes = [System.Text.Encoding]::UTF8.GetBytes($payload)
$headers = @{
    Authorization = "Bearer $token"
    Accept        = 'application/vnd.github+json'
    'User-Agent'  = 'ac-dl-release-script'
}
Write-Host "Creating GitHub release '$Tag'..."
try {
    $resp = Invoke-RestMethod -Uri $apiUrl -Method Post -Headers $headers -Body $bytes -ContentType 'application/json; charset=utf-8'
    Write-Host ''
    Write-Host "Release created: $($resp.html_url)"
}
catch {
    $status = $null
    if ($_.Exception.Response) { $status = $_.Exception.Response.StatusCode.value__ }
    Write-Error "Release creation failed (HTTP $status). If 422, a release for this tag may already exist. $_"
    exit 1
}

Write-Host ''
Write-Host 'Done. Token stripped; .git/config contains no plaintext token.'
