# HeartApp 联机转发服务 · Windows 开机自启管理
#
#   安装（管理员 PowerShell）：
#     powershell -ExecutionPolicy Bypass -File service-windows.ps1 -Install
#     powershell -ExecutionPolicy Bypass -File service-windows.ps1 -Install -Password "口令" -Port 9000
#
#   查看：
#     powershell -ExecutionPolicy Bypass -File service-windows.ps1 -Status
#
#   卸载：
#     powershell -ExecutionPolicy Bypass -File service-windows.ps1 -Remove
#
# 用系统计划任务（开机触发，以 SYSTEM 运行），不需要安装 NSSM 之类的服务包装器。

param(
    [switch]$Install,
    [switch]$Remove,
    [switch]$Status,
    [int]$Port = 8765,
    [int]$MaxClients = 20,
    [int]$RoomSize = 4,
    [string]$Password = ""
)

$ErrorActionPreference = "Stop"
$TaskName = "HeartAppRelay"
$ServerDir = $PSScriptRoot
$AppDir = Join-Path $ServerDir "app"
$VenvPython = Join-Path $ServerDir ".venv\Scripts\python.exe"
$RunScript = Join-Path $AppDir "service-run.ps1"
$FirewallRule = "HeartApp Relay $Port"

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Show-Usage {
    Write-Host "用法："
    Write-Host "  powershell -ExecutionPolicy Bypass -File service-windows.ps1 -Install [-Password 口令] [-Port 8765]"
    Write-Host "  powershell -ExecutionPolicy Bypass -File service-windows.ps1 -Status"
    Write-Host "  powershell -ExecutionPolicy Bypass -File service-windows.ps1 -Remove"
}

function Write-ServiceLauncher {
    param(
        [string]$Path,
        [string]$WorkingDirectory,
        [string]$Python,
        [string]$AccessPassword,
        [int]$ListenPort,
        [int]$ClientLimit,
        [int]$RoomLimit
    )
    # 单引号字面量避免密码中的 %、$、引号或反引号被再次解释。
    $quotedDirectory = $WorkingDirectory.Replace("'", "''")
    $quotedPython = $Python.Replace("'", "''")
    $lines = @("Set-Location -LiteralPath '$quotedDirectory'")
    if ($AccessPassword) {
        $quotedPassword = $AccessPassword.Replace("'", "''")
        $lines += "`$env:HEARTAPP_RELAY_PASSWORD = '$quotedPassword'"
    }
    $lines += "& '$quotedPython' -m heartapp.relay --allow-plaintext --host 0.0.0.0 --port $ListenPort --max-clients $ClientLimit --room-size $RoomLimit"
    $lines += 'exit $LASTEXITCODE'
    # Windows PowerShell 5.1 需要 BOM 才能可靠读取中文路径和密码。
    $encoding = New-Object System.Text.UTF8Encoding($true)
    [System.IO.File]::WriteAllText($Path, (($lines -join "`r`n") + "`r`n"), $encoding)
}

function Invoke-Install {
    if (-not (Test-Admin)) {
        throw "需要管理员权限。请右键「以管理员身份运行」PowerShell 后再执行。"
    }
    if (-not (Test-Path $VenvPython)) {
        throw "找不到 $VenvPython。请先双击 install-windows.bat 完成安装。"
    }
    if (-not (Test-Path (Join-Path $AppDir "heartapp\relay.py"))) {
        throw "找不到 $AppDir\heartapp\relay.py。请先双击 install-windows.bat。"
    }

    Write-Host "生成启动脚本 $RunScript"
    Write-ServiceLauncher -Path $RunScript -WorkingDirectory $AppDir -Python $VenvPython `
        -AccessPassword $Password -ListenPort $Port -ClientLimit $MaxClients -RoomLimit $RoomSize

    # 启动脚本里可能有访问密码，只允许管理员 / SYSTEM 读取
    & icacls $RunScript /inheritance:r /grant:r "SYSTEM:(R)" "Administrators:(R)" | Out-Null

    Write-Host "注册计划任务 $TaskName（开机启动，以 SYSTEM 运行）"
    $powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $action = New-ScheduledTaskAction -Execute $powershell `
        -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$RunScript`"" -WorkingDirectory $AppDir
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -User "SYSTEM" -RunLevel Highest -Force | Out-Null

    Write-Host "放行防火墙 TCP $Port 入站"
    Get-NetFirewallRule -DisplayName $FirewallRule -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName $FirewallRule -Direction Inbound -Protocol TCP `
        -LocalPort $Port -Action Allow -Profile Any | Out-Null

    Write-Host "启动任务"
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 2

    Write-Host ""
    Write-Host "已安装。客户端填写 ws://这台电脑的IP:$Port"
    Write-Host "查看状态：powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Status"
    Write-Host ""
    Invoke-Status
}

function Invoke-Remove {
    if (-not (Test-Admin)) {
        throw "需要管理员权限才能卸载。"
    }
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "已删除计划任务 $TaskName"
    } else {
        Write-Host "计划任务不存在：$TaskName"
    }
    Get-NetFirewallRule -DisplayName $FirewallRule -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue
    Write-Host "已删除防火墙规则：$FirewallRule"
    foreach ($launcher in @($RunScript, (Join-Path $AppDir "service-run.cmd"))) {
        if (Test-Path -LiteralPath $launcher) {
            Remove-Item -LiteralPath $launcher -Force -ErrorAction SilentlyContinue
            Write-Host "已删除 $launcher"
        }
    }
    Write-Host "卸载完成（代码和虚拟环境仍保留在 $ServerDir）"
}

function Invoke-Status {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "计划任务：未安装"
        return
    }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "计划任务：$($task.TaskName)"
    Write-Host "  状态    ：$($task.State)"
    Write-Host "  上次运行：$($info.LastRunTime)  结果：$($info.LastTaskResult)"
    Write-Host "  下次运行：$($info.NextRunTime)"

    $probePort = if ($Port) { $Port } else { 8765 }
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$probePort/healthz" -TimeoutSec 3 -UseBasicParsing
        Write-Host "  健康检查：$($resp.StatusCode)  $($resp.Content.Trim())"
    } catch {
        Write-Host "  健康检查：失败（$($_.Exception.Message)）"
        Write-Host "  可用 `"Get-ScheduledTaskInfo -TaskName $TaskName`" 或查看事件日志排查"
    }
}

if ($Install) {
    Invoke-Install
} elseif ($Remove) {
    Invoke-Remove
} elseif ($Status) {
    Invoke-Status
} else {
    Show-Usage
}
