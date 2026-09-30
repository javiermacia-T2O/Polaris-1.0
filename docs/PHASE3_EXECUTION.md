# PHASE3_EXECUTION.md

Documento único de ejecución de la FASE 3 (navegación y operación de CSV/Parquet
de 5 GB+ sin materializar el origen completo en Pandas). Se actualiza al cerrar
cada bloque. No duplicar informes.

## Base

- Repositorio: https://github.com/javiermacia-T2O/Polaris-1.0.git
- Base auditada: `5d1b2a6` (Fases 1/2 terminadas).
- Rama integrada: `codex/fase3-complete` (base `4140069`).
- Entornos usados: Windows para la línea base/benchmark y Linux para la
  reconstrucción final de frontend. El smoke Windows final se mantiene como
  validación pendiente, no se declara como PASS.

## Línea base de tests (antes de editar)

Comando:
```
.venv\Scripts\python.exe -m pytest -q --no-header --basetemp=".pytest_tmp" -p no:cacheprovider
```

Resultado: **269 passed, 1 failed, 1 skipped** (64.93 s).

- Fallo preexistente: `test_light_geox_layout.py::test_geox_primary_action_calls_accept_and_keeps_result`
  (`_tkinter.TclError: invalid command name ".!geoxdialog.!notebook"`; entorno Tk).
- Skip preexistente: `test_light_geox_layout.py:59` (Tk no disponible).
- Nota de entorno: el directorio `%TEMP%\pytest-of-javier.macia` tiene una ACL
  rota (WinError 5). Se usa `--basetemp=".pytest_tmp"` para evitarlo. No es un
  fallo del código.

## Bloques

| Bloque | Estado |
|--------|--------|
| 1. Línea base y contratos | DONE |
| 2. Recursos, conexiones y cancelación | DONE |
| 3. Dataset, caché y navegación | DONE |
| 4. Tablas, pivot y uniones | DONE |
| 5. Análisis, resultados y exportación | DONE |
| 6. Validación y entrega | PARTIAL (smoke Windows pendiente) |

## Bloque 2 — Recursos, conexiones y cancelación (DONE)

### Qué se implementó

- `core/resource_manager.py` (nuevo): gestor único de recursos con reservas
  atómicas, espera cancelable y liberación garantizada en `finally`.
  - `ResourceProfile` por RAM total: <12 GiB → light/heavy 192/256 MiB, 1 hilo,
    techo sidecar 1.25 GiB; 12–<24 GiB → 256/384 MiB, 2 hilos, techo 2 GiB;
    >=24 GiB → 384/768 MiB, 4 hilos, techo 4 GiB. Reserva del SO =
    `max(2 GiB, 25% RAM)`.
  - Admisión por memoria disponible real: `max(0, min(techo sidecar − RSS
    sidecar, RAM disponible − reserva SO) − reservas adicionales no
    consumidas)`. No se cuenta dos veces la memoria residente.
  - Máximo 2 consultas activas (1 pesada). Operación imposible → error
    accionable; operación temporalmente ocupada → espera cancelable.
  - `ResourceCancelled` hereda de `TaskCancelled` para que las rutas
    existentes la traten como cancelación cooperativa.
- `ConnectionPool` (en `resource_manager.py`): hasta 2 conexiones DuckDB
  reutilizables e independientes, con propietario exclusivo durante la
  consulta y el consumo del reader. Préstamo reentrante por hilo; reclama
  préstamos de hilos ya terminados; cierra conexiones inactivas bajo presión;
  sin cursores compartidos.
- `core/engine.py`: sustituye el `threading.local` de conexión por el pool.
  `get_conn()` presta del pool y aplica vistas; `release_conn()` suelta el
  préstamo por completo; `reset_conn()` cierra y retira la conexión (adaptador
  legado); `interrupt_thread(ident)` interrumpe solo la operación activa de la
  conexión de ese hilo.
- `python/medicion_core/sidecar.py`: `_PendingRequest` vincula petición, hilo y
  conexión; `cancel()` resuelve por identidad de hilo (sigue la conexión
  recreada) y recurre a `conn.interrupt()`; `_run_one()` libera la conexión en
  `finally`. Las operaciones de control (`health`, `cancel`, estado de jobs) no
  tocan el pool: responden aunque todas las conexiones estén ocupadas.
- `services/active_dataset.py`: eliminado el override local
  `SET memory_limit='512MB'` de `row_count()`; las operaciones pesadas
  (conversión, agregación completa, orden completo, exportación,
  materialización científica) pasan por el gestor.
- `services/table_result_cache.py`: la materialización usa el gestor en lugar
  de un límite local que eludía la admisión.
- `python/medicion_core/application.py`: `load_dataset` cuenta los bytes
  residentes (se eliminó `retained_bytes=0`).
- `core/memory_budget.py`: perfiles por RAM y ruta lazy forzada para CSV/Parquet
  >=256 MiB.
- `tests/test_resource_manager.py` (nuevo): 28 pruebas enfocadas (admisión
  concurrente, error/cancelación sin fugas de reserva, espera cancelable,
  conexión recreada, cancelación tardía que no afecta al siguiente trabajo,
  `health` disponible bajo carga).
- `tests/conftest.py`: fixture autouse que libera el préstamo de conexión del
  hilo de test tras cada prueba, para que ningún test contamine al siguiente.

### Verificación

- Suite completa: **287 passed, 2 failed, 1 skipped** (46.92 s).
- Los 2 fallos son de `test_light_geox_layout.py` (geometría de diálogos Tk) y
  fallan también en aislamiento, sin tocar el motor ni el gestor de recursos:
  son ambientales, no regresiones de este bloque.
- `tests/test_resource_manager.py` en aislamiento: 28 passed.

## Bloques 3–5 — implementación final

- Navegación: total desconocido es `null`, `has_more` se obtiene con una fila
  interna adicional; máximo público 1000. CSV/Parquet conservan orden físico
  con identidad oculta y el sort solicitado añade desempate estable.
- Filtros: búsqueda DISTINCT server-side, tipada, 100 valores por página,
  cursor ligado a versión/columna/búsqueda, debounce de 250 ms y cancelación
  real por `AbortSignal`. Nulo, cadena vacía y texto `(vacío)` no colisionan.
- Caché: ubicación escribible de usuario (`POLARIS_CACHE_DIR` o caché del SO),
  separación reusable/resultados/tmp/sesión, cuota y reserva de disco, LRU/TTL
  de generaciones no referenciadas y limpieza conservadora de sesiones
  huérfanas. CSV >=256 MiB sigue disponible directamente y programa una única
  conversión Parquet tras dos consultas correctas.
- Constructor: la tabla agregada se materializa una vez a Parquet antes de
  comunicar éxito. Pivots usan categorías como tuplas tipadas y condiciones
  parametrizadas null-safe; límite 50 categorías/500 columnas en rutas lazy y
  Pandas.
- Uniones: concat lazy mediante `UNION ALL [BY NAME]`; joins lazy null-safe con
  preflight exacto y materialización Parquet. Se conservan dependencias de
  todas las fuentes y duplicados.
- Recursos: jobs quedan vinculados al dataset y a la conexión activa; cierre y
  cancelación interrumpen SQL, liberan préstamos y el historial terminal queda
  acotado. Datasets/resultados Pandas se contabilizan como memoria residente.
- UX: Datos y Constructor son una sola pestaña. El tipo se edita en la cabecera
  de cada columna. Se eliminó el panel de pivotado duplicado. El rol Filtro ya
  no se deselecciona al estar vacío; la confirmación de aplicar/cargar solo se
  muestra después de invalidar estado y cargar el primer preview real.

## Validación

### Antes de la reconstrucción del entorno

- Python: **295 passed, 6 skipped** (los skips corresponden a Tk no disponible).
- Dataset real CSV: **5,369,374,621 bytes**, **75,870,000 filas**, 30 columnas.
- Parquet equivalente: **515,235,414 bytes**.

| Escenario | CSV | Parquet |
|---|---:|---:|
| Primera página (mediana) | 0.066 s | 0.025 s |
| Página profunda (mediana) | 0.082 s | 0.021 s |
| Filtro acotado | 0.090 s | — |
| DISTINCT completo | 21.36 s | 6.05 s |
| Sort completo | 23.58 s | 6.05 s |
| Pico observado | 420,676 KiB | 243,580 KiB |

La navegación directa CSV tuvo un pico específico de 228,652 KiB. Los tiempos
de operaciones completas se separan de navegación acotada; no se presenta el
cache del SO como cold garantizado.

### Reconstrucción final

- `npm run typecheck`: PASS.
- `npm test -- --run`: **5 passed** (incluye regresión Fila→Filtro).
- `npm run build`: PASS, 158 módulos.
- `python -m py_compile` sobre el delta: PASS.
- Suite Python final y Cargo: pendientes en este runner porque el reinicio del
  workspace eliminó el ejecutable de `.venv` y dejó truncado el binario nativo
  de DuckDB; la red restringida impidió reconstruir `jaxlib`. Esto es un
  bloqueo de validación del entorno, no se marca como PASS.
- Smoke release Windows/PyInstaller: pendiente (runner Linux sin Rust ni
  ejecutable Windows).

Scripts reproducibles añadidos: `scripts/generate_phase3_data.py` y
`scripts/benchmark_phase3.py`.

## Estado y siguiente acción exacta

Código de Bloques 1–5: **DONE**. Validación Bloque 6: **PARTIAL** hasta ejecutar
en Windows:

```powershell
.venv\Scripts\python.exe -m pytest -q --no-header --basetemp=.pytest_tmp -p no:cacheprovider
npm run typecheck; npm test -- --run; npm run build
cargo test --manifest-path src-tauri/Cargo.toml
powershell -ExecutionPolicy Bypass -File scripts/build_sidecar.ps1
python scripts/gate_fase1.py
```
