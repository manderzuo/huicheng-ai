#Requires -Version 5.1
<#
  给手机装「中文输入」组件（ADBKeyboard）。

  为什么必须要它：
    Mobile MCP 输入中文靠"剪贴板 + 粘贴"，需要手机里有 devicekit；
    新机没装就会把**旧剪贴板内容**贴进搜索框（英文却正常，极难排查）。
    本项目改为走 ADBKeyboard 广播（原生支持中文），17 KB，一次装好永久可用。

  说明：
    · 已装 AdbKeyboard 的手机会自动跳过；
    · 程序运行时也会自动装（首次输入中文时），本脚本只是批量提前铺好；
    · 跑法：双击“手机助手安装.bat”。逐台串行，装完会汇总。
#>
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Say([string]$m) { Write-Host $m }

function Find-Adb([string]$root) {
  $p = Join-Path $root 'bin\adb.exe'
  if (Test-Path $p) { return $p }
  return 'adb'
}

function Find-Apk([string]$root) {
  $cands = @(
    (Join-Path $root 'bin\apk\ADBKeyboard.apk'),
    (Join-Path $root 'apk\ADBKeyboard.apk')
  )
  foreach ($c in $cands) { if (Test-Path $c) { return $c } }
  return ''
}

Say '=== 手机中文输入组件 · 批量安装 ==='
$adb = Find-Adb $Root
$apk = Find-Apk $Root
Say ('  adb: ' + $adb)
if ($apk -eq '') {
  Say ''
  Say '[X] 没找到 ADBKeyboard.apk。'
  Say '    正常情况下它应该在 bin\apk\ADBKeyboard.apk（17 KB）。'
  Say '    请确认更新包已完整解压覆盖。'
  Say '完成。'
  exit 1
}
Say ('  apk: ' + $apk)

$raw = & $adb devices 2>$null
$ids = @()
foreach ($line in $raw) { if ($line -match '^([^\s]+)\s+device$') { $ids += $Matches[1] } }
if ($ids.Count -eq 0) {
  Say ''
  Say '[X] 一台设备都没连上。先插好 USB / 打开 USB 调试。'
  Say '完成。'
  exit 1
}
Say ('  在线手机: ' + $ids.Count + ' 台')
Say ''

$done = 0; $skip = 0; $fail = @()
$i = 0
foreach ($id in $ids) {
  $i++
  $prefix = ('[{0}/{1}] {2}' -f $i, $ids.Count, $id)
  $has = (& $adb -s $id shell 'pm list packages com.android.adbkeyboard' 2>$null) -join ''
  if ($has -match 'com\.android\.adbkeyboard') {
    # 已装，顺手确保启用
    & $adb -s $id shell 'ime enable com.android.adbkeyboard/.AdbIME' 2>$null | Out-Null
    Say ($prefix + '  已有，跳过')
    $skip++
    continue
  }
  Say ($prefix + '  安装中…')
  $out = & $adb -s $id install -r $apk 2>&1
  if (($out -join ' ') -match 'Success') {
    & $adb -s $id shell 'ime enable com.android.adbkeyboard/.AdbIME' 2>$null | Out-Null
    $done++
  } else {
    $fail += $id
    Say ('   ' + (($out | Where-Object { $_ -match '\S' } | Select-Object -Last 1)))
  }
}

Say ''
Say '---- 结果 ----'
Say ('  新装成功: ' + $done)
Say ('  已有跳过: ' + $skip)
Say ('  失败:     ' + $fail.Count)
if ($fail.Count -gt 0) {
  Say '  失败的设备：'
  foreach ($f in $fail) { Say ('    [X] ' + $f) }
  Say '  常见原因：手机上没点"允许安装"、开发者选项没开"USB 安装"。'
  Say ('  点了允许后重跑本脚本即可（已装好的会自动跳过）。')
}
if ($fail.Count -eq 0) { Say '  [OK] 全部就绪，程序搜中文不会再把剪贴板内容贴进搜索框。' }
Say '完成。'
