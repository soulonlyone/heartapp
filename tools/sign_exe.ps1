# 给 dist\ 下的所有 exe 打自签名代码签名，并确保本机信任这张证书。
#
#   pwsh -NoProfile -ExecutionPolicy Bypass -File tools\sign_exe.ps1
#
# 首次运行会：
#   1. 生成一张自签名代码签名证书（有效期 10 年，私钥不可导出）
#   2. 导出公钥证书到 heartapp-selfsigned.cer
#   3. 把它装进「当前用户 → 受信任的根证书颁发机构」
#   4. 给 4 个 exe 签名
#
# 说明：自签名只在**本机**有效。换一台电脑仍然会显示「未知发布者」，
# 想要全球信任必须买商业代码签名证书。

param(
    [string]$DistDir = "dist",
    [string]$Subject = "CN=HeartApp, O=HeartApp, C=CN",
    [switch]$Quiet
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Say($msg) { if (-not $Quiet) { Write-Host $msg } }

# 从某些宿主进程（例如 python 的 subprocess）启动时，模块自动加载可能没生效，
# 导致 Cert: 驱动器不存在。显式把证书提供程序拉起来，否则会被误判成「没有证书」。
if (-not (Get-PSDrive -Name Cert -ErrorAction SilentlyContinue)) {
    Import-Module Microsoft.PowerShell.Security -ErrorAction SilentlyContinue
    Import-Module PKI -ErrorAction SilentlyContinue
}
if (-not (Get-PSDrive -Name Cert -ErrorAction SilentlyContinue)) {
    Say "[x] 当前会话没有 Cert: 证书驱动器，无法读写证书存储。"
    Say "    请在普通 PowerShell 窗口里运行本脚本，或双击「给exe签名.bat」。"
    exit 2
}

# --- 1) 找或生成证书 ------------------------------------------------------
$cert = Get-ChildItem Cert:\CurrentUser\My -ErrorAction SilentlyContinue |
    Where-Object { $_.Subject -eq $Subject -and $_.HasPrivateKey } |
    Sort-Object NotAfter -Descending |
    Select-Object -First 1

if (-not $cert) {
    Say "未找到证书，正在生成 …"
    try {
        $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject $Subject `
            -CertStoreLocation Cert:\CurrentUser\My -KeyUsage DigitalSignature `
            -KeyExportPolicy NonExportable -NotAfter (Get-Date).AddYears(10) `
            -HashAlgorithm SHA256 -ErrorAction Stop
        Say "  已生成：$($cert.Thumbprint)"
    } catch {
        Say "  [x] 生成证书失败：$($_.Exception.Message)"
        Say "      如果是权限问题，请用普通 PowerShell 窗口手动运行本脚本。"
        exit 2
    }
} else {
    Say "使用已有证书：$($cert.Thumbprint)"
}

# --- 2) 导出公钥证书 ------------------------------------------------------
$cerPath = Join-Path $root "heartapp-selfsigned.cer"
if (-not (Test-Path $cerPath)) {
    try {
        Export-Certificate -Cert $cert -FilePath $cerPath -Type CERT -ErrorAction Stop | Out-Null
        Say "已导出公钥证书：heartapp-selfsigned.cer"
    } catch {
        Say "  [x] 导出证书失败：$($_.Exception.Message)"
    }
}

# --- 3) 确认本机是否已信任这张证书 ----------------------------------------
$trusted = Get-ChildItem Cert:\CurrentUser\Root -ErrorAction SilentlyContinue |
    Where-Object { $_.Thumbprint -eq $cert.Thumbprint }

if (-not $trusted) {
    Say "证书尚未装进「受信任的根证书颁发机构」，正在导入 …"
    try {
        Import-Certificate -FilePath $cerPath -CertStoreLocation Cert:\CurrentUser\Root -ErrorAction Stop | Out-Null
    } catch {
        # 有些环境不允许静默导入 Root，退回 certutil
        & certutil -addstore -f -user Root $cerPath 2>&1 | Out-Null
    }
    $trusted = Get-ChildItem Cert:\CurrentUser\Root -ErrorAction SilentlyContinue |
        Where-Object { $_.Thumbprint -eq $cert.Thumbprint }
    if ($trusted) {
        Say "已安装到受信任根"
    } else {
        Say "  [!] 自动安装失败。请手动双击 heartapp-selfsigned.cer →"
        Say "      「安装证书」→ 当前用户 → 「受信任的根证书颁发机构」。"
    }
} else {
    Say "本机已信任该证书"
}

# --- 4) 签名 --------------------------------------------------------------
$exes = @(Get-ChildItem $DistDir -Recurse -File -Filter "*.exe" -ErrorAction SilentlyContinue)
if ($exes.Count -eq 0) {
    Say "dist\ 下没有找到 exe，先运行 tools\build_exe.py"
    exit 1
}

foreach ($f in $exes) {
    try {
        $sig = Set-AuthenticodeSignature -FilePath $f.FullName -Certificate $cert `
            -HashAlgorithm SHA256 -ErrorAction Stop
        if (-not $Quiet) { "  签名 {0,-32} {1}" -f $f.Name, $sig.Status }
    } catch {
        Say "  [x] 签名失败 $($f.Name)：$($_.Exception.Message)"
    }
}

# --- 5) 复核 --------------------------------------------------------------
$ok = 0
foreach ($f in $exes) {
    if ((Get-AuthenticodeSignature $f.FullName).Status -eq "Valid") { $ok++ }
}
Say ""
Say "签名有效：$ok / $($exes.Count) 个 exe"
if ($ok -eq $exes.Count) { exit 0 } else { exit 1 }
