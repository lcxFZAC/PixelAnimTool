param([int]$Port = 8765, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$workshopRoot = $PSScriptRoot
Set-Location -LiteralPath $workshopRoot
$workshopPort = $Port
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$workshopPort/health" -TimeoutSec 2
    if ($health.app -eq 'pixel-workshop') {
        if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$workshopPort" }
        Write-Host 'Pixel Workshop is already running.'
        exit 0
    }
} catch { }
$pythonExe = Join-Path $workshopRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    $candidate = Get-Command python -ErrorAction SilentlyContinue
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($candidate) {
        & $candidate.Source -c 'import sys; assert sys.version_info >= (3, 10)'
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ is required. Install it from python.org, enable PATH, and try again.' }
        & $candidate.Source -m venv (Join-Path $workshopRoot '.venv')
    } elseif ($launcher) {
        & $launcher.Source -3 -c 'import sys; assert sys.version_info >= (3, 10)'
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ is required.' }
        & $launcher.Source -3 -m venv (Join-Path $workshopRoot '.venv')
    } else {
        throw 'Install Python 3.10+ from https://www.python.org/downloads/windows/ and select Add Python to PATH.'
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the isolated Python environment.' }
}
& $pythonExe (Join-Path $workshopRoot 'check_dependencies.py')
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Preparing Pillow in the project environment (first launch only)...'
    & $pythonExe -m pip install --disable-pip-version-check -r (Join-Path $workshopRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency download failed. Check your connection, then run this launcher again.' }
}
# Run hidden. Logs contain startup information only, never API keys or request bodies.
$serverPath = Join-Path $workshopRoot 'server.py'
Start-Process -FilePath $pythonExe -ArgumentList @('"' + $serverPath + '"', '--port', "$workshopPort") -WorkingDirectory $workshopRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $workshopRoot 'server.log') -RedirectStandardError (Join-Path $workshopRoot 'server-error.log')
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 300
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$workshopPort/health" -TimeoutSec 1
        if ($health.app -eq 'pixel-workshop') {
            if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$workshopPort" }
            Write-Host 'Pixel Workshop is ready. Use Exit service in the page when finished.'
            exit 0
        }
    } catch { }
}
throw 'Startup failed. Check server-error.log; port 8765 may be occupied. No existing process was terminated.'
