#Requires -Version 5.1
<#
  汇城群控AI版 · 只读诊断采集
  ============================================================
  设计原则（写死，不许改）：
    · 本脚本**只读**：只做 Get-* / HTTP GET / adb 查询类命令；
    · **不写程序目录**，报告写到桌面\汇城诊断报告\ 下；
    · **不杀进程、不重启服务、不改配置、不改代码、不点手机**；
    · 任何一步失败都继续往下跑，报告里留空并说明原因。

  用法：
    双击「收集诊断.bat」，或
    powershell -NoProfile -ExecutionPolicy Bypass -File collect_report.ps1
#>
$ErrorActionPreference = 'Continue'

# ---------- 基本参数（可改：程序装在别处就改这里） ----------
$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Api    = 'http://127.0.0.1:8765'
$OutDir = Join-Path ([Environment]::GetFolderPath('Desktop')) '汇城诊断报告'
$Stamp  = Get-Date -Format 'yyyyMMdd_HHmmss'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Report = Join-Path $OutDir ("诊断_" + $Stamp + ".txt")

$lines = New-Object System.Collections.Generic.List[string]
function W([string]$s = '') { $lines.Add($s) }
function Sec([string]$t) {
  W ''
  W ('=' * 62)
  W ('  ' + $t)
  W ('=' * 62)
}

# 只读 HTTP GET；返回对象或 $null，并把错误记进报告
# 注意：PowerShell 5.1 的 Invoke-RestMethod 会把没有 charset 的 JSON 按 ISO-8859-1
# 解码，中文路径会变成「æ±å」这种乱码。这里手工按 UTF-8 解码再转对象。
function Get-Json([string]$path, [int]$timeout = 30) {
  try {
    $resp = Invoke-WebRequest -Uri ($Api + $path) -TimeoutSec $timeout `
              -UseBasicParsing -ErrorAction Stop
    $bytes = $resp.RawContentStream.ToArray()
    $text = [System.Text.Encoding]::UTF8.GetString($bytes)
    if ([string]::IsNullOrWhiteSpace($text)) { return $null }
    return ($text | ConvertFrom-Json)
  } catch {
    W ('  [无法读取 ' + $path + '] ' + $_.Exception.Message)
    return $null
  }
}

# 只读 shell：只跑白名单里的查询命令
function Adb-Read([string]$serial, [string]$cmd, [int]$timeout = 20) {
  $adb = Join-Path $AppDir 'bin\adb.exe'
  if (-not (Test-Path $adb)) { return '' }
  try {
    return (& $adb -s $serial shell $cmd 2>&1 | Out-String).Trim()
  } catch { return '' }
}

W '汇城群控AI版 · 只读诊断报告'
W ('采集时间: ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
W ('程序目录: ' + $AppDir)
W ('操作系统: ' + [Environment]::OSVersion.VersionString)
W ('计算机名: ' + $env:COMPUTERNAME + '    用户: ' + $env:USERNAME)
W ''
W '说明：本报告只读采集，未对系统做任何修改。'

# ============================================================
Sec '1. 版本与文件'
# ============================================================
$verFile = Join-Path $AppDir '版本.txt'
if (Test-Path $verFile) { W ('  版本号   : ' + (Get-Content $verFile -Encoding UTF8 -TotalCount 1)) }
else { W '  版本号   : (缺 版本.txt)' }

$exe = Join-Path $AppDir 'HuichengFleetAI.exe'
if (Test-Path $exe) {
  $f = Get-Item $exe
  W ('  主程序   : ' + [math]::Round($f.Length / 1KB, 0) + ' KB  ' + $f.LastWriteTime.ToString('yyyy-MM-dd HH:mm'))
} else { W '  主程序   : XXX  MISSING' }

$must = @(
  'runtime\python\python.exe',
  'runtime\node\node.exe',
  'runtime\mobile-mcp\start-mobile-mcp.cjs',
  'bin\adb.exe',
  'bin\apk\ADBKeyboard.apk',
  '_internal\fishfleet\ui_library\phone20\actions.json',
  '_internal\vendor\trae-maker-MCP\skills\aiwork-seedance\mcp\server.mjs'
)
W '  关键文件:'
foreach ($m in $must) {
  $ok = Test-Path (Join-Path $AppDir $m)
  W ('    ' + $(if ($ok) { '[OK]  ' } else { '[缺失]' }) + ' ' + $m)
}

# ============================================================
Sec '2. 进程与端口'
# ============================================================
$procs = Get-Process -Name 'HuichengFleetAI' -ErrorAction SilentlyContinue
if ($procs) {
  foreach ($p in $procs) {
    W ('  主程序   : PID ' + $p.Id + '  内存 ' + [math]::Round($p.WorkingSet64 / 1MB, 0) + ' MB  启动 ' + $p.StartTime.ToString('HH:mm:ss'))
  }
  if ($procs.Count -gt 1) { W ('  !! 有 ' + $procs.Count + ' 个主程序实例，可能存在残留') }
} else { W '  主程序   : 未运行（如果刚启动，稍等 20 秒再看）' }

foreach ($n in @('node', 'mobilecli-windows-amd64', 'adb', 'ffmpeg')) {
  $c = @(Get-Process -Name $n -ErrorAction SilentlyContinue).Count
  W ('  ' + $n.PadRight(26) + ': ' + $c + ' 个进程')
}

$listen = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($listen) {
  W ('  端口 8765: 监听中，PID ' + ($listen | Select-Object -First 1).OwningProcess)
} else { W '  端口 8765: 没有监听（后端没起来，或起在别的端口）' }

# 后端可能落到 8766+，顺手看一眼
foreach ($p in 8766..8768) {
  $l = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
  if ($l) { W ('  端口 ' + $p + ': 也有监听，PID ' + ($l | Select-Object -First 1).OwningProcess + '  ← 可能是多实例') }
}

# ============================================================
Sec '3. 后端接口健康（只读 GET）'
# ============================================================
$cfg = Get-Json '/api/control/config' 10
if ($cfg) {
  W ('  /api/control/config : OK')
  try { W ('    协议=' + $cfg.protocol + '  启用=' + $cfg.enabled) } catch {}
}
$st = Get-Json '/api/desktop/state' 10
if ($st) { W ('  /api/desktop/state  : 待退出=' + $st.pending + '  已关闭=' + $st.closed + '  原因=' + $st.reason) }
$diag = Get-Json '/api/log/diagnose' 20
if ($diag -and $diag.ok) {
  W ('  /api/log/diagnose   : OK')
  W ('    日志目录: ' + $diag.log_dir)
}

# ============================================================
Sec '4. 设备（只读）'
# ============================================================
$dev = Get-Json '/api/devices' 150
if ($dev) {
  W ('  API 识别: ' + $dev.total + ' 台，在线 ' + $dev.online + ' 台')
  if ($dev.total -eq 0) { W '  !! 一台都没识别到，重点查第 5 节的 adb 输出' }
} else {
  W '  API 读取失败（可能 40 台枚举超时，看第 5 节 adb 结果）'
}

$adbPath = Join-Path $AppDir 'bin\adb.exe'
$serials = @()
if (Test-Path $adbPath) {
  $raw = & $adbPath devices 2>&1 | Out-String
  $serials = @($raw -split "`r?`n" | ForEach-Object { if ($_ -match '^([^\s]+)\s+device$') { $Matches[1] } })
  W ('  adb devices: ' + $serials.Count + ' 台在线')
  $unauth = @($raw -split "`r?`n" | Where-Object { $_ -match 'unauthorized' }).Count
  if ($unauth -gt 0) { W ('  !! ' + $unauth + ' 台 unauthorized（手机上没点允许）') }
}

# ============================================================
Sec '5. 输入法状态抽样（前 3 台，只读）'
# ============================================================
if ($serials.Count -gt 0) {
  foreach ($s in ($serials | Select-Object -First 3)) {
    $ime = Adb-Read $s 'settings get secure default_input_method'
    $kb  = Adb-Read $s 'pm list packages com.android.adbkeyboard'
    $u2  = Adb-Read $s 'pm list packages com.github.uiautomator'
    $hasStd = ($kb -match 'adbkeyboard')
    $hasU2  = ($u2 -match 'uiautomator')
    W ('  ' + $s)
    W ('    默认输入法 : ' + $ime)
    W ('    标准ADB键盘: ' + $(if ($hasStd) { '已装' } else { '未装' }))
    W ('    u2版ADB键盘: ' + $(if ($hasU2) { '已装' } else { '未装' }))
    if (-not $hasStd -and -not $hasU2) {
      W '    !! 两种都没有 → 搜中文会把旧剪贴板内容贴进去，跑「手机助手安装.bat」'
    }
  }
} else { W '  （无在线设备，跳过）' }

# ============================================================
Sec '6. 最近任务状态（只读）'
# ============================================================
$tasks = Get-Json '/api/execution/tasks' 20
if ($tasks -and $tasks.tasks) {
  $t = @($tasks.tasks)
  W ('  任务总数: ' + $t.Count)
  $byStatus = $t | Group-Object -Property status | Sort-Object Count -Descending
  foreach ($g in $byStatus) { W ('    ' + $g.Name.PadRight(18) + ' x ' + $g.Count) }
  W ''
  W '  最近 8 条:'
  foreach ($x in ($t | Select-Object -Last 8)) {
    W ('    ' + $x.id + '  ' + $x.status.PadRight(16) + ' ' + $x.serial + '  ' + $x.type)
  }
  $stuck = @($t | Where-Object { $_.status -in @('queued', 'executing', 'running') })
  if ($stuck.Count -gt 0) { W ('  !! ' + $stuck.Count + ' 个任务停在执行态，可能占着手机') }
} else { W '  （读不到任务列表）' }

# ============================================================
Sec '7. 日志尾部'
# ============================================================
$logDir = Join-Path $AppDir 'data\logs'
if (Test-Path $logDir) {
  W ('  日志目录: ' + $logDir)
  foreach ($name in @('error.log', 'app.log', 'events.jsonl')) {
    $p = Join-Path $logDir $name
    if (Test-Path $p) {
      $f = Get-Item $p
      W ''
      W ('  ---- ' + $name + '  (' + [math]::Round($f.Length / 1KB, 1) + ' KB, 最后写入 ' + $f.LastWriteTime.ToString('MM-dd HH:mm:ss') + ') ----')
      $n = if ($name -eq 'events.jsonl') { 40 } else { 120 }
      $tail = Get-Content $p -Tail $n -Encoding UTF8 -ErrorAction SilentlyContinue
      foreach ($line in $tail) { W ('  | ' + $line) }
    } else { W ('  (缺 ' + $name + ')') }
  }
} else { W '  data\logs 不存在（程序可能从未成功启动过）' }

# ============================================================
Sec '8. 磁盘与配置（只读）'
# ============================================================
try {
  $d = Get-PSDrive -Name ($AppDir.Substring(0, 1)) -ErrorAction Stop
  W ('  ' + $d.Name + ': 盘  已用 ' + [math]::Round($d.Used / 1GB, 1) + ' GB  可用 ' + [math]::Round($d.Free / 1GB, 1) + ' GB')
  if ($d.Free -lt 2GB) { W '  !! 磁盘剩余不足 2GB，截图/录制会失败' }
} catch { W '  磁盘信息读取失败' }

$llm = Join-Path $AppDir 'data\llm.json'
if (Test-Path $llm) {
  try {
    $j = Get-Content $llm -Raw -Encoding UTF8 | ConvertFrom-Json
    W ''
    W '  AI 配置（只显示是否填写，不显示 Key）：'
    foreach ($k in @('auto', 'video')) {
      $sec = $j.$k
      if ($sec) {
        W ('    ' + $k.PadRight(6) + ' 地址=' + $(if ($sec.url) { '已填' } else { '空' }) + '  模型=' + $(if ($sec.model) { $sec.model } else { '空' }) + '  Key=' + $(if ($sec.key) { '已填' } else { '空' }))
      } else { W ('    ' + $k.PadRight(6) + ' (未配置)') }
    }
  } catch { W '  llm.json 解析失败' }
} else { W '  data\llm.json 不存在（还没配过 AI）' }

# ============================================================
Sec '9. 已知问题自动研判'
# ============================================================
$hint = New-Object System.Collections.Generic.List[string]
$errText = ''
$errPath = Join-Path $logDir 'error.log'
if (Test-Path $errPath) { $errText = (Get-Content $errPath -Tail 400 -Encoding UTF8 -ErrorAction SilentlyContinue | Out-String) }

if ($errText -match 'Non-ASCII text is not supported') {
  $hint.Add('命中：中文输入走了旧路径 → 跑「手机助手安装.bat」装 ADBKeyboard，并确认程序是 r7')
}
if ($errText -match 'start_failed') {
  $hint.Add('命中：Mobile MCP 起不来 → 检查 runtime\node\node.exe 与 runtime\mobile-mcp\start-mobile-mcp.cjs 是否在')
}
if ($errText -match 'device not found|device offline') {
  $hint.Add('命中：设备掉线 → 重插 USB / 手机上重新授权 / 跑 adb自检.bat')
}
if ($errText -match '409|该手机已有AI任务') {
  $hint.Add('命中：设备被占（上一任务没收敛） → 看第 6 节有没有卡在执行态的任务')
}
if ($errText -match '视频传入或媒体库识别未确认') {
  $hint.Add('命中：素材没进媒体库 → 视频要用 mp4；图片注意小红书/抖音各自的格式限制')
}
if ($errText -match 'AIWORK_GATEWAY_BASE_URL') {
  $hint.Add('命中：视频网关没配 → 在界面 AI 设置里填视频地址和 Key（r7 会自动同步给生成服务）')
}
if ($errText -match 'WinError 2') {
  $hint.Add('命中：外部命令找不到 → 多半是旧包残留了开发机绝对路径，确认版本是 r7')
}
if ($procs -and @($procs).Count -gt 1) {
  $hint.Add('命中：多个主程序实例 → 先双击「彻底关闭.bat」再重新启动')
}
if ($listen -and -not $dev) {
  $hint.Add('命中：后端在跑但设备接口读不出来 → 40 台枚举可能超时，等 2 分钟再采一次')
}

if ($hint.Count -eq 0) { W '  未命中已知问题特征，把本报告发回即可人工分析。' }
else { foreach ($h in $hint) { W ('  · ' + $h) } }

# ============================================================
W ''
W ('=' * 62)
W '  报告结束。本文件可直接发给技术方。'
W ('  路径：' + $Report)
W ('=' * 62)

# 写文件（只写桌面报告目录）
try {
  $lines -join "`r`n" | Set-Content -LiteralPath $Report -Encoding UTF8
  Write-Host ''
  Write-Host '[OK] 报告已生成：'
  Write-Host ('     ' + $Report)
  Write-Host ('     大小 ' + [math]::Round((Get-Item $Report).Length / 1KB, 1) + ' KB')
} catch {
  Write-Host ('[X] 写报告失败：' + $_.Exception.Message)
}
