# PROGRESS.md

Registro de progreso. Actualizar obligatoriamente antes de terminar cada
sesión. No borrar entradas: lo completado queda como historial.

## Tareas completadas

- **FASE 2 — Arranque coordinado por eventos + splash profesional** (2026-09-30):
  - **Gate FASE 1 (PASO 0)**: sidecar real PyInstaller `--onedir` reconstruido
    y desplegado en `src-tauri/target/debug/sidecar/`. `scripts/gate_fase1.py`
    PASS: `health` nunca bloquea por una operación pesada (p50 4.0 ms), la
    cancelación responde (p50 510 ms), sin respuestas duplicadas (57/0),
    reinicio tras caída y sin huérfanos al cerrar.
  - **Splash independiente**: `public/splash.html` es HTML plano (sin React,
    sin bundler, sin red) que pinta el primer frame desde estilos inline, así
    que no hay destello blanco/azul. Escucha `startup://stage` y
    `startup://failed`; ofrece Reintentar/Detalles/Salir.
  - **Ventana principal oculta**: `main` arranca con `visible: false` y solo se
    muestra cuando motor y frontend están listos.
  - **`StartupCoordinator`** con estados `STARTING`, `SPLASH_READY`,
    `ENGINE_STARTING`, `ENGINE_READY`, `FRONTEND_LOADING`, `FRONTEND_READY`,
    `READY`, `FAILED`. Cada transición la dispara un evento real (efecto React,
    respuesta `health`, carga de ventana); **ningún temporizador** decide la
    disponibilidad.
  - **`READY` idempotente**: `reveal_once()` usa un `AtomicBool` con `swap`, de
    modo que dos señales simultáneas revelan la ventana una sola vez.
  - **Prewarm del sidecar** durante el `setup` de Tauri (no lo dispara un
    `api.health()` de React), en paralelo con la carga del webview.
  - **Watchdog de arranque** con timeout de 45 s y reintento sin duplicar
    procesos (`retry_startup` detiene el sidecar parcial antes de relanzarlo).
  - **Tareas no críticas diferidas**: el frontend señala `frontend_ready` en
    cuanto el shell está montado; análisis, diagnósticos y cachés cargan
    después, con la ventana ya visible.
  - **PyInstaller `--onedir` preservado**; sin regresión de FASE 1.
  - Tests: `cargo test --lib` 20/20 (incluye casos 2–8, 10–14 del plan),
    `src/test/frontendReady.test.ts` 3/3, Vitest total 4/4, `tsc -b` limpio.
  - Arranque release medido: `setup 2450 ms`, `FRONTEND_READY 2835 ms`,
    `ENGINE_READY 7086 ms`, `READY 7121 ms`; sin huérfanos al cerrar.

- **FASE 1 — IPC asíncrono, multiplexado, cancelable y observable** (2026-09-29):
  - `sidecar.py` reescrito: hilo lector, pools `interactive` (2) y
    `background` (1), operaciones de control inline, `_RequestRegistry` con
    cancelación vía DuckDB `interrupt()`, `_Writer` con lock y observabilidad
    por petición (`queue_ms`, `execution_ms`, `total_ms`, `status`,
    `cancelled`, `priority`).
  - `application.py`: `cancel`/`progress` propagados a las operaciones pesadas
    y puente `_bridge_cancel` hacia `JobManager`.
  - `lib.rs`: mapa de pendientes + hilo lector permanente, `route_line()`
    puro, `SidecarManager` con `ensure`/`restart`/`stop`, comando async
    `sidecar_request` con `spawn_blocking`, comando `cancel_request` y evento
    `sidecar://progress`.
  - `api.ts`: `RequestOptions`, `SidecarError`, `cancelRequest`, progreso y
    opciones en las operaciones pesadas (compatible hacia atrás).
  - Tests: `test_sidecar_concurrency.py` (10), 3 tests nuevos de Rust,
    `docs/IPC.md` nuevo y `docs/PROJECT.md` actualizado.
  - Verificación: `cargo test --lib` 5/5; Python 260 pasan, 1 skip, 2 fallos
    preexistentes de Tkinter; `tsc -b` limpio; Vitest 1/1.

- Paridad React/Tauri: se completó la migración de tres capacidades del menú
  Tkinter al frontend React con contratos tipados sobre el sidecar Python.
  - **Unir datasets** (`merge_datasets`): concat (todas/comunes) y merge
    (inner/outer) con claves; nuevo `MergePanel.tsx` en la pantalla Datos.
  - **Mantenimiento** (`get_cache_info`, `clear_cache`, `free_memory`): tamaño
    de caché Parquet, limpieza protegiendo fuentes activas y liberación de
    memoria ligera/completa (reset DuckDB); acciones en `DiagnosticsScreen.tsx`.
  - **Periodo** (`get_date_columns`, `get_date_range`, `apply_date_range`,
    `reset_date_range`): detección de columnas de fecha, granularidad inferida
    y filtrado por rango; nuevo `DateRangePanel.tsx` en la pantalla Datos.
- Se amplió `scripts/e2e_sidecar.py` con pasos de export, merge, caché,
  memoria y rango de fechas. E2E completo pasa: `merge_datasets: e2e-unido 832
  filas`, `get_date_range: 2023-01-02 -> 2024-12-23 D`, `apply_date_range: 416
  filas`, `E2E OK`.
- `tsc -b` y `vite build` pasan; suite Vitest pasa.

- Se corrigió la vista de resultados para recorrer tablas dentro de listas y
  tuplas, igual que la exportación; ambas usan ahora el mismo recolector.
- El resumen ejecutivo de Causal Impact representa métricas, efectos y
  probabilidades no finitos como `—`; el orden por KPI/volumen tolera valores
  de volumen ausentes.
- Se actualizaron los informes de Regresión, Causal Impact y GeoX para reflejar
  selección temporal automática, estacionalidad inferida con PRE y parámetros
  internos que ya no se presentan en el diálogo.
- `compileall` de `mmm_app/` y `tests/` pasó. Smokes aislados del recolector de
  tablas y del resumen causal con NaN pasaron con Python 3.12.14.

- En Regresión, se añadió evaluación rolling/expanding OOS de hasta cinco
  folds cronológicos para el modelo seleccionado, con escalado dentro del
  entrenamiento de cada fold y baseline de promedio PRE por fold. El resultado
  conserva también el holdout final. Se volvió seleccionable en el diálogo el
  modo ITS que el motor ya implementaba.
- Se añadió una prueba para rolling OOS y una aserción de interfaz para ITS;
  compilación sintáctica y smoke OLS determinista pasan. No se pudo ejecutar
  pytest por falta de instalación en los runtimes accesibles.

- Se inventariaron los siete plugins de análisis registrados en
  `mmm_app/analyses/` y se creó un informe Markdown detallado por análisis, más
  un índice en `docs/ANALISIS/README.md` y una portada en `docs/README.md`.
- Se documentaron entradas, parámetros, valores predeterminados, preparación,
  pasos de cálculo, salidas, gráficos y límites según el código.
- Se actualizó `PROJECT.md` con la nueva ubicación de documentación técnica.
- Se revisó que los siete nombres expuestos por los plugins tengan una entrada
  en el índice; esta sesión solo modificó documentación.
- En Causal Impact, los controles y combinaciones ahora se preseleccionan con
  pseudo-cortes expanding PRE (Ridge ligero; deben superar el baseline de
  promedio de entrenamiento; hasta cinco finalistas); el
  ganador se ordena por RMSE/MAE PRE OOS, no por p-value/efecto POST. KPI
  ausentes omiten el KPI, y el resumen informa métricas PRE OOS, backend y
  etiqueta descriptiva de calidad, además de controles descartados y motivo.
- En GeoX, se rechaza KPI ausente, panel no diario, fechas con huecos y panel
  geo-día incompleto; ya no se prorratea, no se filtra el universo por Top-N
  volumen y no se relaja automáticamente `min_r2`. Se pasa explícitamente
  `QualityCheckConfig()` de Meridian GeoX.
- En Regresión, se añadió holdout cronológico OOS (RMSE, MAE, WAPE, sesgo y
  R²), se excluyen filas incompletas en vez de imputar cero y se suprimen las
  contribuciones aditivas falsas de modelos de árbol. Se añadió el modo de
  Evento/ITS segmentada con cambio de nivel/pendiente, HAC/Newey-West, IC y
  efecto acumulado; el diálogo exige un evento/grupo y avisa de límites causales.
- Se añadieron avisos metodológicos breves en los tres diálogos y pruebas
  unitarias nuevas/actualizadas para backtesting, missing vs cero, frecuencia
  GeoX y ausencia del fallback R².
- Se actualizaron informes técnicos de Causal Impact, GeoX y Regresión para
  reflejar cambios y límites que siguen pendientes.
- Se incorporó ITS como modo explícito de Regresión: indicador desde la primera
  fecha marcada, tendencia lineal, salto de nivel, cambio de pendiente,
  covarianza HAC/Newey-West, IC 95 %, efecto por periodo y acumulado. El diálogo
  actualiza su aviso, permite ejecutar ITS sin controles explicativos y exige
  fecha/evento; se añadieron pruebas unitarias de fórmula y guardrails.
- Se identificó el fallo de GeoX: ninguna candidata superaba el R² mínimo
  solicitado de 0,8.
- Se añadió una recuperación controlada en el plugin GeoX: se respeta primero
  el umbral solicitado y, ante el rechazo específico de GeoX, se reintenta una
  vez con `max(0,5, R² solicitado - 0,2)`.
- El resultado recuperado incluye advertencia y registra el R² solicitado y el
  aplicado. Si tampoco hay diseños, devuelve un diagnóstico accionable sin
  traceback esperado.
- Se sincronizó el plugin dentro de la distribución portable existente.
- Se actualizó también la entrada equivalente dentro de
  `dist/MedicionAgil_Light_Portable.zip` y se verificó que coincide por hash
  con la carpeta portable.
- La compilación sintáctica de la fuente y de la prueba nueva terminó
  correctamente.

## Tareas en curso

- Validar gráficos y tablas de los análisis contra dependencias reales y datos
  representativos; el entorno disponible no permite ejecutar esos flujos.
- Implementación global parcial; las capacidades restantes están enumeradas
  en `PLAN.md`, `ISSUES.md` y los informes de análisis.
- El runtime Python 3.12.14 empaquetado en Codex permitió compilar sintácticamente
  los nueve archivos tocados. También pasaron comprobaciones aisladas de
  backtest Causal Impact, holdout OLS, contribuciones de árbol y rechazos
  GeoX de semanal/missing/SDID usando stubs para dependencias UI.
- La suite pytest y las integraciones reales no se ejecutaron: pytest no está
  instalado y las bibliotecas empaquetadas de la app no forman un entorno de
  importación completo. No es validación end-to-end.

## Tareas pendientes

- Ejecutar pruebas metodológicas y de regresión con Python 3.12 funcional.
- Completar controles de contaminación, placebos y sensibilidad Causal Impact.
- Completar GeoX POST-test, guardado/reutilización del `Design` y selección de
  duración/ranking diagnóstico de diseños.
- Completar selección automática de complejidad, tuning temporal y SHAP
  compatible en Regresión; ITS actual aún requiere estacionalidad, eventos
  concurrentes y mayor validación.
- Completar estados de calidad con causas concretas y validación UI end-to-end.
- Reconstruir/verificar la distribución portable y validar con CSV Jamaica.
- Completar POST-test y serialización GeoX, placebos/sensibilidad Causal
  Impact, y diagnósticos ITS/SHAP de Regresión.

## Historial de sesiones

| Fecha | Sesión | Resumen |
|-------|--------|---------|
| 2026-09-28 | Creación del sistema de documentación | Se creó /docs con los 7 archivos de documentación de sesión. Sin otros cambios de código. |
| 2026-09-28 | Corrección de GeoX | Se añadió fallback transparente para el rechazo por R² mínimo; se actualizó la distribución portable, se añadió prueba y se hizo verificación sintáctica. pytest queda pendiente por el entorno Python. |
| 2026-09-28 | Informes técnicos de análisis | Se documentaron los siete análisis encontrados en el cargador de plugins en `docs/ANALISIS/`. Sin cambios de código. |
| 2026-09-28 | Mejoras metodológicas parciales | Causal Impact usa selección PRE OOS; GeoX exige panel diario íntegro, solo TBR y umbral R² estricto; Regresión reporta holdout temporal y no atribuye árboles falsamente. Compilación sintáctica y smoke tests aislados pasaron; pytest, integraciones y funcionalidades restantes pendientes. |
| 2026-09-28 | Regresión rolling OOS / selector ITS | Se añadieron folds expanding OOS y se expuso ITS en el selector. Compilación y smoke OLS determinista pasaron; suite pytest e integración aún no disponibles. |
| 2026-09-28 | Presentación y simplificación de análisis | Se corrigió el renderizado de tablas anidadas y los valores no disponibles de Causal Impact; se actualizaron informes sobre parámetros automatizados. Compilación/smokes aislados pasan; validación gráfica y end-to-end pendiente. |
| 2026-09-29 | Rendimiento, constructor de tablas y UI | Se eliminó el cuello de botella de `get_analysis_manifest` (13,8 s → 139 ms) extrayendo `get_table_format` por AST sin importar los plugins pesados. El constructor de tablas ahora proyecta solo las variables asignadas a fila/columna (antes mostraba todo el dataset). Se añadió una caché persistente del recuento de filas (`.rows.json` junto al Parquet, validada por tamaño/mtime de la fuente): el `COUNT(*)` de 4,3 GB pasa de ~13 s a ~2 ms en cargas posteriores y persiste entre procesos. Además, el primer recuento de un origen >16 MB ya no bloquea: `get_dataset_metadata`/`get_table_page` devuelven un total provisional marcado como aproximado (`rows_approximate`/`total_rows_approximate`) y el recuento exacto se calcula en un job de fondo; el frontend sondea hasta recibir el valor real. Se añadieron breakpoints responsivos (860/620 px), margen del logo y reubicación de mensajes sutiles. Sidecar reconstruido con PyInstaller y desplegado; sonda del sidecar congelado confirma `get_column_values`, la proyección correcta y `metadata rows: 6 approximate: False`. Suite Python 251 pasan / 2 fallos Tk preexistentes; Vitest 1 pasa; `tsc -b` limpio. Verificación final sobre `data test/`: los 4 datasets cargan y construyen tabla; el CSV de 4,3 GB carga en ~580 ms con `rows=0` (conteo diferido) y resuelve al valor exacto (22.303.560) en segundo plano. |
