param(
    [string]$BackendHost = "127.0.0.1",
    [int]$BackendPort = 7860,
    [int]$FrontendPort = 5173,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$BackendUrl = "http://${BackendHost}:${BackendPort}"

if (-not (Test-Path (Join-Path $Root "frontend\node_modules"))) {
    Write-Host "frontend/node_modules not found. Run: npm --prefix frontend install" -ForegroundColor Yellow
}

Write-Host "Starting Qwen3-ASR Web Console" -ForegroundColor Cyan
Write-Host "Backend:  $BackendUrl"
Write-Host "Frontend: http://127.0.0.1:$FrontendPort"
Write-Host "Project .env is loaded by the backend process; secrets are not printed by this script."

$backendCommand = @"
Set-Location '$Root'
& '$Python' -m uvicorn web_app.main:app --host '$BackendHost' --port $BackendPort
"@

$frontendCommand = @"
Set-Location '$Root'
`$env:VITE_API_BASE_URL = '$BackendUrl'
npm --prefix frontend run dev -- --host 127.0.0.1 --port $FrontendPort --strictPort
"@

Start-Process powershell -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $backendCommand) -WorkingDirectory $Root
Start-Process powershell -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $frontendCommand) -WorkingDirectory $Root