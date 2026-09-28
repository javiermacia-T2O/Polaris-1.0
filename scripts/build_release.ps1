param([switch]$Clean)

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$cargo = Join-Path $env:USERPROFILE '.cargo\bin\cargo.exe'

& (Join-Path $PSScriptRoot 'verify.ps1')
& (Join-Path $PSScriptRoot 'build_sidecar.ps1') -Clean:$Clean
Push-Location $workspace
try {
    npm run tauri build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally { Pop-Location }
