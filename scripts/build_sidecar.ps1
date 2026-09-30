param([switch]$Clean)

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$python = Join-Path $workspace '.venv\Scripts\python.exe'
$app = Join-Path $workspace 'MedicionAgil_Light\mmm_app'
$core = Join-Path $workspace 'MedicionAgil_Light\python'
$entry = Join-Path $core 'medicion_sidecar.py'
$dist = Join-Path $workspace 'MedicionAgil_Light\dist'
$work = Join-Path $workspace 'MedicionAgil_Light\build\sidecar'
$spec = Join-Path $workspace 'MedicionAgil_Light'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Falta .venv. Ejecuta scripts\bootstrap.ps1.'
}

# PyInstaller writes its progress to stderr. With $ErrorActionPreference='Stop'
# PowerShell treats that as a NativeCommandError and aborts the build, so the
# call is wrapped with a relaxed preference and the exit code is checked.
$arguments = @(
    '-m', 'PyInstaller', '--noconfirm', '--onedir', '--console',
    '--name', 'medicion-sidecar', '--distpath', $dist, '--workpath', $work,
    '--specpath', $spec,
    '--paths', $app, '--paths', $core,
    '--add-data', "$(Join-Path $app 'analyses');analyses",
    '--collect-all', 'meridian_geox', '--collect-all', 'jax',
    '--collect-all', 'jaxkd', '--collect-all', 'jaxlib',
    '--collect-all', 'tslearn', '--hidden-import', 'causalimpact',
    '--hidden-import', 'statsmodels.api', '--hidden-import', 'sklearn.linear_model',
    '--hidden-import', 'sklearn.ensemble', '--hidden-import', 'sklearn.preprocessing',
    # The sidecar imports the scientific application lazily (so `health` never
    # pays for pandas). PyInstaller cannot see those deferred imports, so they
    # are declared explicitly to keep the frozen bundle complete.
    '--hidden-import', 'medicion_core.application',
    '--hidden-import', 'medicion_core.jobs',
    '--hidden-import', 'medicion_core.schemas',
    '--hidden-import', 'medicion_core.serialization',
    '--hidden-import', 'services.active_dataset',
    '--hidden-import', 'services.analysis_service',
    '--hidden-import', 'services.data_service',
    '--hidden-import', 'services.merge_service',
    '--hidden-import', 'services.table_service',
    '--hidden-import', 'core.plugin_loader',
    '--hidden-import', 'core.engine',
    '--hidden-import', 'core.loader',
    '--hidden-import', 'core.exporter',
    $entry
)
if ($Clean) { $arguments = @('-m', 'PyInstaller', '--clean') + $arguments[2..($arguments.Count - 1)] }

$previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $python @arguments
$code = $LASTEXITCODE
$ErrorActionPreference = $previous
if ($code -ne 0) { exit $code }
Write-Output "Sidecar: $(Join-Path $dist 'medicion-sidecar\medicion-sidecar.exe')"
