$ErrorActionPreference = "Stop"
$taskProjectRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw "Run scripts\setup.ps1 first."
}
Push-Location -LiteralPath $taskProjectRoot
try {
    & $taskPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "Dependency check failed." }
    & $taskPython -m pytest --basetemp .runtime/pytest-tmp
    if ($LASTEXITCODE -ne 0) { throw "Tests failed." }
} finally {
    Pop-Location
}
