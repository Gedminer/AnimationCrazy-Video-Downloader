<#
.SYNOPSIS
    Securely push this repo to your GitHub (token never passes through anyone).

.DESCRIPTION
    Scheme B: the token is entered only on your machine via -AsSecureString (no echo,
    not logged). After a successful push the token is stripped from the remote URL so
    no plaintext token remains in .git/config.

    Prerequisites (do these in the GitHub web UI first):
      1) Create an empty repo at https://github.com/new (Private recommended; do not
         check any box).
      2) Generate a Fine-grained PAT:
         - Resource owner: your account
         - Repository access: Only select repositories -> your new repo
         - Permissions -> Repository permissions -> Contents: Read and write
         - Expiration: 30 days is enough
      3) Note your GitHub username and the repo name.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File push-to-github.ps1
    powershell -ExecutionPolicy Bypass -File push-to-github.ps1 -RemoteName origin
#>
[CmdletBinding()]
param(
    [string]$RemoteName = 'fork',
    [string]$Repo = 'AnimationCrazy-Video-Downloader',
    [string]$Branch = '',
    [string]$CommitMessage = ''
)

$ErrorActionPreference = 'Stop'

# SecureString -> plaintext (only in this process memory; never written to disk or printed)
function Unsecure([System.Security.SecureString]$s) {
    $ptr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { return [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($ptr) }
    finally { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

# ---- 1) Collect input (token is not echoed) ----
$user = Read-Host 'GitHub username'
$tokenSecure = Read-Host -AsSecureString 'GitHub token (Fine-grained PAT, input not shown)'
$token = Unsecure $tokenSecure

if (-not $user -or -not $token) {
    Write-Error 'Username and token are both required. Aborted.'
    exit 1
}

# ---- 2) Determine branch ----
if (-not $Branch) {
    $Branch = (git rev-parse --abbrev-ref HEAD).Trim()
}
Write-Host "Target branch: $Branch"

# ---- 3) Optional: commit any pending changes ----
$status = (git status --porcelain).Trim()
if ($status) {
    if (-not $CommitMessage) {
        $CommitMessage = Read-Host 'Uncommitted changes detected. Enter a commit message (blank = default)'
    }
    if (-not $CommitMessage) { $CommitMessage = 'chore: update' }
    git add -A
    git commit -m $CommitMessage
    if ($LASTEXITCODE -ne 0) { Write-Error 'Commit failed. Aborted.'; exit 1 }
    Write-Host 'Local changes committed.'
} else {
    Write-Host 'Working tree clean, no commit needed.'
}

# ---- 4) Set the temporary token-bearing remote ----
$tokenUrl = "https://$token@github.com/$user/${Repo}.git"
$existing = (git remote get-url $RemoteName 2>$null)
if ($existing) {
    git remote set-url $RemoteName $tokenUrl
} else {
    git remote add $RemoteName $tokenUrl
}
Write-Host "Temporarily pointed $RemoteName at the token-bearing URL."

# ---- 5) Push ----
try {
    Write-Host 'Pushing...'
    git push -u $RemoteName $Branch
    if ($LASTEXITCODE -ne 0) { throw 'git push returned a non-zero exit code.' }
}
catch {
    # Strip the token even on failure
    git remote set-url $RemoteName "https://github.com/$user/${Repo}.git"
    Write-Error "$_ Check: repo exists, token has Contents: Read and write, username/repo correct."
    exit 1
}

# ---- 6) Strip the token (critical!) ----
git remote set-url $RemoteName "https://github.com/$user/${Repo}.git"
Write-Host ''
Write-Host 'Push succeeded.'
Write-Host "Done: https://github.com/$user/$Repo"
Write-Host '(Token stripped from the remote URL; .git/config no longer contains a plaintext token.)'
