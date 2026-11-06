# 从 raw/*.hier.txt（dumpsys activity top 视图树）提取控件表
# 输出 system/pages_controls.json：每页 -> {activity, count, controls:[{cls,id,b(绝对坐标),c}]}
# 注意：dumpsys 的 bounds 是相对父容器的，本脚本按缩进层级累加父偏移，还原出绝对坐标。
param([string]$Root = "")
$ErrorActionPreference = "Stop"
if (-not $Root) { $Root = Split-Path -Parent $MyInvocation.MyCommand.Path }
$rawDir  = Join-Path $Root "raw"
$outFile = Join-Path $Root "system\pages_controls.json"

$re = [regex]'^(?<ind>\s*)(?<cls>[A-Za-z0-9_\.\$]+)\{(?<hash>[0-9a-f]+)\s+(?<flags>[^\s]+)\s+[^\s]*\s+(?<x1>\-?\d+),(?<y1>\-?\d+)\-(?<x2>\-?\d+),(?<y2>\-?\d+)(?:\s+#[0-9a-f]+\s+(?<pkg>[A-Za-z0-9_\.]+):id/(?<id>[A-Za-z0-9_]+))?\}'
$reAct = [regex]'(?m)^[ \t]*ACTIVITY\s+(?<act>\S+)'

$result = [ordered]@{}
Get-ChildItem $rawDir -Filter *.hier.txt | Sort-Object Name | ForEach-Object {
  $key = $_.BaseName -replace '\.hier$', ''
  $text = Get-Content $_.FullName -Raw -Encoding UTF8

  $lastAct = $null; $start = 0
  foreach ($m in $reAct.Matches($text)) { $lastAct = $m.Groups['act'].Value; $start = $m.Index }
  $body = $text.Substring($start)

  $stack = @{}
  $items = @()
  foreach ($line in ($body -split "`n")) {
    $m = $re.Match($line)
    if (-not $m.Success) { continue }
    $depth = $m.Groups['ind'].Value.Length
    $x1 = [int]$m.Groups['x1'].Value; $y1 = [int]$m.Groups['y1'].Value
    $x2 = [int]$m.Groups['x2'].Value; $y2 = [int]$m.Groups['y2'].Value
    if ($x2 -le $x1 -or $y2 -le $y1) { continue }

    $parent = $null
    foreach ($d in ($stack.Keys | Sort-Object -Descending)) {
      if ($d -lt $depth) { $parent = $stack[$d]; break }
    }
    $ox = 0; $oy = 0
    if ($parent) { $ox = $parent[0]; $oy = $parent[1] }
    $ax1 = $ox + $x1; $ay1 = $oy + $y1; $ax2 = $ox + $x2; $ay2 = $oy + $y2
    $stack[$depth] = @($ax1, $ay1)

    $id = $m.Groups['id'].Value
    $cls = $m.Groups['cls'].Value
    $flags = $m.Groups['flags'].Value
    $clickable = $flags -match 'C'
    $isLayout = $cls -match 'Layout|ViewGroup|RecyclerView|ScrollView|ViewPager|ListView|WebView|Toolbar'
    if (-not $id -and -not $isLayout -and -not $clickable) { continue }

    $items += [pscustomobject]@{
      cls = $cls
      id  = $(if ($id) { "$($m.Groups['pkg'].Value):id/$id" } else { $null })
      b   = "$ax1,$ay1-$ax2,$ay2"
      c   = $clickable
    }
  }

  $result[$key] = [pscustomobject]@{
    activity = $lastAct
    count    = $items.Count
    controls = $items
  }
  Write-Output ("{0,-26} {1,-58} {2,4}" -f $key, $lastAct, $items.Count)
}
$result | ConvertTo-Json -Depth 6 | Out-File $outFile -Encoding utf8
Write-Output ("`n-> {0} ({1:N0} KB)" -f $outFile, ((Get-Item $outFile).Length / 1KB))
