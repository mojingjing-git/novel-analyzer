# 7 本 Project Gutenberg 外文书下载脚本
# 严格按 plan §6.1 复刻（含 2.1.D 修复：try 块开头清掉部分写入文件）
$ids = 1342, 84, 345, 98, 2701, 64317, 1661
$urlTemplates = @(
    "https://www.gutenberg.org/cache/epub/{0}/pg{0}.txt",
    "https://www.gutenberg.org/files/{0}/{0}-0.txt",
    "https://www.gutenberg.org/ebooks/{0}.txt.utf-8"
)
$dstDir = "F:\AI\01_项目\小说分析器\tests\fixtures\gutenberg"
Set-Location $dstDir
foreach ($id in $ids) {
    $dst = "$dstDir\gutenberg_$id.txt"
    if (Test-Path $dst) {
        $size = (Get-Item $dst).Length
        if ($size -gt 1024) {
            Write-Host "[$id] 已存在: $size bytes"
            continue
        } else {
            Write-Host "[$id] 已存在但太小 ($size bytes)，重下"
            Remove-Item $dst -Force
        }
    }
    $downloaded = $false
    $succeeded_template = ""
    foreach ($template in $urlTemplates) {
        $url = $template -f $id
        Write-Host "[$id] 尝试: $url"
        # 2.1.D 修复：try 块开头清掉可能存在的部分写入文件
        Remove-Item $dst -ErrorAction SilentlyContinue
        try {
            Invoke-WebRequest -Uri $url -OutFile $dst -TimeoutSec 60 -UseBasicParsing
            $size = (Get-Item $dst).Length
            if ($size -gt 1024) {
                $downloaded = $true
                $succeeded_template = $template
                Write-Host "[$id] OK: $size bytes"
                break
            } else {
                Write-Warning "[$id] $url 写入仅 $size bytes（< 1024），视为失败并清理"
                Remove-Item $dst -ErrorAction SilentlyContinue
            }
        } catch {
            Write-Warning "[$id] 失败: $url ($($_.Exception.Message))"
            Remove-Item $dst -ErrorAction SilentlyContinue
        }
    }
    if (-not $downloaded) {
        Write-Warning "[$id] 全部 fallback 失败"
    }
}
Write-Host "----- 最终清单 -----"
Get-ChildItem $dstDir -Filter "*.txt" | ForEach-Object { "{0} : {1:N0} bytes" -f $_.Name, $_.Length }
