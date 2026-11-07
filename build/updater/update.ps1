#Requires -Version 5.1
<#
  汇城AI 一键更新（客户机上双击“一键更新.bat”即跑本脚本）。
  约定：
    · 本脚本与“一键更新.bat”放在安装根目录（AI/）；
    · 更新包叫 update.zip，也放在 AI/（由我发给你，拷进去就行）；
    · 只替换程序文件，data/（配置/数据库/日志）与 runtime/（已装环境）原样保留。
#>
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Zip  = Join-Path $Root 'update.zip'
try { Start-Transcript -Path (Join-Path $Root '更新日志.txt') -Append } catch { }
try { $PSVersionTable.PSVersion.ToString() } catch { }

function Say([string]$m) { Write-Host $m }

function Get-Ver([string]$base) {
  $f = Join-Path $base '版本.txt'
  if (Test-Path $f) { return (Get-Content $f -Encoding UTF8 -TotalCount 1) }
  return ''
}

Say '=== 汇城AI 一键更新 ==='
Say ('目录：' + $Root)
if (!(Test-Path $Zip)) {
  Say '[X] 没找到 update.zip，请把我发你的更新包拷到这个目录再双击。'
  Say '按回车退出。'; exit 1
}
$cur = Get-Ver $Root
Expand-Archive -Path $Zip -DestinationPath (Join-Path $env:TEMP 'hc_update_tmp') -Force 2>$null
$tmp = Join-Path $env:TEMP 'hc_update_tmp'
$new = Get-Ver $tmp
Say ('当前版本：' + ($cur | Out-String)).Trim()
Say ('更新包版本：' + ($new | Out-String)).Trim()
if ($cur -ne '' -and $cur -eq $new) {
  Say '[OK] 已经是最新，不用更新。'; exit 0
}

Say '[1/5] 停主程序…'
foreach ($n in @('HuichengFleetAI', '汇城AI安装引导', '汇城AI构建工具', '网络检测')) {
  taskkill /IM ($n + '.exe') /F 2>$null | Out-Null
}
Start-Sleep -Seconds 3

Say '[2/5] 备份 data/（配置+数据库，日志除外）…'
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$bak = Join-Path $Root ('backup_' + $stamp)
New-Item -ItemType Directory $bak -Force | Out-Null
if (Test-Path (Join-Path $Root 'data')) {
  robocopy (Join-Path $Root 'data') (Join-Path $bak 'data') /E /XD logs /NFL /NDL /NJH /NJS | Out-Null
}
Say ('备份在：' + $bak)

Say '[3/5] 替换程序文件（保留 data/ 与 runtime/）…'
$exclude = @('data', 'runtime')
Get-ChildItem $tmp -Force | ForEach-Object {
  if ($exclude -contains $_.Name) { return }
  $dest = Join-Path $Root $_.Name
  if ($_.PSIsContainer) {
    robocopy $_.FullName $dest /E /NFL /NDL /NJH /NJS | Out-Null
  } else {
    Copy-Item $_.FullName $dest -Force
  }
}
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue

Say '[4/5] 补 runtime 错位（主程序旁边也要有一份）…'
$inner = Join-Path $Root '汇城AI安装引导\runtime'
$top   = Join-Path $Root 'runtime'
if ((Test-Path $inner) -and !(Test-Path $top)) {
  cmd /c ("xcopy `"" + $inner + "`" `"" + $top + "\`" /E /I /Y >nul")
  Say '已把 汇城AI安装引导\runtime 复制到顶层 runtime。'
} else {
  Say 'runtime 位置正常，跳过。'
}

Say '[5/5] 启动并验证…'
Start-Process (Join-Path $Root 'HuichengFleetAI.exe') -WorkingDirectory $Root
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Seconds 2
  try {
    $c = New-Object Net.Sockets.TcpClient
    $r = $c.BeginConnect('127.0.0.1', 8765, $null, $null)
    if ($r.AsyncWaitHandle.WaitOne(1000) -and $c.Connected) { $ok = $true; $c.Close(); break }
    $c.Close()
  } catch { }
}
if ($ok) { Say '[OK] 服务 8765 已起来，更新完成。' }
else { Say '[X] 程序已启动但 8765 没响应，等1分钟再看，或跑 诊断.bat 发我。' }
try { Stop-Transcript } catch { }
Say '完成。'

