# ATTEMPTS.md

Soluciones ya probadas, funcionaran o no, para evitar repetir intentos.
Revisar antes de investigar un problema. No borrar entradas aunque fallen.

## Formato de entrada

**Fecha - Problema**: referencia al problema en ISSUES.md o descripción.
**Intento**: qué se probó exactamente.
**Resultado**: OK funcionó / FALLO falló / PARCIAL parcial.
**Notas**: por qué funcionó o falló, y qué descarta el resultado.

## Intentos registrados

### 2026-09-28 - Presentación de resultados anidados y valores ausentes

**Intento**: compartir un recolector recursivo de DataFrames entre la vista y
la exportación; normalizar valores no finitos del resumen Causal Impact y
ordenar volúmenes mediante una clave numérica temporal.

**Resultado**: OK en compilación y smokes aislados. La vista recoge ahora
tablas dentro de listas/tuplas; los valores ausentes del resumen se muestran
como `—`.

**Notas**: no valida Tk/Matplotlib ni exportación real. Faltan dependencias y
pytest en el runtime disponible.

### 2026-09-28 - GEOX-001

**Intento**: revisar el traceback y el código del plugin. Se confirmó que la
excepción la lanza `meridian_geox` cuando ninguna candidata supera
`min_r2=0.8`; no es un error de importación ni de carga del CSV.

**Resultado**: OK como diagnóstico.

**Notas**: el filtro estadístico funciona como fue diseñado, pero la interfaz
solo devolvía un error genérico.

### 2026-09-28 - GEOX-001

**Intento**: añadir un segundo intento automático solo para esa excepción, con
`max(0.5, min_r2 - 0.2)`, conservando el umbral original en la salida y
mostrando una advertencia cuando se relaja.

**Resultado**: PARCIAL.

**Notas**: la fuente y el plugin de la distribución portable están
actualizados y la sintaxis pasa. Falta ejecutar la prueba dinámica con Python
3.12 y confirmar el comportamiento con el CSV real.

### 2026-09-28 - Mejora metodológica CI/GeoX/Regresión

**Intento**: implementar holdout PRE/OOS para selección de controles Causal
Impact, validación temporal final y exclusión de contribuciones falsas de
árboles en Regresión, y validaciones diarias estrictas más rechazo de missing
y fallback R² en GeoX. Se añadió después el modo ITS segmentado HAC.

**Resultado**: PARCIAL; `py_compile` pasó y smoke checks aislados de
backtesting, OOS, contribuciones de árbol y guardrails GeoX pasaron. ITS solo
pasó compilación sintáctica. Pytest y los flujos end-to-end no se ejecutaron.

**Notas**: el Python 3.12.14 de Codex se pudo usar, pero pytest no está
instalado y las dependencias no forman un entorno integral; los smoke checks
usaron stubs para módulos UI. GeoX Jamaica y end-to-end permanecen sin
comprobar. No repetir la hipótesis de
que Causal Impact selecciona por POST: el ganador ahora se ordena por métricas
de backtesting PRE. Falta cubrir contaminación y otras sensibilidades.

### 2026-09-28 - Regresión rolling OOS y acceso a ITS en UI

**Intento**: exponer el modo ITS que ya soportaban el motor y el preflight,
añadir métricas rolling expanding mediante `TimeSeriesSplit` al resultado de
Regresión y probar el cálculo OLS con serie determinista lineal.

**Resultado**: PARCIAL. `py_compile` pasa y el smoke OLS con splits
cronológicos y baseline por fold devolvió cinco folds y RMSE cercano a cero,
inferior al baseline. No se pudo ejecutar pytest ni la prueba real con
scikit-learn porque el runtime Python accesible no tiene esas dependencias.

**Notas**: la distribución portable incluye módulos compilados, pero su árbol
de archivos omite módulos Python guardados dentro del archivo PyInstaller;
añadir su carpeta al `PYTHONPATH` no constituye un entorno de ejecución
válido. Se verificó la lógica numérica con un stub determinista de
`TimeSeriesSplit`; integración UI/ITS pendiente.
