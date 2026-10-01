param(
    [Parameter(Mandatory = $true)]
    [string]$Python,
    [string]$PackageName = 'MedicionAgil_Light_Portable',
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$parent = Split-Path -Parent $project
$pyinstaller = Join-Path $parent '.build-deps'
$packages = Join-Path $parent '.venv-dev\Lib\site-packages'
$geoxPackages = Join-Path $parent '.geox-deps'
$app = Join-Path $project 'mmm_app'
$portable = Join-Path $project (Join-Path 'dist' $PackageName)
$zip = Join-Path $project (Join-Path 'dist' "$PackageName.zip")

if (-not (Test-Path -LiteralPath $Python)) { throw "Python no encontrado: $Python" }
if (-not (Test-Path -LiteralPath (Join-Path $pyinstaller 'PyInstaller'))) {
    throw 'Falta PyInstaller en .build-deps del repositorio padre.'
}
if (-not (Test-Path -LiteralPath $packages)) {
    throw 'Faltan las dependencias de ejecución en .venv-dev del repositorio padre.'
}
if (-not (Test-Path -LiteralPath (Join-Path $geoxPackages 'meridian_geox\__init__.py'))) {
    throw 'Falta GeoX en .geox-deps. Instala meridian-geox==1.0.1 en ese directorio antes del build.'
}

$env:PYTHONPATH = "$pyinstaller;$packages;$geoxPackages;$app"
$cleanArgs = if ($Clean) { @('--clean') } else { @() }
& $Python -m PyInstaller --noconfirm @cleanArgs --onedir --windowed `
    --name MedicionAgil `
    --distpath $portable `
    --workpath (Join-Path $project 'build') `
    --specpath $project `
    --paths $app `
    --paths $geoxPackages `
    --add-data "$(Join-Path $app 'analyses');analyses" `
    --collect-all meridian_geox `
    --collect-all jax `
    --collect-all jaxkd `
    --collect-all jaxlib `
    --collect-all tslearn `
    --hidden-import causalimpact `
    --hidden-import statsmodels.api `
    --hidden-import sklearn.linear_model `
    --hidden-import sklearn.ensemble `
    --hidden-import sklearn.preprocessing `
    (Join-Path $app 'app_desktop.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Copy-Item -LiteralPath (Join-Path $project 'README.md') -Destination $portable
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $portable, $zip, [System.IO.Compression.CompressionLevel]::Optimal, $true)
Write-Output "Distribución: $zip"
