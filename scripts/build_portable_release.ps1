#Requires -Version 5.1
<#
.SYNOPSIS
    Construye el ZIP portable de Polaris listo para el usuario final.

.DESCRIPTION
    Genera una carpeta autocontenida que el usuario final solo tiene que
    descomprimir y ejecutar: no requiere instalar Python, dependencias,
    servicios ni modificar el PATH.

    Contenido del paquete (lo único e indispensable):
        Polaris.exe            Host Tauri (ventana + IPC).
        medicion-sidecar.exe   Motor Python congelado (PyInstaller onedir).
        _internal\             Runtime de Python y dependencias del sidecar.
        LEEME.txt              Instrucciones para el usuario final.

    El host resuelve el sidecar como hermano del ejecutable, por lo que ambos
    .exe deben quedar en la misma carpeta; `_internal` debe quedar junto a
    `medicion-sidecar.exe` (layout onedir de PyInstaller).

.PARAMETER PackageName
    Nombre de la carpeta y del ZIP generados. Por defecto `Polaris`.

.PARAMETER Version
    Versión incluida en el nombre del ZIP. Por defecto se lee de
    `src-tauri\tauri.conf.json`.

.PARAMETER SkipSidecar
    Reutiliza el sidecar ya construido en `src-tauri\sidecar-source\dist`.

.PARAMETER SkipHost
    Reutiliza el ejecutable Tauri ya construido en `src-tauri\target\release`.

.PARAMETER Clean
    Limpia los directorios de trabajo de PyInstaller antes de construir.
#>
param(
    [string]$PackageName = 'Polaris',
    [string]$Version,
    [switch]$SkipSidecar,
    [switch]$SkipHost,
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'

$workspace = Split-Path -Parent $PSScriptRoot
$python = Join-Path $workspace '.venv\Scripts\python.exe'
$sidecarRoot = Join-Path $workspace 'src-tauri\sidecar-source'
$app = Join-Path $sidecarRoot 'mmm_app'
$core = Join-Path $sidecarRoot 'python'
$entry = Join-Path $core 'medicion_sidecar.py'
$sidecarDist = Join-Path $sidecarRoot 'dist'
$sidecarWork = Join-Path $sidecarRoot 'build\sidecar-release'
$sidecarSpec = Join-Path $sidecarRoot 'build\sidecar-release-spec'
$hostExe = Join-Path $workspace 'src-tauri\target\release\medicion-agil.exe'
$releaseRoot = Join-Path $workspace 'release'
$portable = Join-Path $releaseRoot $PackageName
$releaseRootFull = [System.IO.Path]::GetFullPath($releaseRoot).TrimEnd('\')
$portableFull = [System.IO.Path]::GetFullPath($portable)
if (-not $portableFull.StartsWith($releaseRootFull + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'La carpeta portable debe permanecer dentro de release.'
}

if (-not $Version) {
    $conf = Get-Content -LiteralPath (Join-Path $workspace 'src-tauri\tauri.conf.json') -Raw |
        ConvertFrom-Json
    $Version = $conf.version
}
$zip = Join-Path $releaseRoot "$PackageName-$Version.zip"

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Falta .venv. Ejecuta scripts\bootstrap.ps1.'
}

# ---------------------------------------------------------------------------
# 1. Sidecar congelado (PyInstaller onedir)
# ---------------------------------------------------------------------------
if (-not $SkipSidecar) {
    Write-Output '[1/4] Construyendo el sidecar (PyInstaller)...'
    New-Item -ItemType Directory -Force -Path $sidecarSpec | Out-Null

    # `polars` es un import opcional que el motor nunca usa (`_POLARS_OK` no se
    # consulta) y arrastra ~168 MB de `_polars_runtime_32`. Se excluye para
    # reducir el paquete. El resto de exclusiones son herramientas de
    # desarrollo que no forman parte del runtime del sidecar.
    $arguments = @(
        '-m', 'PyInstaller', '--noconfirm', '--onedir', '--console',
        '--name', 'medicion-sidecar',
        '--distpath', $sidecarDist,
        '--workpath', $sidecarWork,
        '--specpath', $sidecarSpec,
        '--paths', $app, '--paths', $core,
        '--add-data', "$(Join-Path $app 'analyses');analyses",
        '--collect-all', 'meridian_geox', '--collect-all', 'jax',
        '--collect-all', 'jaxkd', '--collect-all', 'jaxlib',
        '--collect-all', 'tslearn',
        '--hidden-import', 'causalimpact',
        '--hidden-import', 'statsmodels.api',
        '--hidden-import', 'sklearn.linear_model',
        '--hidden-import', 'sklearn.ensemble',
        '--hidden-import', 'sklearn.preprocessing',
        # El sidecar importa la aplicación científica de forma diferida (para
        # que `health` no pague pandas). PyInstaller no ve esos imports, así
        # que se declaran explícitamente para que el bundle quede completo.
        '--hidden-import', 'medicion_core.application',
        '--hidden-import', 'medicion_core.jobs',
        '--hidden-import', 'medicion_core.schemas',
        '--hidden-import', 'medicion_core.serialization',
        '--hidden-import', 'services.active_dataset',
        '--hidden-import', 'services.analysis_service',
        '--hidden-import', 'services.data_service',
        '--hidden-import', 'services.merge_service',
        '--hidden-import', 'services.table_service',
        '--hidden-import', 'ui.figure_utils',
        '--hidden-import', 'core.plugin_loader',
        '--hidden-import', 'core.engine',
        '--hidden-import', 'core.loader',
        '--hidden-import', 'core.exporter',
        '--exclude-module', 'polars',
        '--exclude-module', 'pytest',
        '--exclude-module', 'IPython',
        '--exclude-module', 'jupyter',
        '--exclude-module', 'notebook',
        $entry
    )
    if ($Clean) { $arguments = @('-m', 'PyInstaller', '--clean') + $arguments[2..($arguments.Count - 1)] }

    # PyInstaller escribe el progreso en stderr; con ErrorActionPreference=Stop
    # PowerShell lo trata como error y aborta, así que se relaja y se comprueba
    # el código de salida.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $python @arguments
    $code = $LASTEXITCODE
    $ErrorActionPreference = $previous
    if ($code -ne 0) { exit $code }
} else {
    Write-Output '[1/4] Sidecar: se reutiliza el existente.'
}

$sidecarExe = Join-Path $sidecarDist 'medicion-sidecar\medicion-sidecar.exe'
$sidecarInternal = Join-Path $sidecarDist 'medicion-sidecar\_internal'
if (-not (Test-Path -LiteralPath $sidecarExe)) { throw "No se encontró el sidecar: $sidecarExe" }
if (-not (Test-Path -LiteralPath $sidecarInternal)) { throw "No se encontró _internal: $sidecarInternal" }

# ---------------------------------------------------------------------------
# 2. Host Tauri (sin instalador NSIS: solo el ejecutable)
# ---------------------------------------------------------------------------
if (-not $SkipHost) {
    Write-Output '[2/4] Construyendo el host Tauri (release)...'
    Push-Location $workspace
    # `npm`/`tauri` escriben información en stderr; con ErrorActionPreference=Stop
    # PowerShell lo trata como error terminante y aborta, así que se relaja y se
    # comprueba el código de salida.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        npm run tauri build -- --no-bundle
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
        Pop-Location
    }
    if ($code -ne 0) { exit $code }
} else {
    Write-Output '[2/4] Host: se reutiliza el existente.'
}
if (-not (Test-Path -LiteralPath $hostExe)) { throw "No se encontró el host: $hostExe" }

# ---------------------------------------------------------------------------
# 3. Ensamblado de la carpeta portable
# ---------------------------------------------------------------------------
Write-Output '[3/4] Ensamblando la carpeta portable...'
if (Test-Path -LiteralPath $portable) { Remove-Item -LiteralPath $portable -Recurse -Force }
New-Item -ItemType Directory -Force -Path $portable | Out-Null

Copy-Item -LiteralPath $hostExe -Destination (Join-Path $portable "$PackageName.exe")
Copy-Item -LiteralPath $sidecarExe -Destination $portable
Copy-Item -LiteralPath $sidecarInternal -Destination $portable -Recurse

$readme = @"
$PackageName $Version
=====================

Aplicación de escritorio local para cargar CSV, Excel y Parquet, explorar y
modelar datos, unir datasets, ejecutar análisis y exportar resultados.

CÓMO EJECUTAR
-------------
1. Extrae TODO el contenido de este ZIP en una carpeta (por ejemplo, en el
   Escritorio). No ejecutes la aplicación desde dentro del ZIP.
2. Haz doble clic en "$PackageName.exe".

No hace falta instalar nada: ni Python, ni dependencias, ni servicios, ni
modificar el PATH. Todo lo necesario va incluido en esta carpeta.

IMPORTANTE
----------
- Mantén juntos "$PackageName.exe", "medicion-sidecar.exe" y la carpeta
  "_internal". Si se separan, la aplicación no arrancará.
- La primera apertura puede tardar unos segundos mientras Windows comprueba
  los archivos extraídos. No se instala nada al iniciar.
- Al guardar resultados, elige una carpeta y escribe un nombre para el análisis.
  Polaris creará carpetas por fecha, nombre, tipo de análisis y resultado.
- Si Windows SmartScreen muestra un aviso la primera vez, elige "Más
  información" y "Ejecutar de todas formas".
"@
Set-Content -LiteralPath (Join-Path $portable 'LEEME.txt') -Value $readme -Encoding UTF8

# ---------------------------------------------------------------------------
# 4. ZIP
# ---------------------------------------------------------------------------
Write-Output '[4/4] Comprimiendo el ZIP...'
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $portable, $zip, [System.IO.Compression.CompressionLevel]::Optimal, $true)

$sizeMb = [math]::Round((Get-Item -LiteralPath $zip).Length / 1MB, 1)
Write-Output "Distribución: $zip ($sizeMb MB)"
