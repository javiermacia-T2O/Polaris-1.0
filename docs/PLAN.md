# PLAN.md

Plan de trabajo del proyecto. Mantener ordenado por prioridad y actualizado.

## Estado general

En curso: migración de la app Tkinter a React + TypeScript + Tauri/Rust con
sidecar científico Python. La paridad funcional avanza por capacidades: en la
última sesión se migraron unir datasets, mantenimiento (caché/memoria) y
selección de periodo (fecha/granularidad). Siguen pendientes la validación
interactiva con `tauri dev`, el build portable y las capacidades metodológicas
de análisis descritas abajo.

## Tareas pendientes

| # | Tarea | Prioridad | Notas |
|---|-------|-----------|-------|
| 1 | Validar `tauri dev` end-to-end | Alta | Cargar dataset, ejecutar análisis, exportar, unir y filtrar por fecha desde la UI real. |
| 2 | Completar la selección PRE y la trazabilidad en Causal Impact | Alta | Backtesting implementado; faltan calidad de controles, contaminación, placebos, sensibilidad y descartes por motivo. |
| 3 | Completar GeoX PRE/POST y serialización del Design | Alta | Reglas de datos diarios y R² estricto implementadas; POST-test y guardar/recargar Design pendientes. |
| 4 | Completar Regresión OOS, selección y modos de objetivo | Alta | Selección automática por rolling OOS, holdout, rolling e ITS implementados; SHAP, tuning y validación con dependencias reales pendientes. |
| 5 | Completar guardrails y calidad en los resultados/UI | Alta | Diagnósticos y tablas de calidad; tablas anidadas corregidas. Falta validación gráfica end-to-end. |
| 6 | Build portable + NSIS y prueba del exe distribuido | Media | Requiere entorno de build y prueba funcional. |
| 7 | Validar GeoX con dataset Jamaica y flujos end-to-end | Media | Requiere intérprete funcional y dataset real. |

## Tareas en curso

- Validación interactiva de la app React/Tauri y build portable; completar
  diagnósticos y capacidades metodológicas pendientes.

## Criterios de siguiente hito

Cambios incrementales compilables, pruebas de no-regresión enfocadas y
descripción explícita de cualquier metodología aproximada o bloqueo.
