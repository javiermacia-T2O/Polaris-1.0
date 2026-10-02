# Distribución portable de Polaris

Este documento describe cómo generar el paquete que el usuario final solo tiene
que descomprimir y ejecutar, **sin instalar nada** (ni Python, ni dependencias,
ni servicios, ni modificar el `PATH`).

## Generar el paquete

```powershell
.\scripts\build_portable_release.ps1
```

Parámetros:

| Parámetro      | Descripción                                                        |
| -------------- | ------------------------------------------------------------------ |
| `-PackageName` | Nombre de la carpeta y del ZIP. Por defecto `Polaris`.             |
| `-Version`     | Versión del ZIP. Por defecto se lee de `src-tauri\tauri.conf.json`. |
| `-SkipSidecar` | Reutiliza el sidecar ya construido en `MedicionAgil_Light\dist`.   |
| `-SkipHost`    | Reutiliza el host ya construido en `src-tauri\target\release`.     |
| `-Clean`       | Limpia los directorios de trabajo de PyInstaller antes de construir. |

Salida: `release\Polaris-<versión>.zip` (~269 MB).

## Contenido del paquete

```
Polaris\
├── Polaris.exe            Host Tauri (ventana + IPC).
├── medicion-sidecar.exe   Motor Python congelado (PyInstaller onedir).
├── _internal\             Runtime de Python y dependencias del sidecar.
└── LEEME.txt              Instrucciones para el usuario final.
```

El host resuelve el sidecar como **hermano del ejecutable**
(`src-tauri\src\lib.rs`, `sidecar_command()`), por lo que ambos `.exe` deben
quedar en la misma carpeta. `_internal` debe quedar junto a
`medicion-sidecar.exe` (layout onedir de PyInstaller).

## Pasos del script

1. **Sidecar** — PyInstaller `--onedir` sobre `python\medicion_sidecar.py`.
   - `--exclude-module polars`: import opcional muerto (`_POLARS_OK` nunca se
     consulta) que arrastraba ~168 MB de `_polars_runtime_32`.
   - `--collect-all meridian_geox/jax/jaxkd/jaxlib/tslearn` y `--hidden-import`
     para el stack científico importado de forma diferida.
2. **Host** — `npm run tauri build -- --no-bundle` (solo el ejecutable, sin
   instalador NSIS).
3. **Ensamblado** — copia los binarios a `release\Polaris\` y genera
  `LEEME.txt`. El usuario inicia la app directamente con `Polaris.exe`, sin
  pasar por un script de consola.
4. **ZIP** — comprime la carpeta con `System.IO.Compression.ZipFile`.

## Verificación

Prueba de humo del sidecar congelado (arranque + stack científico):

```powershell
.\.venv\Scripts\python.exe scripts\smoke_sidecar.py release\Polaris\medicion-sidecar.exe
```

Salida esperada:

```
health: {'status': 'ok', 'protocol': 1}
list_analyses: 7
load_dataset: ...
SMOKE_OK
```

> `load_dataset` puede omitirse con `MEMORY_BUDGET_ERROR` si el equipo tiene
> poca RAM libre (el sidecar reserva memoria para el sistema). Es un aviso
> ambiental, no un fallo del paquete: `health` y `list_analyses` ya prueban que
> el bundle arranca y carga todo el stack científico.

Prueba de la GUI completa:

```powershell
Start-Process release\Polaris\Polaris.exe
```

El log `release\Polaris\logs\sidecar.jsonl` debe mostrar `health`,
`list_datasets` y `list_analyses` con `"status": "ok"`.

## Notas

- `release/` está en `.gitignore`: el ZIP y la carpeta portable no se versionan.
- El sidecar crea `logs\` y `output\` junto a su ejecutable en tiempo de
  ejecución; no forman parte del paquete.
- Los scripts `.ps1` con acentos deben guardarse como **UTF-8 con BOM** para que
  PowerShell 5.1 los lea correctamente.