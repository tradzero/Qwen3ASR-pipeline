param(
    [string]$BackendHost = "127.0.0.1",
    [int]$BackendPort = 7860,
    [int]$FrontendPort = 5173,
    [string]$CondaEnv = "qwen3-asr",
    [string]$CondaHook = "",
    [string]$Python = "python",
    [ValidateSet("Idle", "BelowNormal", "Normal", "AboveNormal", "High")]
    [string]$BackendPriority = "AboveNormal",
    [int]$ShutdownTimeoutSeconds = 20,
    [switch]$StopOnly,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$StartScript = Join-Path $PSScriptRoot "start-web.ps1"

if (-not (Test-Path $StartScript)) {
    throw "start-web.ps1 not found: $StartScript"
}

function Get-ListeningProcessIds {
    param([int]$Port)

    if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
        return @(
            Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty OwningProcess -Unique
        )
    }

    $pattern = "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$"
    return @(
        netstat -ano -p tcp |
            ForEach-Object {
                if ($_ -match $pattern) {
                    [int]$Matches[1]
                }
            } |
            Select-Object -Unique
    )
}

function Wait-PortRelease {
    param(
        [int]$Port,
        [int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $remaining = @(Get-ListeningProcessIds -Port $Port | Where-Object { $_ -and $_ -ne $PID } | Sort-Object -Unique)
        if (-not $remaining.Count) {
            return @()
        }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)

    return @(Get-ListeningProcessIds -Port $Port | Where-Object { $_ -and $_ -ne $PID } | Sort-Object -Unique)
}

function Stop-ProcessTree {
    param([int]$ProcessId)

    if (Get-Command taskkill.exe -ErrorAction SilentlyContinue) {
        & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
        return
    }

    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Stop-PortListeners {
    param(
        [string]$Label,
        [int]$Port
    )

    $processIds = @(Get-ListeningProcessIds -Port $Port | Where-Object { $_ -and $_ -ne $PID })
    if (-not $processIds.Count) {
        Write-Host ("{0} port {1}: no listener found." -f $Label, $Port)
        return
    }

    foreach ($processId in $processIds) {
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if (-not $process) {
            continue
        }

        $description = "PID $processId ($($process.ProcessName))"
        if ($DryRun) {
            Write-Host ("{0} port {1}: would stop {2}." -f $Label, $Port, $description)
            continue
        }

        Write-Host ("{0} port {1}: stopping {2}." -f $Label, $Port, $description)
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }

    if (-not $DryRun) {
        $remaining = @(Wait-PortRelease -Port $Port -TimeoutSeconds $ShutdownTimeoutSeconds)
        if ($remaining.Count) {
            Write-Host ("{0} port {1}: listener still active after {2}s; trying taskkill /T /F." -f $Label, $Port, $ShutdownTimeoutSeconds) -ForegroundColor Yellow
            foreach ($processId in $remaining) {
                Stop-ProcessTree -ProcessId $processId
            }
            $remaining = @(Wait-PortRelease -Port $Port -TimeoutSeconds $ShutdownTimeoutSeconds)
        }
        if ($remaining.Count) {
            throw ("{0} port {1} is still held by PID(s): {2}" -f $Label, $Port, ($remaining -join ', '))
        }
    }
}

Write-Host "Restarting Qwen3-ASR Web Console" -ForegroundColor Cyan
Write-Host "Project:  $Root"
Write-Host "Backend:  http://${BackendHost}:${BackendPort}"
Write-Host "Frontend: http://127.0.0.1:$FrontendPort"

Stop-PortListeners -Label "Backend" -Port $BackendPort
Stop-PortListeners -Label "Frontend" -Port $FrontendPort

if ($DryRun) {
    Write-Host "Dry run: would invoke start-web.ps1 now."
    return
}

if ($StopOnly) {
    Write-Host "StopOnly: ports are free; start-web.ps1 was not invoked."
    return
}

& $StartScript `
    -BackendHost $BackendHost `
    -BackendPort $BackendPort `
    -FrontendPort $FrontendPort `
    -CondaEnv $CondaEnv `
    -CondaHook $CondaHook `
    -Python $Python `
    -BackendPriority $BackendPriority