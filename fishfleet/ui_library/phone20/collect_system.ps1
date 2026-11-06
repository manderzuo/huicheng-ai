# FishFleet phone20 设置子页批量补采
# 把每个设置子页的「dumpsys activity top 视图树」+ 截图落盘到 raw/ 与 screenshots/
param(
  [string]$Serial = "SERIAL0001",
  [string]$Adb = "C:\Program Files (x86)\huicheng_android\tools\adb.exe"
)
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$raw  = Join-Path $root "raw"
$shot = Join-Path $root "screenshots"
New-Item -ItemType Directory -Force -Path $raw, $shot | Out-Null

function Save-Shot {
  param([string]$Path)
  & $Adb -s $Serial exec-out screencap -p > $Path
}

$pages = @(
  @{k="settings_connectivity"; a="android.settings.WIFI_SETTINGS";               n="連接/Wi-Fi"},
  @{k="settings_bluetooth";    a="android.settings.BLUETOOTH_SETTINGS";          n="藍牙"},
  @{k="settings_data_usage";   a="android.settings.DATA_USAGE_SETTINGS";         n="數據用量"},
  @{k="settings_airplane";     a="android.settings.AIRPLANE_MODE_SETTINGS";      n="飛航模式"},
  @{k="settings_sound";        a="android.settings.SOUND_SETTINGS";              n="音效與震動"},
  @{k="settings_notification"; a="android.settings.NOTIFICATION_SETTINGS";       n="通知"},
  @{k="settings_display";      a="android.settings.DISPLAY_SETTINGS";            n="顯示螢幕"},
  @{k="settings_wallpaper";    a="android.settings.WALLPAPER_SETTINGS";          n="桌布與樣式"},
  @{k="settings_security";     a="android.settings.SECURITY_SETTINGS";           n="生物辨識與安全性"},
  @{k="settings_privacy";      a="android.settings.PRIVACY_SETTINGS";            n="隱私設定"},
  @{k="settings_location";     a="android.settings.LOCATION_SOURCE_SETTINGS";    n="位置"},
  @{k="settings_accounts";     a="android.settings.SYNC_SETTINGS";               n="帳號與備份"},
  @{k="settings_accessibility";a="android.settings.ACCESSIBILITY_SETTINGS";      n="協助工具"},
  @{k="settings_language";     a="android.settings.LOCALE_SETTINGS";             n="語言與輸入"},
  @{k="settings_date_time";    a="android.settings.DATE_SETTINGS";               n="日期與時間"},
  @{k="settings_about";        a="android.settings.DEVICE_INFO_SETTINGS";        n="關於手機"},
  @{k="settings_developer";    a="android.settings.APPLICATION_DEVELOPMENT_SETTINGS"; n="開發者選項"},
  @{k="settings_reset";        a="android.settings.BACKUP_AND_RESET_SETTINGS";   n="重設"},
  @{k="settings_update";       a="android.settings.SYSTEM_UPDATE_SETTINGS";      n="軟體更新"},
  @{k="settings_battery";      a="android.settings.BATTERY_SAVER_SETTINGS";      n="電池省電"},
  @{k="settings_storage";      a="android.settings.INTERNAL_STORAGE_SETTINGS";   n="儲存空間"},
  @{k="settings_apps";         a="android.settings.APPLICATION_SETTINGS";        n="應用程式"}
)

$results = @()
foreach ($p in $pages) {
  $key = $p.k
  if ($p.a) { & $Adb -s $Serial shell am start -a $p.a 2>&1 | Out-Null }
  Start-Sleep -Milliseconds 1500

  $resumed = (& $Adb -s $Serial shell dumpsys activity activities 2>&1 | Select-String "mResumedActivity" | Select-Object -First 1) -join ""
  $act = ""
  if ($resumed -match "u0\s+([^\s]+)") { $act = $Matches[1] }

  $hier = & $Adb -s $Serial shell dumpsys activity top 2>&1 | Out-String
  $hier | Out-File -FilePath (Join-Path $raw "$key.hier.txt") -Encoding utf8
  Save-Shot (Join-Path $shot "$key.png")

  $results += [pscustomobject]@{ key=$key; name=$p.n; activity=$act; bytes=$hier.Length }
  Write-Output ("{0,-24} {1,-56} {2,8} bytes" -f $key, $act, $hier.Length)
}
$results | ConvertTo-Json -Depth 4 | Out-File (Join-Path $root "collect_result.json") -Encoding utf8
Write-Output ("`n完成 {0} 页 -> collect_result.json" -f $results.Count)
