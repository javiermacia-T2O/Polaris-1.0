# PLAN.md

Plan de trabajo del proyecto. Mantener ordenado por prioridad y actualizado.

## Estado general

En curso: mejora metodológica y de presentación de Causal Impact, GeoX y
Regresión. La UI oculta parámetros técnicos automatizados; siguen pendientes
validación dinámica y capacidades metodológicas descritas a continuación.

## Tareas pendientes

| # | Tarea | Prioridad | Notas |
|---|-------|-----------|-------|
| 1 | Completar la selección PRE y la trazabilidad en Causal Impact | Alta | Backtesting implementado; faltan calidad de controles, contaminación, placebos, sensibilidad y descartes por motivo. |
| 2 | Completar GeoX PRE/POST y serialización del Design | Alta | Reglas de datos diarios y R² estricto implementadas; POST-test y guardar/recargar Design pendientes. |
| 3 | Completar Regresión OOS, selección y modos de objetivo | Alta | Selección automática por rolling OOS, holdout, rolling e ITS implementados; SHAP, tuning y validación con dependencias reales pendientes. |
| 4 | Completar guardrails y calidad en los resultados/UI | Alta | Diagnósticos y tablas de calidad; tablas anidadas corregidas. Falta validación gráfica end-to-end. |
| 5 | Ejecutar pruebas y corregir fallos | Alta | Compilación y smokes aislados pasan; el runtime actual no tiene pytest, Matplotlib, scikit-learn ni statsmodels. |
| 6 | Validar GeoX con dataset Jamaica y flujos end-to-end | Media | Requiere intérprete funcional y dataset real. |

## Tareas en curso

- Validación funcional de análisis y presentación con dependencias reales;
  completar diagnósticos y capacidades pendientes.

## Criterios de siguiente hito

Cambios incrementales compilables, pruebas de no-regresión enfocadas y
descripción explícita de cualquier metodología aproximada o bloqueo.
