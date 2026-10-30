# 彻底移除 HeartApp 自签名证书：删掉受信任根 + 私钥证书。
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\remove_cert.ps1
#
# 移除后：
#   * exe 里已写入的签名还在，但因为没有受信任根，会变回「未知发布者」
#   * build_exe.py 之后不会再自动签名（签名脚本找不到证书就跳过）
#   * 想重新签名：双击「给exe签名.bat」

param(
    [string]$Subject = "CN=HeartApp, O=HeartApp, C=CN",
    [switch]$Quiet
)

$ErrorActionPreference = "Continue"

function Say($msg) { if (-not $Quiet) { Write-Host $msg } }

$removed = 0

Say "正在移除证书：$Subject"

# 1) 受信任根
foreach ($c in @(Get-ChildItem Cert:\CurrentUser\Root -ErrorAction SilentlyContinue |
                 Where-Object { $_.Subject -eq $Subject })) {
    Say "  从受信任根移除：$($c.Thumbprint)"
    try {
        $c | Remove-Item -Force -ErrorAction Stop
        $removed++
    } catch {
        # 用户根存储不允许静默删除，退回 certutil
        & certutil -delstore -user Root $c.Thumbprint 2>&1 | Out-Null
        if (-not (Get-ChildItem Cert:\CurrentUser\Root |
                  Where-Object { $_.Thumbprint -eq $c.Thumbprint })) {
            $removed++
        }
    }
}

# 2) 私钥证书
foreach ($c in @(Get-ChildItem Cert:\CurrentUser\My -ErrorAction SilentlyContinue |
                 Where-Object { $_.Subject -eq $Subject })) {
    Say "  从个人存储移除：$($c.Thumbprint)"
    try {
        $c | Remove-Item -Force -ErrorAction Stop
        $removed++
    } catch {
        & certutil -delstore -user My $c.Thumbprint 2>&1 | Out-Null
        $removed++
    }
}

# 3) 复查
$leftRoot = @(Get-ChildItem Cert:\CurrentUser\Root -ErrorAction SilentlyContinue |
              Where-Object { $_.Subject -eq $Subject })
$leftMy = @(Get-ChildItem Cert:\CurrentUser\My -ErrorAction SilentlyContinue |
            Where-Object { $_.Subject -eq $Subject })

Say ""
if ($leftRoot.Count -eq 0 -and $leftMy.Count -eq 0) {
    Say "已全部移除。"
    Say ""
    Say "提示：exe 里的签名数据还在，但现在没有受信任根，会显示为「未知发布者」。"
    Say "      想要完全干净的未签名文件，重新打包一次即可（不会再自动签名）。"
    exit 0
} else {
    Say "[!] 还有残留：受信任根 $($leftRoot.Count) 个，私钥证书 $($leftMy.Count) 个。"
    Say "    可以打开 certmgr.msc 手动删除。"
    exit 1
}
