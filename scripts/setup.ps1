param([string]$PythonExecutable = "")
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:PYTHONUTF8 = "1"
if (-not $PythonExecutable) {
    $bundledPython = Join-Path $env:USERPROFILE ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe"
    if (Test-Path -LiteralPath $bundledPython) {
        $PythonExecutable = $bundledPython
    } else {
        $PythonExecutable = (Get-Command python -ErrorAction Stop).Source
    }
}
& $PythonExecutable -c "import sys; assert sys.version_info[:2] == (3,12), 'Python 3.12 is required'"
if ($LASTEXITCODE -ne 0) { throw "Supply a Python 3.12 interpreter with -PythonExecutable" }
New-Item -ItemType Directory -Path "tmp/pip" -Force | Out-Null
$env:TEMP = (Resolve-Path -LiteralPath "tmp/pip").Path
$env:TMP = $env:TEMP
if (-not (Test-Path -LiteralPath ".venv/Scripts/python.exe")) {
    & $PythonExecutable -m venv --without-pip .venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the project environment" }
}
& $PythonExecutable -m pip --python ".venv/Scripts/python.exe" install -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw "Locked dependency installation failed" }
& $PythonExecutable -m pip --python ".venv/Scripts/python.exe" install --no-deps --no-build-isolation -e .
if ($LASTEXITCODE -ne 0) { throw "Project installation failed" }
$npmExecutable = (Get-Command npm.cmd -ErrorAction Stop).Source
& $npmExecutable ci --prefix frontend
if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed" }
& ".venv/Scripts/python.exe" scripts/build_frontend.py
if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }
Write-Output "Setup complete. Run Start-Lab.cmd or .venv/Scripts/python.exe -m sensorfusion serve."
