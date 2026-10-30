# 截取指定标题的窗口。给 tools\screenshot_demo.py 配套用。
#
#   pwsh -NoProfile -File tools\capture_window.ps1 -Out shot.png -Title "窗口标题"
#
# 只按窗口标题精确匹配（之前用模糊匹配误伤过别的窗口）。

param(
    [Parameter(Mandatory = $true)][string]$Out,
    [string]$Title = '心率记录仪 — 从智能手表读取并记录心率'
)

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class WinCap {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
  public static IntPtr Found = IntPtr.Zero;
  public static string Want = "";
  public static bool Cb(IntPtr h, IntPtr l) {
    if (!IsWindowVisible(h)) return true;
    StringBuilder sb = new StringBuilder(512);
    GetWindowText(h, sb, 512);
    if (sb.ToString() == Want) { Found = h; return false; }
    return true;
  }
}
"@

[WinCap]::Want = $Title
[void][WinCap]::EnumWindows([WinCap+EnumProc]{ param($h, $l) [WinCap]::Cb($h, $l) }, [IntPtr]::Zero)
$h = [WinCap]::Found
if ($h -eq [IntPtr]::Zero) {
    Write-Output "NOTFOUND"
    exit 1
}

[void][WinCap]::SetForegroundWindow($h)
Start-Sleep -Milliseconds 900

$r = New-Object WinCap+RECT
[void][WinCap]::GetWindowRect($h, [ref]$r)
$w = $r.Right - $r.Left
$ht = $r.Bottom - $r.Top

$bmp = New-Object System.Drawing.Bitmap $w, $ht
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.Left, $r.Top, 0, 0, (New-Object System.Drawing.Size $w, $ht))
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose()
$bmp.Dispose()

Write-Output "OK ${w}x${ht} -> $Out"
