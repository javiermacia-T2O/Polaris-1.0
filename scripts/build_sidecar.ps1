param([switch]$Clean)

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$python = Join-Path $workspace '.venv\Scripts\python.exe'
$app = Join-Path $workspace 'MedicionAgil_Light\mmm_app'
$core = Join-Path $workspace 'MedicionAgil_Light\python'
$entry = Join-Path $core 'medicion_sidecar.py'
$dist = Join-Path $workspace 'MedicionAgil_Light\dist'
$work = Join-Path $workspace 'MedicionAgil_Light\build\sidecar'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Falta .venv. Ejecuta scripts\bootstrap.ps1.'
}

$arguments = @(
    '-m', 'PyInstaller', '--noconfirm', '--onedir', '--console',
    '--name', 'medicion-sidecar', '--distpath', $dist, '--workpath', $work,
    '--specpath', (Join-Path $workspace 'MedicionAgil_Light'),
    '--paths', $app, '--paths', $core,
    '--add-data', "$(Join-Path $app 'analyses');analyses",
    '--collect-all', 'meridian_geox', '--collect-all', 'jax',
    '--collect-all', 'jaxkd', '--collect-all', 'jaxlib',
    '--collect-all', 'tslearn', '--hidden-import', 'causalimpact',
    '--hidden-import', 'statsmodels.api', '--hidden-import', 'sklearn.linear_model',
    '--hidden-import', 'sklearn.ensemble', '--hidden-import', 'sklearn.preprocessing',
    $entry
)
if ($Clean) { $arguments = @('-m', 'PyInstaller', '--clean') + $arguments[2..($arguments.Count - 1)] }

& $python @arguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output "Sidecar: $(Join-Path $dist 'medicion-sidecar\medicion-sidecar.exe')"
