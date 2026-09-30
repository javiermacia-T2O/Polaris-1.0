# PHASE3_EXECUTION.md

Documento único de ejecución de la FASE 3 (navegación y operación de CSV/Parquet
de 5 GB+ sin materializar el origen completo en Pandas). Se actualiza al cerrar
cada bloque. No duplicar informes.

## Base

- Repositorio: https://github.com/javiermacia-T2O/Polaris-1.0.git
- Base auditada: `5d1b2a6` (Fases 1/2 terminadas).
- Rama de trabajo: `fase3-lazy-navigation`.
- Sin commit/push/reset en este encargo.
- Entorno: Python 3.12.14 (`.venv`), Node v24.21.0, Rust 1.98.1, Windows.

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
| 3. Dataset, caché y navegación | NOT STARTED |
| 4. Tablas, pivot y uniones | NOT STARTED |
| 5. Análisis, resultados y exportación | NOT STARTED |
| 6. Validación y entrega | NOT STARTED |

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

## Siguiente acción exacta

Bloque 3: dataset, caché y navegación (`core/loader.py`, `core/atomic.py`,
`core/diagnostics.py`).