# CHECKPOINT.md

Estado exacto del proyecto al finalizar la última sesión y punto exacto desde
el que continuar. Actualizar obligatoriamente antes de terminar cada sesión.

## Última sesión

- **Fecha**: 2026-09-28
- **Estado**: paridad React/Tauri ampliada. Se migraron tres capacidades del
  menú Tkinter al frontend React sobre el sidecar Python: unir datasets,
  mantenimiento (caché/memoria) y selección de periodo (fecha/granularidad).
  E2E del sidecar, `tsc -b`, `vite build` y Vitest pasan. Falta validación
  interactiva con `tauri dev` y build portable.

## Estado exacto

- **Rama/git**: no se detectó repositorio Git en la raíz ni en
  `MedicionAgil_Light`.
- **Cambios de esta sesión**:
  - `python/medicion_core/application.py`: nuevos métodos `merge_datasets`,
    `get_cache_info`, `clear_cache`, `free_memory`, `get_date_columns`,
    `get_date_range`, `apply_date_range`, `reset_date_range`.
  - `python/medicion_core/sidecar.py`: registro de las ocho operaciones.
  - `src/shared/api.ts`: `mergeDatasets`, `cacheInfo`, `clearCache`,
    `freeMemory`, `dateColumns`, `dateRange`, `applyDateRange`,
    `resetDateRange`.
  - `src/features/datasets/MergePanel.tsx` y `DateRangePanel.tsx` (nuevos);
    `DatasetsScreen.tsx` los integra.
  - `src/features/diagnostics/DiagnosticsScreen.tsx`: panel de mantenimiento.
  - `scripts/e2e_sidecar.py`: pasos de export, merge, caché, memoria y fechas.
- **Cambios anteriores relevantes**: `mmm_app/analyses/regression.py` calcula
  hasta cinco folds expanding con `TimeSeriesSplit` para el modelo elegido. Cada
  fold ajusta el modelo (y el escalador cuando aplica) sólo con su
  entrenamiento, reporta RMSE/MAE/WAPE/sesgo/R² y
  compara con el promedio de su propio prefijo. Conserva el holdout final y
  usa sus métricas para calidad sólo cuando rolling no está disponible. El
  selector de `regression_dialog.py` ahora incluye Evento/ITS.
- **Pruebas**: E2E del sidecar pasa completo (`merge_datasets: e2e-unido 832
  filas`, `get_date_range: 2023-01-02 -> 2024-12-23 D`, `apply_date_range: 416
  filas`, `E2E OK`). `tsc -b` y `vite build` pasan; Vitest pasa (1 archivo).
  El sidecar se reconstruyó con PyInstaller y se desplegó en
  `src-tauri/target/debug/sidecar/`.
- **Entorno**: Python 3.12.14 en `.venv`; Node v24.21.0; Rust 1.98.1.
- **Portable**: no se reconstruyó ni sincronizó en esta sesión. Requiere
  entorno de build y prueba funcional.

## Continuar desde

1. Conseguir un entorno Python 3.12 completo del proyecto con pytest,
   Matplotlib, scikit-learn y statsmodels (pandas y NumPy disponibles);
   ejecutar la suite dirigida y la suite general, incluyendo ITS/HAC y
   rolling CV real.
2. Corregir fallos de esas pruebas y validar el modo ITS desde la UI.
3. Continuar `PLAN.md`: tuning temporal y SHAP en Regresión; controles
   contaminados/placebos/sensibilidad en Causal Impact;
   POST-test y guardar/recargar `Design` en GeoX.
4. Validar GeoX con el CSV Jamaica y reconstruir/probar la distribución
   portable.

Los informes técnicos están indexados en `docs/README.md` y
`docs/ANALISIS/README.md`. La fuente de verdad sobre comportamiento sigue
siendo el código ejecutable.
