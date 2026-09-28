# CHECKPOINT.md

Estado exacto del proyecto al finalizar la última sesión y punto exacto desde
el que continuar. Actualizar obligatoriamente antes de terminar cada sesión.

## Última sesión

- **Fecha**: 2026-09-28
- **Estado**: implementación parcial de guardrails y mejora de presentación.
  Regresión ofrece selección temporal automática, rolling OOS, holdout e ITS;
  Causal Impact infiere estacionalidad PRE. En esta sesión se arregló la vista
  de tablas anidadas y la representación de valores no finitos. Falta
  validación end-to-end y build portable.

## Estado exacto

- **Rama/git**: no se detectó repositorio Git en la raíz ni en
  `MedicionAgil_Light`.
- **Cambios relevantes**: fuentes de análisis Causal Impact, GeoX y Regresión;
  diálogos de los tres análisis; pruebas metodológicas; documentación técnica
  en `docs/ANALISIS/` y documentación de continuidad.
- **Cambios anteriores relevantes**: `mmm_app/analyses/regression.py` calcula
  hasta cinco folds expanding con `TimeSeriesSplit` para el modelo elegido. Cada
  fold ajusta el modelo (y el escalador cuando aplica) sólo con su
  entrenamiento, reporta RMSE/MAE/WAPE/sesgo/R² y
  compara con el promedio de su propio prefijo. Conserva el holdout final y
  usa sus métricas para calidad sólo cuando rolling no está disponible. El
  selector de `regression_dialog.py` ahora incluye Evento/ITS.
- **Cambios de esta sesión**: `app_desktop.py` comparte un recolector
  recursivo para tablas visibles/exportables, incluyendo listas y tuplas.
  `causal_impact.py` muestra valores no finitos como `—` y ordena el resumen
  con una clave numérica auxiliar. Se actualizaron los informes de análisis y
  la documentación de continuidad.
- **Pruebas**: `compileall` de todo `mmm_app/` y `tests/` pasó. Smokes aislados
  del recolector de tablas y del resumen Causal Impact con NaN pasaron. El
  smoke OLS rolling devolvió cinco folds y 50 predicciones OOS, con
  RMSE aproximado a cero y menor que su baseline. Este smoke usó un stub
  determinista de `TimeSeriesSplit`; no prueba scikit-learn real. Pytest,
  statsmodels, scikit-learn y Matplotlib no están instalados en el runtime.
  ITS/HAC, suite de pruebas e integración real
  siguen sin validar.
- **Entorno**: Python 3.12.14. Intentar añadir el árbol `_internal` de
  PyInstaller al `PYTHONPATH` no produce un entorno integral: varios módulos
  Python están en el archivo empaquetado, no como archivos importables, y la
  resolución parcial encuentra módulos incompletos.
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
