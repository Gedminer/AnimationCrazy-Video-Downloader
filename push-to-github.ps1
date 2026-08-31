<#
.SYNOPSIS
    安全地把本仓库推送到你的 GitHub（令牌不经过聊天 / 助手之手）。

.DESCRIPTION
    参考「方案 B：令牌不过我的手」。
    - 令牌仅在你本机输入，使用 -AsSecureString，输入时不回显，也不经过任何人或日志。
    - 推送成功后立即把 remote URL 里的令牌抹掉，避免明文留在 .git/config。
    - 推送目标默认是名为 fork 的远程（不动你原有的 origin / 上游）；
      想直接推到 origin，运行时加 -RemoteName origin 即可。

    前置（必须先在网页完成）：
      1) 打开 https://github.com/new 建好空仓库（建议 Private，什么都别勾）。
      2) 打开 https://github.com/settings/personal-access-tokens
         -> Fine-grained tokens -> Generate new token
         - Resource owner：你的账号
         - Repository access：Only select repositories -> 选你刚建的仓库
         - Permissions -> Repository permissions -> Contents: Read and write
         - Expiration：30 天足够
         生成后复制 github_pat_...
      3) 记住你的 GitHub 用户名与该仓库名。

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

# SecureString -> 明文（仅在本进程内存中，不落盘、不打印）
function Unsecure([System.Security.SecureString]$s) {
    $ptr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { return [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($ptr) }
    finally { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

# ---- 1) 收集信息（令牌不回显） ----
$user = Read-Host 'GitHub 用户名'
$tokenSecure = Read-Host -AsSecureString 'GitHub 令牌 (Fine-grained PAT, 输入不回显)'
$token = Unsecure $tokenSecure

if (-not $user -or -not $token) {
    Write-Error '用户名与令牌均不能为空，已中止。'
    exit 1
}

# ---- 2) 确定分支 ----
if (-not $Branch) {
    $Branch = (git rev-parse --abbrev-ref HEAD).Trim()
}
Write-Host "目标分支: $Branch"

# ---- 3) 可选：有未提交改动则提交 ----
$status = (git status --porcelain).Trim()
if ($status) {
    if (-not $CommitMessage) {
        $CommitMessage = Read-Host '检测到未提交改动，请输入提交信息（留空则用默认）'
    }
    if (-not $CommitMessage) { $CommitMessage = 'chore: 更新' }
    git add -A
    git commit -m $CommitMessage
    if ($LASTEXITCODE -ne 0) { Write-Error '提交失败，已中止。'; exit 1 }
    Write-Host '已提交本地改动。'
} else {
    Write-Host '工作区干净，无需提交。'
}

# ---- 4) 设置带令牌的 remote（临时） ----
$tokenUrl = "https://$token@github.com/$user/$Repo.git"
$existing = (git remote get-url $RemoteName 2>$null)
if ($existing) {
    git remote set-url $RemoteName $tokenUrl
} else {
    git remote add $RemoteName $tokenUrl
}
Write-Host "已临时将 $RemoteName 指向带令牌的地址。"

# ---- 5) 推送 ----
try {
    Write-Host '正在推送...'
    git push -u $RemoteName $Branch
    if ($LASTEXITCODE -ne 0) { throw 'git push 返回非零退出码。' }
}
catch {
    # 即使失败也尽量抹掉令牌
    git remote set-url $RemoteName "https://github.com/$user/$Repo.git"
    Write-Error "$_ 请检查：仓库是否已建好、令牌权限是否为 Contents: Read and write、用户名/仓库名是否正确。"
    exit 1
}

# ---- 6) 抹掉令牌（关键！） ----
git remote set-url $RemoteName "https://github.com/$user/$Repo.git"
Write-Host ''
Write-Host '推送成功。'
Write-Host "完成：https://github.com/$user/$Repo"
Write-Host '（已抹掉 remote URL 中的令牌，.git/config 不再含明文令牌）'
