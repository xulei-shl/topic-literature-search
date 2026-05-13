# 统计 outputs/wanfang-search/新青年 每个年份的 exported_total 之和
$baseDir = "outputs/wanfang-search/新青年"

$yearDirs = Get-ChildItem $baseDir -Directory -Filter "year-*" | Sort-Object Name

$totalSum = 0
$results = @()

foreach ($dir in $yearDirs) {
    $progressFile = Get-ChildItem $dir.FullName -Filter "progress-*.json" | Select-Object -First 1

    if ($progressFile) {
        $content = Get-Content $progressFile.FullName -Raw | ConvertFrom-Json
        $exportedTotal = $content.runtime.exported_total

        $totalSum += $exportedTotal
        $results += [PSCustomObject]@{
            Year = $dir.Name.Replace("year-", "")
            ExportedTotal = $exportedTotal
        }
    }
}

# 输出每个年份的统计
$results | Format-Table -AutoSize

# 输出总和
Write-Host "`n===================================="
Write-Host "总计 exported_total: $totalSum"
Write-Host "===================================="
