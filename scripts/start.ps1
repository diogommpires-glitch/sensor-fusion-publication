$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv/Scripts/python.exe"
$workspace = Join-Path $projectRoot "workspace"
$viewerUrl = "http://127.0.0.1:8766"
$launchLock = [System.Threading.Mutex]::new($false, "Local\SensorFusionLabDesktopLauncher")
$ownsLock = $false
$server = $null

function Get-LabHealth {
    try {
        Invoke-RestMethod -Uri "$viewerUrl/api/health" -TimeoutSec 1 -UseBasicParsing
    } catch [System.Net.WebException] {
        if ($_.Exception.Response) {
            throw "Port 8766 is occupied by a service that failed the Sensor Fusion health check."
        }
        return $null
    }
}

try {
    try {
        $ownsLock = $launchLock.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        $ownsLock = $true
    }
    if (-not $ownsLock) { return }
    if (-not (Test-Path -LiteralPath $python)) {
        throw "Python environment not found. Run scripts/setup.ps1 in $projectRoot first."
    }

    $health = Get-LabHealth
    if ($null -eq $health) {
        $logDirectory = Join-Path $workspace "logs"
        New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
        $env:PYTHONUTF8 = "1"
        $server = Start-Process -FilePath $python -ArgumentList @(
            "-m", "sensorfusion", "--workspace", "`"$workspace`"", "serve"
        ) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $logDirectory "desktop-server.out.log") `
            -RedirectStandardError (Join-Path $logDirectory "desktop-server.err.log")
        $deadline = [DateTime]::UtcNow.AddSeconds(30)
        while ($null -eq $health -and [DateTime]::UtcNow -lt $deadline) {
            if ($server.HasExited) {
                throw "Server exited during startup. See $logDirectory/desktop-server.err.log."
            }
            Start-Sleep -Milliseconds 200
            $health = Get-LabHealth
        }
        if ($null -eq $health) {
            throw "Server did not become ready within 30 seconds. See $logDirectory/desktop-server.err.log."
        }
    }
    if ($health.status -ne "ok" -or [string]$health.workspace -ne $workspace) {
        throw "Port 8766 belongs to a different service or workspace. Close that service before starting this lab."
    }
    Start-Process -FilePath $viewerUrl
} catch {
    if ($null -ne $server -and -not $server.HasExited) { $server.Kill() }
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show($_.Exception.Message, "Sensor Fusion could not start") | Out-Null
    exit 1
} finally {
    if ($ownsLock) { $launchLock.ReleaseMutex() }
    $launchLock.Dispose()
}
