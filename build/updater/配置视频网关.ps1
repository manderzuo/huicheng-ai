#Requires -Version 5.1
<#
  配置「视频生成」（AI Work / Seedance）的网关地址与 API Key。

  为什么需要单独一步：
    视频生成走本地 MCP 桥（_internal\vendor\trae-maker-MCP\...\server.mjs），
    桥本身不带任何账号——它只负责把请求转发给你自己的 AI Work 网关。
    网关地址和 Key 必须由使用者本机配置一次，**Key 用 Windows DPAPI 加密**
    存在 %APPDATA%\AIWork\seedance-skill.json，不会进交付包、也不会明文落盘。

  跑法：双击“配置视频网关.bat”。
#>
$ErrorActionPreference = 'Continue'

function Say([string]$m) { Write-Host $m }

$configDir = Join-Path $env:APPDATA 'AIWork'
$configPath = Join-Path $configDir 'seedance-skill.json'
$defaultUrl = 'https://api.gemstory.cn/v1'

Say '=== 视频生成 · 网关配置 ==='
Say ''

# 已有配置先展示（不显示 Key 明文）
if (Test-Path $configPath) {
  try {
    $old = Get-Content $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Say '  当前已配置：'
    Say ('    网关地址: ' + $old.gateway_base_url)
    Say ('    更新时间: ' + $old.updated_at)
    Say ('    API Key : (已加密保存，长度 ' + ([string]$old.api_key_protected).Length + ')')
    Say ''
    $ans = Read-Host '  是否重新配置？(y/N)'
    if ($ans -notmatch '^[Yy]') {
      Say ''
      Say '  保持原配置，未做修改。'
      Say '完成。'
      exit 0
    }
    Say ''
  } catch {
    Say '  已有配置文件读不出来，将覆盖重写。'
  }
}

Say ('  网关地址直接回车 = 默认 ' + $defaultUrl)
$url = Read-Host '  网关地址'
if ([string]::IsNullOrWhiteSpace($url)) { $url = $defaultUrl }
$url = $url.Trim().TrimEnd('/')
if ($url -notmatch '^https?://') {
  Say ''
  Say '[X] 地址要以 http:// 或 https:// 开头。'
  Say '完成。'
  exit 1
}
# 与官方脚本一致：www.gemstory.cn → api.gemstory.cn，缺 /v1 就补上
if ($url -match '://www\.gemstory\.cn') { $url = $url -replace '://www\.gemstory\.cn', '://api.gemstory.cn' }
if ($url -notmatch '/v1$') {
  if ($url -match '/admin(/v1)?$') { $url = $url -replace '/admin(/v1)?$', '/v1' }
  elseif ($url -notmatch '/v1/') { $url = $url + '/v1' }
}

Say ''
Say '  API Key 请粘贴你账号的 Key（输入时不显示，回车确认）'
$secureKey = Read-Host '  API Key' -AsSecureString
if (-not $secureKey -or $secureKey.Length -eq 0) {
  Say ''
  Say '[X] Key 不能为空。'
  Say '完成。'
  exit 1
}

try {
  New-Item -ItemType Directory -Force -Path $configDir | Out-Null
  $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey))
  $body = [ordered]@{
    gateway_base_url  = $url
    api_key_protected = (ConvertFrom-SecureString -SecureString $secureKey)
    updated_at        = [DateTime]::UtcNow.ToString('o')
  }
  $body | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $configPath -Encoding UTF8
  Say ''
  Say ('[OK] 已保存到 ' + $configPath)
  Say ('     网关地址 ' + $url)
  Say ('     Key 已用 Windows DPAPI 加密（只有当前用户能解开）')
} catch {
  Say ''
  Say ('[X] 保存失败：' + $_.Exception.Message)
  Say '完成。'
  exit 1
}

# 顺手验一下网关通不通（只做 DNS/TCP 层，不发业务请求、不扣费）
Say ''
Say '  正在检查网关连通性…'
try {
  $u = [Uri]$url
  $tcp = New-Object Net.Sockets.TcpClient
  $async = $tcp.BeginConnect($u.Host, 443, $null, $null)
  if ($async.AsyncWaitHandle.WaitOne(5000) -and $tcp.Connected) {
    Say ('  [OK] ' + $u.Host + ':443 可连通')
  } else {
    Say ('  [!] ' + $u.Host + ':443 连不上，检查网络/防火墙（配置已保存，网络好了就能用）')
  }
  $tcp.Close()
} catch {
  Say '  [!] 连通性检查跳过'
}

Say ''
Say '下一步：重启 HuichengFleetAI.exe，然后在界面里点“视频生成”测试。'
Say '完成。'
