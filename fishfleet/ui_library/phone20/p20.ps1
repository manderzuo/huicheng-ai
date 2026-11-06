# ============================================================
# FishFleet 手机一键控制脚本 (phone20 / 通用 Galaxy S10)
# 用法:
#   .\p20.ps1 status
#   .\p20.ps1 wifi on           # 开 Wi-Fi
#   .\p20.ps1 bt off            # 关蓝牙
#   .\p20.ps1 rotate on         # 开自动旋转
#   .\p20.ps1 rotate portrait   # 强制竖屏
#   .\p20.ps1 tz on             # 开自动时区
#   .\p20.ps1 time off          # 关自动时间
#   .\p20.ps1 brightness 180    # 设亮度 0-255
#   .\p20.ps1 auto-bright off   # 关自动亮度
#   .\p20.ps1 dark on           # 深色模式
#   .\p20.ps1 location off      # 关定位
#   .\p20.ps1 volume 8          # 媒体音量 0-15
#   .\p20.ps1 stayon on         # 充电保持唤醒
#   .\p20.ps1 timeout never     # 永不熄屏
#   .\p20.ps1 qs / expand / collapse / home / back / recents
#   .\p20.ps1 shot D:\a.png
#   .\p20.ps1 app kill <pkg> | clear <pkg> | disable <pkg> | enable <pkg>
#   .\p20.ps1 reboot
#
# 指定其他手机: .\p20.ps1 status -Serial SERIAL0001
# ============================================================
param(
  [Parameter(Position = 0)][string]$Action = "status",
  [Parameter(Position = 1)][string]$Value = "",
  [Parameter(Position = 2)][string]$Value2 = "",
  [string]$Serial = "SERIAL0001",
  [string]$Adb = "C:\Program Files (x86)\huicheng_android\tools\adb.exe"
)

$ErrorActionPreference = "Stop"

function Sh {
  param([string]$Cmd)
  $out = & $Adb -s $Serial shell $Cmd 2>&1 | Out-String
  return $out.Trim()
}

function Get-Setting {
  param([string]$Ns, [string]$Key)
  return (Sh "settings get $Ns $Key")
}

function Put-Setting {
  param([string]$Ns, [string]$Key, [string]$Val)
  Sh "settings put $Ns $Key $Val" | Out-Null
  Start-Sleep -Milliseconds 150
  return (Get-Setting $Ns $Key)
}

function Show-Status {
  $rows = @(
    @{ n = "Wi-Fi";           v = (Get-Setting "global" "wifi_on");                k = @{"1" = "开"; "0" = "关"} }
    @{ n = "蓝牙";            v = (Get-Setting "global" "bluetooth_on");           k = @{"1" = "开"; "0" = "关"} }
    @{ n = "飞行模式";        v = (Sh "cmd connectivity airplane-mode");           k = @{"enabled" = "开"; "disabled" = "关"} }
    @{ n = "自动旋转";        v = (Get-Setting "system" "accelerometer_rotation"); k = @{"1" = "开"; "0" = "锁定"} }
    @{ n = "屏幕方向 user_rotation"; v = (Get-Setting "system" "user_rotation");   k = @{"0" = "竖屏"; "1" = "横屏"; "2" = "反竖"; "3" = "反横"} }
    @{ n = "自动时间";        v = (Get-Setting "global" "auto_time");              k = @{"1" = "开"; "0" = "关"} }
    @{ n = "自动时区";        v = (Get-Setting "global" "auto_time_zone");         k = @{"1" = "开"; "0" = "关"} }
    @{ n = "24小时制";        v = (Get-Setting "system" "time_12_24");             k = @{"24" = "是"; "12" = "否"} }
    @{ n = "亮度";            v = (Get-Setting "system" "screen_brightness");      k = @{} }
    @{ n = "自动亮度";        v = (Get-Setting "system" "screen_brightness_mode"); k = @{"1" = "开"; "0" = "关"} }
    @{ n = "息屏超时(ms)";    v = (Get-Setting "system" "screen_off_timeout");     k = @{} }
    @{ n = "充电保持唤醒";    v = (Get-Setting "global" "stay_on_while_plugged_in"); k = @{"7" = "是"; "0" = "否"} }
    @{ n = "深色模式";        v = (Sh "cmd uimode night");                        k = @{"Night mode: yes" = "开"; "Night mode: no" = "关"} }
    @{ n = "定位总开关";      v = (Sh "cmd location is-location-enabled");        k = @{"Location mode: 3" = "开"} }
    @{ n = "护眼模式";        v = (Get-Setting "secure" "blue_light_filter");     k = @{"1" = "开"; "0" = "关"; "null" = "未设置(键值不存在)"} }
  )
  Write-Output ("==== 手机 {0} 系统开关状态 ====" -f $Serial)
  foreach ($r in $rows) {
    $disp = $r.v
    if ($r.k -and $r.k.ContainsKey($r.v)) { $disp = $r.k[$r.v] + "  ($($r.v))" }
    Write-Output ("{0,-22} {1}" -f $r.n, $disp)
  }
  $volAll = Sh "cmd media_session volume --stream 3 --get"
  $volLine = (($volAll -split "`n") | Where-Object { $_ -match "volume is" }) -join ""
  if ($volLine) { Write-Output ("{0,-22} {1}" -f "媒体音量", $volLine.Trim()) }
  $bat = Sh "dumpsys battery"
  $lvl = ((($bat -split "`n") | Where-Object { $_ -match "level:" }) -join "").Trim()
  $st  = ((($bat -split "`n") | Where-Object { $_ -match "status:" }) -join "").Trim()
  Write-Output ("{0,-22} {1}  {2}" -f "电量", $lvl, $st)
}

function Need-Arg {
  param([string]$Hint)
  if ([string]::IsNullOrWhiteSpace($Value)) { throw "缺少参数：$Hint" }
}

switch ($Action.ToLower()) {

  "status" { Show-Status }

  "wifi" {
    Need-Arg "on|off|toggle"
    switch ($Value.ToLower()) {
      "on"     { Sh "svc wifi enable" | Out-Null }
      "off"    { Sh "svc wifi disable" | Out-Null }
      "toggle" { if ((Get-Setting "global" "wifi_on") -eq "1") { Sh "svc wifi disable" | Out-Null } else { Sh "svc wifi enable" | Out-Null } }
      default  { throw "wifi 参数只能是 on|off|toggle" }
    }
    Start-Sleep -Milliseconds 800
    Write-Output ("Wi-Fi 现在: {0}" -f (Get-Setting "global" "wifi_on"))
  }

  "bt" {
    Need-Arg "on|off|toggle"
    switch ($Value.ToLower()) {
      "on"     { Sh "svc bluetooth enable" | Out-Null }
      "off"    { Sh "svc bluetooth disable" | Out-Null }
      "toggle" { if ((Get-Setting "global" "bluetooth_on") -eq "1") { Sh "svc bluetooth disable" | Out-Null } else { Sh "svc bluetooth enable" | Out-Null } }
      default  { throw "bt 参数只能是 on|off|toggle" }
    }
    Start-Sleep -Milliseconds 1200
    Write-Output ("蓝牙 现在: {0}" -f (Get-Setting "global" "bluetooth_on"))
  }

  "airplane" {
    Need-Arg "on|off"
    if ($Value -eq "on") { Sh "cmd connectivity airplane-mode enable" | Out-Null }
    else { Sh "cmd connectivity airplane-mode disable" | Out-Null }
    Start-Sleep -Milliseconds 800
    Write-Output ("飞行模式: {0}" -f (Sh "cmd connectivity airplane-mode"))
  }

  "rotate" {
    Need-Arg "on|off|portrait|landscape"
    switch ($Value.ToLower()) {
      "on"        { Put-Setting "system" "accelerometer_rotation" "1" | Out-Null }
      "off"       { Put-Setting "system" "accelerometer_rotation" "0" | Out-Null }
      "portrait"  { Put-Setting "system" "accelerometer_rotation" "0" | Out-Null; Put-Setting "system" "user_rotation" "0" | Out-Null }
      "landscape" { Put-Setting "system" "accelerometer_rotation" "0" | Out-Null; Put-Setting "system" "user_rotation" "1" | Out-Null }
      default     { throw "rotate 参数只能是 on|off|portrait|landscape" }
    }
    Write-Output ("自动旋转: {0} / user_rotation: {1}" -f (Get-Setting "system" "accelerometer_rotation"), (Get-Setting "system" "user_rotation"))
  }

  "tz" {
    Need-Arg "on|off"
    if ($Value -eq "on") { Put-Setting "global" "auto_time_zone" "1" | Out-Null } else { Put-Setting "global" "auto_time_zone" "0" | Out-Null }
    Write-Output ("自动时区: {0}" -f (Get-Setting "global" "auto_time_zone"))
  }

  "time" {
    Need-Arg "on|off"
    if ($Value -eq "on") { Put-Setting "global" "auto_time" "1" | Out-Null } else { Put-Setting "global" "auto_time" "0" | Out-Null }
    Write-Output ("自动时间: {0}" -f (Get-Setting "global" "auto_time"))
  }

  "hour24" {
    Need-Arg "on|off"
    if ($Value -eq "on") { Put-Setting "system" "time_12_24" "24" | Out-Null } else { Put-Setting "system" "time_12_24" "12" | Out-Null }
    Write-Output ("24小时制: {0}" -f (Get-Setting "system" "time_12_24"))
  }

  "brightness" {
    Need-Arg "0-255"
    $n = [int]$Value
    if ($n -lt 0 -or $n -gt 255) { throw "亮度必须在 0-255" }
    Put-Setting "system" "screen_brightness" "$n" | Out-Null
    Write-Output ("亮度: {0}" -f (Get-Setting "system" "screen_brightness"))
  }

  "auto-bright" {
    Need-Arg "on|off"
    if ($Value -eq "on") { Put-Setting "system" "screen_brightness_mode" "1" | Out-Null } else { Put-Setting "system" "screen_brightness_mode" "0" | Out-Null }
    Write-Output ("自动亮度: {0}" -f (Get-Setting "system" "screen_brightness_mode"))
  }

  "dark" {
    Need-Arg "on|off"
    if ($Value -eq "on") { Sh "cmd uimode night yes" | Out-Null } else { Sh "cmd uimode night no" | Out-Null }
    Start-Sleep -Milliseconds 600
    Write-Output ("深色模式: {0}" -f (Sh "cmd uimode night"))
  }

  "eye" {
    Need-Arg "on|off"
    $v = if ($Value -eq "on") { "1" } else { "0" }
    Put-Setting "secure" "blue_light_filter" $v | Out-Null
    Write-Output ("护眼模式: {0}" -f (Get-Setting "secure" "blue_light_filter"))
  }

  "location" {
    Need-Arg "on|off"
    if ($Value -eq "on") { Sh "cmd location set-location-enabled true" | Out-Null } else { Sh "cmd location set-location-enabled false" | Out-Null }
    Start-Sleep -Milliseconds 400
    Write-Output ("定位: {0}" -f (Sh "cmd location is-location-enabled"))
  }

  "dnd" {
    Need-Arg "on|off"
    Sh ("cmd notification set_dnd " + $Value) | Out-Null
    Start-Sleep -Milliseconds 400
    $z = Sh "dumpsys notification | grep -i zen_mode"
    Write-Output ("勿扰: {0}" -f ($z -join " "))
  }

  "volume" {
    Need-Arg "0-15"
    Sh ("cmd media_session volume --stream 3 --set " + $Value) | Out-Null
    Write-Output ((Sh "cmd media_session volume --stream 3 --get" | Select-String "volume is") -join "")
  }

  "stayon" {
    Need-Arg "on|off"
    if ($Value -eq "on") { Sh "svc power stayon true" | Out-Null } else { Sh "svc power stayon false" | Out-Null }
    Write-Output ("stay_on_while_plugged_in: {0}" -f (Get-Setting "global" "stay_on_while_plugged_in"))
  }

  "timeout" {
    Need-Arg "毫秒数 或 never"
    $ms = if ($Value -eq "never") { "2147483647" } else { $Value }
    Put-Setting "system" "screen_off_timeout" $ms | Out-Null
    Write-Output ("息屏超时: {0}" -f (Get-Setting "system" "screen_off_timeout"))
  }

  "qs"     { Sh "cmd statusbar expand-notifications" | Out-Null; Write-Output "已展开通知栏" }
  "expand" { Sh "cmd statusbar expand-settings" | Out-Null; Write-Output "已展开快捷设置" }
  "collapse" { Sh "cmd statusbar collapse" | Out-Null; Write-Output "已收起" }
  "home"   { Sh "input keyevent 3" | Out-Null; Write-Output "已回桌面" }
  "back"   { Sh "input keyevent 4" | Out-Null; Write-Output "已返回" }
  "recents" { Sh "input keyevent 187" | Out-Null; Write-Output "已打开最近任务" }
  "wake"   { Sh "input keyevent 224" | Out-Null; Write-Output "已点亮屏幕" }
  "sleep"  { Sh "input keyevent 223" | Out-Null; Write-Output "已熄屏" }

  "shot" {
    Need-Arg "输出路径"
    & $Adb -s $Serial exec-out screencap -p > $Value
    Write-Output ("已保存: {0}" -f $Value)
  }

  "app" {
    Need-Arg "kill|clear|disable|enable + 包名"
    $sub = $Value; $pkg = $Value2
    if ([string]::IsNullOrWhiteSpace($pkg)) { throw "缺少包名" }
    switch ($sub.ToLower()) {
      "kill"    { Sh ("am force-stop " + $pkg) | Out-Null; Write-Output ("已停止: {0}" -f $pkg) }
      "clear"   { Write-Output (Sh ("pm clear " + $pkg)); }
      "disable" { Sh ("pm disable-user --user 0 " + $pkg) | Out-Null; Write-Output ("已停用: {0}" -f $pkg) }
      "enable"  { Sh ("pm enable " + $pkg) | Out-Null; Write-Output ("已启用: {0}" -f $pkg) }
      "uninstall" { Sh ("pm uninstall --user 0 " + $pkg) | Out-Null; Write-Output ("已卸载(user0): {0}" -f $pkg) }
      default   { throw "app 子命令只能是 kill|clear|disable|enable|uninstall" }
    }
  }

  "reboot" {
    Write-Output "正在重启，adb 会断开约 30-60 秒..."
    Sh "svc power reboot" | Out-Null
  }

  "raw" {
    Need-Arg "任意 adb shell 命令"
    Write-Output (Sh $Value)
  }

  default { throw "未知动作: $Action。用 .\p20.ps1 status 查看状态，或看文件头注释。" }
}
