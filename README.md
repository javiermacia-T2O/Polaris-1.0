# Polaris

Aplicación local de Marketing Science para cargar, preparar y analizar datasets
CSV, Excel y Parquet, incluidos archivos masivos que permanecen en
DuckDB/Arrow y se recorren por páginas.

## Generar el ZIP en Windows

Desde PowerShell, en la carpeta descargada con GitHub Desktop:

```powershell
.\scripts\bootstrap.ps1
.\scripts\build_portable_release.ps1 -Clean
```

El archivo final queda en `release\Polaris-0.1.0.zip`. Extráelo completo y
ejecuta `Polaris.exe`.

No es necesario instalar Python ni dependencias. Polaris y su motor local se
inician como procesos gráficos, sin abrir una ventana de comandos.

También se puede lanzar manualmente el workflow
`.github/workflows/build-portable.yml`; el repositorio fuente no almacena el
binario de cientos de megabytes.

La documentación técnica del empaquetado está en [docs/RELEASE.md](docs/RELEASE.md).
