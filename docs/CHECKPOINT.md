# CHECKPOINT.md

Estado exacto del proyecto al finalizar la última sesión y punto exacto desde
el que continuar. Actualizar obligatoriamente antes de terminar cada sesión.

## Última sesión

- **Fecha**: 2026-10-01
- **Estado**: correcciones de RAM, sustitución de dataset, conteo y arranque
  completadas; release portable reconstruido y probado. Se preservó el gate
  por señales reales. En esta continuación se añadieron reintentos silenciosos
  de bridge, filtros independientes de roles y columnas inmediatas desde la
  metadata. Después se añadió conteo estimado inmediato, preview de 100 filas
  y caché de valores de filtro sobre el dataset completo. Ver
  `docs/PROGRESS.md`.

## Estado exacto

- **Rama/git**: `main`; el árbol de trabajo ya contenía cambios del usuario y
  permanecen sin revertir.
- **Cambios de esta sesión**:
  - `mmm_app/core/memory_budget.py` y `core/resource_manager.py`: presupuesto
    basado en memoria física disponible, menos margen para el SO. El preflight
    ya no vuelve a restar datasets residentes, porque Windows ya los descuenta
    de `available`.
  - `src/app/Sidebar.tsx` y `src/features/datasets/DatasetsScreen.tsx`:
    "Abrir archivo" reemplaza el activo después de cargar el nuevo; "Añadir"
    conserva el pool. Fallos al cerrar revierten el handle nuevo.
  - `python/medicion_core/sidecar.py`: `queue_ms` y `total_ms` parten de la
    recepción de la línea, no del inicio tardío del worker.
  - `python/medicion_core/application.py`: el conteo diferido informa cola,
    ejecución, total y error sin bloquear la metadata provisional.
  - `src-tauri/tauri.conf.json` y `src-tauri/src/lib.rs`: splash visible desde
    el arranque; reveal fullscreen; eliminado IPC `warmup` no soportado;
    hitos de inicio persistidos en `%TEMP%\polaris-startup-<pid>.log`.
  - `public/splash.js`: hasta 12 intentos con backoff para listener/bridge
    antes de mostrar error; los intentos son invisibles durante el progreso.
  - `src/features/tables/TableBuilder.tsx`: Fila/Columna/Valor se asignan por
    separado del filtro; un campo puede ser columna y filtro simultáneamente.
  - `src/features/datasets/DatasetsScreen.tsx`: columnas/tipos se derivan de
    metadata, quitando la petición `get_columns` redundante del primer render.
  - `mmm_app/services/active_dataset.py`: `row_count_estimate` (muestreo de
    bloques para CSV grandes) y total exacto de Parquet desde el footer.
  - `python/medicion_core/application.py`: memoiza la estimación por versión,
    cachea valores de filtro por `(dataset, versión, columna, límite)` con LRU
    de 128 y propaga `progress` a preview, valores y build.
  - `mmm_app/services/table_service.py`: `_PREVIEW_SOURCE_LIMIT = 100`,
    `preview_limit` en `compile_table` y `progress` en `preview_to_pandas`.
  - `src/app/KpiCards.tsx`, `src/app/Sidebar.tsx`,
    `src/features/datasets/DatasetsScreen.tsx`,
    `src/features/datasets/DataPreview.tsx`: muestran `~N filas · exacto en
    curso` mientras el conteo exacto sigue en marcha.
  - `src/features/tables/TableBuilder.tsx` y `src/shared/api.ts`: preview a 100
    filas con `onProgress`, progreso en valores de filtro y en Aplicar.
  - `src/styles.css`: botones de rol en columnas fijas de 52 px.
- **Pruebas**: 73 tests backend afectados; 12 tests Vitest; `npm run typecheck`;
  `npm run build`; sidecar congelado `scripts/smoke_sidecar.py` -> `SMOKE_OK`.
- **Datos reales**: CSV 4,55 GB, 22.303.560 filas. Primera apertura lazy
  responde en 266 ms; el conteo exacto termina en 15,29 s de escaneo / 15,97 s
  desde la petición y queda persistido por fingerprint. La caché original se
  mantuvo intacta durante la medición fría con hard link temporal.
- **Arranque portable**: splash page 1,71 s; setup 8,89 s; frontend 9,64 s;
  engine y `READY` 20,77 s. Main verificada visible en fullscreen 1280×800.
  Catálogo 246–387 ms. Cierre de la instancia de prueba terminó el host y
  sidecar.
- **Artefacto**: `release/Polaris-0.1.0.zip`, 268,6 MB (reconstruido con el
  sidecar y el host actualizados en esta sesión).
- **Paquete anterior**: `release/Polaris-Actualizado-0.1.0.zip`, 319,7 MB.
- **Heap JS**: `npm run build` pasó con heap Node predeterminado y bundle de
  329 KB (96,6 KB gzip). El error de worker JS no se reprodujo; los listados y
  tablas transfieren páginas acotadas. Si reaparece, hace falta el stack/error
  exacto de WebView2 para localizar el worker emisor.

## Continuar desde

1. No quedan bloqueos funcionales identificados en el flujo medido.
2. Reducir el tiempo entre `setup` (8,89 s) y `ENGINE_READY` (20,77 s) si se
   necesita bajar más el TTI; los datos ahora permiten separar cada hito.
3. Si reaparece el error de heap JS en uso real, recopilar el mensaje y stack
   del WebView2; no ocurrió en build ni en el smoke portable actual.

Los informes técnicos están indexados en `docs/README.md` y
`docs/ANALISIS/README.md`. La fuente de verdad sobre comportamiento sigue
siendo el código ejecutable.
