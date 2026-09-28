$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$python = Join-Path $workspace '.venv\Scripts\python.exe'
$cargo = Join-Path $env:USERPROFILE '.cargo\bin\cargo.exe'

Push-Location $workspace
try {
    & $python -m pytest -q
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    npm run typecheck
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    npm test
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    npm run build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if (Test-Path -LiteralPath $cargo) {
        & $cargo check --manifest-path src-tauri\Cargo.toml
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $cargo test --manifest-path src-tauri\Cargo.toml
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } else {
        throw 'Cargo no está instalado.'
    }
} finally { Pop-Location }
