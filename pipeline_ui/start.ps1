$ErrorActionPreference = "Stop"
$uiRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtimeDir = Join-Path $uiRoot ".runtime"
$python = Join-Path (Split-Path -Parent $uiRoot) ".venv-testgen\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Create .venv-testgen and install pipeline/requirements/pipeline.txt first."
}
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null

function Get-ListenerProcessId([int]$port) {
    $line = netstat.exe -ano | Select-String "[:.]$port\s+.*LISTENING\s+\d+\s*$" | Select-Object -First 1
    if (-not $line) { return $null }
    return [int](($line.Line.Trim() -split '\s+')[-1])
}

function Test-LocalUrl([string]$url) {
    try {
        return (Invoke-WebRequest -UseBasicParsing $url -TimeoutSec 2).StatusCode -eq 200
    }
    catch {
        return $false
    }
}

if ((Test-LocalUrl "http://127.0.0.1:8765/api/status") -and (Test-LocalUrl "http://localhost:3000")) {
    Write-Host "NormGraph Workbench is already running: http://localhost:3000"
    Write-Host "Run .\stop.ps1 when you are finished."
    exit 0
}

$occupied = @()
if (Get-ListenerProcessId 8765) { $occupied += 8765 }
if (Get-ListenerProcessId 3000) { $occupied += 3000 }
if ($occupied.Count) {
    throw "Cannot start because local port(s) $($occupied -join ', ') are already in use. Stop that process, then run .\start.ps1 again."
}

foreach ($log in @("api.out.log", "api.err.log", "frontend.out.log", "frontend.err.log")) {
    Remove-Item -LiteralPath (Join-Path $runtimeDir $log) -Force -ErrorAction SilentlyContinue
}

Write-Host "Starting NormGraph Workbench. The first frontend compile can take up to two minutes..."
$api = Start-Process -FilePath $python -ArgumentList @("-B", "-m", "backend.public_server") `
    -WorkingDirectory $uiRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runtimeDir "api.out.log") `
    -RedirectStandardError (Join-Path $runtimeDir "api.err.log")
$frontend = Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", "npm run dev") `
    -WorkingDirectory $uiRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runtimeDir "frontend.out.log") `
    -RedirectStandardError (Join-Path $runtimeDir "frontend.err.log")

Set-Content -LiteralPath (Join-Path $runtimeDir "api-parent.pid") -Value $api.Id
Set-Content -LiteralPath (Join-Path $runtimeDir "frontend-parent.pid") -Value $frontend.Id

$ready = $false
for ($attempt = 0; $attempt -lt 90; $attempt++) {
    $apiListener = Get-ListenerProcessId 8765
    $frontendListener = Get-ListenerProcessId 3000
    if ($apiListener) { Set-Content -LiteralPath (Join-Path $runtimeDir "api.pid") -Value $apiListener }
    if ($frontendListener) { Set-Content -LiteralPath (Join-Path $runtimeDir "frontend.pid") -Value $frontendListener }

    $apiReady = Test-LocalUrl "http://127.0.0.1:8765/api/status"
    $uiReady = Test-LocalUrl "http://localhost:3000"
    if ($apiReady -and $uiReady) { $ready = $true; break }
    if (($api.HasExited -and -not $apiReady) -or ($frontend.HasExited -and -not $uiReady)) { break }
    Start-Sleep -Milliseconds 500
}

if (-not $ready) {
    Write-Host "`nAPI log:" -ForegroundColor Yellow
    Get-Content -LiteralPath (Join-Path $runtimeDir "api.err.log") -Tail 20 -ErrorAction SilentlyContinue
    Write-Host "`nFrontend log:" -ForegroundColor Yellow
    Get-Content -LiteralPath (Join-Path $runtimeDir "frontend.err.log") -Tail 30 -ErrorAction SilentlyContinue
    Get-Content -LiteralPath (Join-Path $runtimeDir "frontend.out.log") -Tail 30 -ErrorAction SilentlyContinue
    & (Join-Path $uiRoot "stop.ps1")
    throw "The local app did not become ready. The relevant logs are shown above and kept in .runtime."
}

Write-Host "NormGraph Workbench is ready: http://localhost:3000"
Write-Host "Run .\stop.ps1 when you are finished."
