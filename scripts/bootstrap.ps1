$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$runtimePython = 'C:\Users\javier.macia\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$venvPython = Join-Path $workspace '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $venvPython)) {
    if (-not (Test-Path -LiteralPath $runtimePython)) {
        throw 'Se necesita Python 3.12 para crear .venv.'
    }
    & $runtimePython -m venv (Join-Path $workspace '.venv')
}
$env:UV_CACHE_DIR = Join-Path $workspace '.uv-cache'
& (Join-Path $workspace '.venv\Scripts\python.exe') -m pip install 'uv==0.12.19'
& (Join-Path $workspace '.venv\Scripts\uv.exe') sync --all-groups
Push-Location $workspace
try { npm install } finally { Pop-Location }
