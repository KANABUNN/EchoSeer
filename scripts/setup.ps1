$ErrorActionPreference = "Stop"
$taskProjectRoot = Split-Path -Parent $PSScriptRoot
$taskVenv = Join-Path $taskProjectRoot ".venv"
$taskPython = Join-Path $taskVenv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $taskPython)) {
    & py -3.14 -m venv $taskVenv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the Python 3.14 environment." }
}
& $taskPython -c "import sys; assert sys.version_info[:2] == (3, 14) and sys.maxsize > 2**32, 'Python 3.14 x64 is required'"
if ($LASTEXITCODE -ne 0) { throw "This environment must use Python 3.14 x64." }
& $taskPython -m pip install --disable-pip-version-check --cache-dir (Join-Path $taskProjectRoot ".cache\pip") -r (Join-Path $taskProjectRoot "requirements-dev.txt") -r (Join-Path $taskProjectRoot "requirements-build.txt")
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
Write-Output "Ready. Run: .\.venv\Scripts\python.exe main.py"
