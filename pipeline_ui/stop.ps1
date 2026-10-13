$uiRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtimeDir = Join-Path $uiRoot ".runtime"
$processIds = @()

if (Test-Path -LiteralPath $runtimeDir) {
    foreach ($pidFile in Get-ChildItem -LiteralPath $runtimeDir -Filter "*.pid" -File) {
        $value = Get-Content -LiteralPath $pidFile.FullName -Raw -ErrorAction SilentlyContinue
        if ($value -and $value.Trim() -match '^\d+$') { $processIds += [int]$value.Trim() }
    }
}

foreach ($processId in ($processIds | Select-Object -Unique)) {
    if (Get-Process -Id $processId -ErrorAction SilentlyContinue) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
}

Get-ChildItem -LiteralPath $runtimeDir -Filter "*.pid" -File -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue

Write-Host "NormGraph Workbench stopped."
