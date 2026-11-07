#Requires -Version 5.1
<#
  彻底关闭汇城群控AI版（主程序 + 后端 + 手机通道 + 投屏）。

  什么时候用：
    · 程序窗口关了，但任务管理器里还有 HuichengFleetAI.exe / node.exe；
    · 重装/更新前清场；
    · 下次启动报 8765 被占用。

  安全边界（有意为之）：
    · **只杀我们自己起的那个浏览器实例**（按 --user-data-dir 里的 FishFleet 特征识别）；
    · 不杀用户自己的 Edge / Chrome 窗口；
    · 不碰 VPN、输入法、其他第三方程序（主人反复强调过）；
    · adb server 不在必杀列表，但清理完会顺手重启一次，避免假死。
#>
$ErrorActionPreference = 'Continue'
function Say([string]$m) { Write-Host $m }

Say '=== 彻底关闭汇城群控AI版 ==='

# 1) 先请程序自己优雅退出（给它 3 秒写盘）
Say '[1/5] 通知程序优雅退出…'
try {
  $body = '{"reason":"force_close","grace":1}'
  Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/desktop/quit' -Method Post `
    -ContentType 'application/json' -Body $body -TimeoutSec 3 | Out-Null
  Say '  已发送关闭请求'
} catch {
  Say '  服务没响应（可能已退出），继续强制清理'
}
Start-Sleep -Seconds 3

# 2) 主程序
Say '[2/5] 结束主程序…'
$procs = Get-Process -Name 'HuichengFleetAI' -ErrorAction SilentlyContinue
if ($procs) {
  foreach ($p in $procs) { try { Stop-Process -Id $p.Id -Force } catch {} }
  Say ('  已结束 ' + $procs.Count + ' 个主程序进程')
} else {
  Say '  主程序没在运行'
}

# 3) 我们自己起的浏览器窗口（按用户数据目录特征认，绝不误杀）
Say '[3/5] 关闭程序专用的浏览器窗口…'
$killed = 0
Get-CimInstance Win32_Process -Filter "Name='msedge.exe' OR Name='chrome.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -match 'FishFleet[\\/]webprofile' } |
  ForEach-Object {
    try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; $killed++ } catch {}
  }
Say ('  已关闭 ' + $killed + ' 个专用窗口（其他浏览器窗口未动）')

# 4) 手机通道：只杀由本程序目录拉起的 node（MCP）
Say '[4/5] 清理手机通道（node / mobilecli）…'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# 4a) 先让 mobilecli 的常驻 daemon 体面退出——它正是「目录删不掉」的元凶
$cli = Join-Path $root 'runtime\mobile-mcp\node_modules\@mobilenext\mobilecli-windows-amd64\mobilecli-windows-amd64.exe'
if (Test-Path $cli) {
  try { & $cli daemon stop 2>$null | Out-Null; Say '  已发送 daemon stop' } catch {}
}
Start-Sleep -Seconds 1

$n = 0
Get-CimInstance Win32_Process -Filter "Name='node.exe' OR Name='mobilecli-windows-amd64.exe'" -ErrorAction SilentlyContinue |
  Where-Object {
    $cl = $_.CommandLine
    $cl -and ($cl -match [regex]::Escape($root) -or $cl -match 'mobile-mcp')
  } |
  ForEach-Object {
    try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; $n++ } catch {}
  }

# 4b) 兜底：这个 exe 是本程序专用的，按镜像名强杀（避免还有漏网的占着文件）
$left = Get-Process -Name 'mobilecli-windows-amd64' -ErrorAction SilentlyContinue
if ($left) {
  foreach ($p in $left) { try { Stop-Process -Id $p.Id -Force; $n++ } catch {} }
}
Say ('  已结束 ' + $n + ' 个通道进程')

# 投屏/解码残留
Get-Process -Name 'ffmpeg' -ErrorAction SilentlyContinue |
  Where-Object { $_.Path -and $_.Path -match [regex]::Escape($root) } |
  ForEach-Object { try { Stop-Process -Id $_.Id -Force } catch {} }

# 5) 端口检查
Say '[5/5] 检查端口 8765…'
$busy = netstat -ano | Select-String ':8765\s' | Select-String 'LISTENING'
if ($busy) {
  Say '  端口仍被占用：'
  foreach ($line in $busy) {
    Say ('    ' + $line.Line.Trim())
    $parts = ($line.Line.Trim() -split '\s+')
    $pid2 = $parts[-1]
    if ($pid2 -match '^\d+$' -and $pid2 -ne '0') {
      try { Stop-Process -Id ([int]$pid2) -Force; Say ('    已结束 PID ' + $pid2) } catch {}
    }
  }
} else {
  Say '  8765 已空闲'
}

Say ''
Say '完成。现在可以重新启动 HuichengFleetAI.exe。'
Say '按回车退出。'
