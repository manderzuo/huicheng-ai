#Requires -RunAsAdministrator
<#
.SYNOPSIS
    汇城群控AI版 · USB 驱动自动修复

.DESCRIPTION
    2026-11-07 实测结论（本机 18 台三星 Galaxy S10 全部正常识别）：

      三星手机（VID_04E8）用的是**微软自带的 winusb.inf**，
      不需要三星官方驱动，也不需要 libwdi 编译的 wdi-simple.exe。

      ```
      InstanceId:  USB\VID_04E8&PID_6860&MI_04
      DriverDesc:  WinUsb Device
      DriverInf:   winusb.inf
      Provider:    Microsoft
      ```

    所以"装驱动"这件事，本质是：
        1. 把手机的 ADB 子接口（MI_04）绑到 WinUSB
        2. 如果之前绑错（状态 Unknown / Error），先解绑再重绑

    汇城的 wdi-simple.exe 做的是同一件事，我们不需要那个二进制。

.NOTES
    需要管理员权限。会修改设备驱动绑定，但不会动系统文件。
#>

$ErrorActionPreference = 'Continue'
$OutputEncoding = [System.Text.Encoding]::UTF8

$LOG = @()
function W($s = '') {
    $LOG += $s
    Write-Host $s
}

W '=' * 64
W '  汇城群控AI版 · USB 驱动自动修复'
W ('  时间: ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
W '=' * 64
W ''

# ---------- 0. 管理员检查 ----------
$isAdmin = ([Security.Principal.WindowsPrincipal] `
            [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    W '[错误] 需要管理员权限'
    W '  右键 → 以管理员身份运行'
    Read-Host '按回车退出'
    exit 1
}
W '[0] 管理员权限: OK'

# ---------- 1. 确认 winusb.inf 存在 ----------
W ''
W '[1] 检查系统 WinUSB 驱动'
$winusb = Join-Path $env:SystemRoot 'INF\winusb.inf'
if (Test-Path $winusb) {
    W ('  OK   ' + $winusb)
} else {
    W ('  MISS ' + $winusb + '  —— 系统被精简过，需要补回')
    W '  可以从另一台正常的 Win10 复制 INF\winusb.inf + System32\drivers\winusb.sys'
}

# ---------- 2. 枚举所有 ADB 接口设备 ----------
W ''
W '[2] 枚举 ADB 接口设备'
$all = @()
$adbIfaces = @()
try {
    $all = Get-PnpDevice -ErrorAction Stop | Where-Object {
        $_.FriendlyName -match 'ADB Interface|Android Composite|SAMSUNG_Android'
    }
    # 2026-11-07 关键：三星 PID_6860 是**复合设备**，有多个子接口：
    #   MI_03 = MTP / 文件传输  -> 状态 Error 是**正常的**，不要修
    #   MI_04 = ADB 调试接口    -> 只有这个需要 WinUSB 绑定
    # 原实现不分接口全修，会把手机的文件传输弄坏。
    $adbIfaces = @($all | Where-Object { $_.InstanceId -match '&MI_04' })
} catch {
    W ('  枚举失败: ' + $_.Exception.Message)
}

if (-not $all -or $all.Count -eq 0) {
    W '  没有找到任何 ADB 接口设备。'
    W ''
    W '  可能原因:'
    W '    · 手机没插 / 数据线只充电'
    W '    · 手机的「USB 调试」没打开'
    W '    · 手机 USB 模式选的是「仅充电」而不是「文件传输」/「MIDI」'
    W ''
    Read-Host '按回车退出'
    exit 2
}

W ('  共 ' + $all.Count + ' 个，其中 ADB 子接口 ' + $adbIfaces.Count + ' 个:')
W ''
W '  说明：MI_03 = 文件传输接口（Error 属正常），MI_04 = ADB 调试接口（必须 OK）'
$bad = @()
foreach ($d in $all) {
    $mark = if ($d.Status -eq 'OK') { 'OK  ' } else { 'BAD ' }
    $vid = if ($d.InstanceId -match 'VID_([0-9A-Fa-f]{4})') { $Matches[1].ToUpper() } else { '????' }
    $brand = switch -Regex ($vid) {
        '^04E8$' { '三星'; break }
        '^18D1$' { 'Google'; break }
        '^12D1$' { '华为'; break }
        '^2717$' { '小米'; break }
        '^22D9$' { 'OPPO'; break }
        '^2D95$' { 'vivo'; break }
        '^0E8D$' { 'MTK'; break }
        default  { '未知' }
    }
    $isAdb = $d.InstanceId -match '&MI_04'
    $tag = if ($isAdb) { 'ADB ' } else { '其他' }
    W ('  ' + $mark + ' [' + $d.Status.PadRight(7) + '] ' + $tag + '  VID_' + $vid + ' ' + $brand + '  ' + $d.InstanceId)
    # 只有 ADB 子接口的异常才算需要修复
    if ($isAdb -and $d.Status -ne 'OK') {
        $bad += $d
    }
}

# ---------- 3. 处理异常设备 ----------
W ''
if ($bad.Count -eq 0) {
    W '[3] 所有 ADB 设备状态正常，无需修复'
    W ''
    W '  接下来验证 adb 能否看到手机:'
    W '    .\bin\adb.exe devices'
} else {
    W ('[3] 需要修复 ' + $bad.Count + ' 个设备')

    # 3a. 先把 winusb.inf 加进驱动库（幂等）
    W ''
    W '  3a. 把 winusb.inf 注册到驱动库'
    $r = & pnputil /add-driver $winusb /install 2>&1
    $r | ForEach-Object { W ('      ' + $_) }

    # 3b. 逐个修复
    W ''
    W '  3b. 逐个重新绑定'
    foreach ($d in $bad) {
        W ('     修复: ' + $d.InstanceId)

        # 先试着移除设备（让它重新枚举）
        $rm = & pnputil /remove-device $d.InstanceId 2>&1
        if ($LASTEXITCODE -ne 0) {
            W ('       remove 跳过: ' + ($rm -join ' ').Trim().Substring(0, [Math]::Min(80, ($rm -join ' ').Trim().Length)))
        } else {
            W '       remove OK'
        }
        Start-Sleep -Milliseconds 500
    }

    W ''
    W '  3c. 扫描硬件改动（让设备重新出现）'
    $scan = & pnputil /scan-devices 2>&1
    $scan | ForEach-Object { W ('      ' + $_) }
    Start-Sleep -Seconds 2

    # 3d. 复查
    W ''
    W '  3d. 复查结果'
    $after = Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object {
        $_.FriendlyName -match 'ADB Interface|Android Composite|SAMSUNG_Android'
    }
    $stillBad = @($after | Where-Object { $_.InstanceId -match '&MI_04' -and $_.Status -ne 'OK' })
    W ('      总数 ' + @($after).Count + '，仍异常 ' + $stillBad.Count)
    if ($stillBad.Count -gt 0) {
        W ''
        W '      以下设备仍异常，可能需要手动指定驱动:'
        foreach ($d in $stillBad) {
            W ('        ' + $d.InstanceId)
        }
        W ''
        W '      手动步骤:'
        W '        1. 打开「设备管理器」(devmgmt.msc)'
        W '        2. 找到带黄色感叹号的 ADB Interface'
        W '        3. 右键 → 更新驱动程序 → 浏览我的电脑'
        W '        4. 让我从列表选择 → 通用串行总线设备 → WinUsb Device'
    }
}

# ---------- 4. 重启 adb 验证 ----------
W ''
W '[4] 重启 adb 并验证'
$adbCandidates = @(
    (Join-Path $PSScriptRoot 'bin\adb.exe'),
    (Join-Path $PSScriptRoot 'adb.exe'),
    'C:\Program Files (x86)\huicheng_android\tools\adb.exe'
)
$adb = $adbCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $adb) {
    W '  找不到 adb.exe，跳过验证'
} else {
    W ('  使用: ' + $adb)
    & $adb kill-server 2>&1 | Out-Null
    Start-Sleep -Seconds 1
    $devs = & $adb devices 2>&1
    W '  --- adb devices ---'
    $devs | ForEach-Object { W ('  ' + $_) }

    $okCount = @($devs | Where-Object { $_ -match '\tdevice$' }).Count
    $unauthCount = @($devs | Where-Object { $_ -match 'unauthorized' }).Count
    W ''
    W ('  已授权: ' + $okCount + ' 台')
    W ('  未授权: ' + $unauthCount + ' 台')
    if ($unauthCount -gt 0) {
        W '  >>> 未授权的手机需要在屏幕上点「允许 USB 调试」'
        W '  >>> 手机不能拔插的话：开发者选项 → 撤销 USB 调试授权 → 关开 USB 调试'
    }
    if ($okCount -eq 0 -and $unauthCount -eq 0) {
        W '  >>> 一台都没看到。检查「USB 调试」是否打开、USB 模式是否为「文件传输」'
    }
}

# ---------- 5. 保存 ----------
W ''
W '=' * 64
$outFile = Join-Path $PSScriptRoot '驱动修复.txt'
try {
    $LOG -join "`r`n" | Out-File -FilePath $outFile -Encoding UTF8 -Force
    W '  结果已保存: ' + $outFile
} catch {
    W ('  保存失败: ' + $_.Exception.Message)
}
W '=' * 64
Read-Host '按回车退出'
