# ISSUES.md

Problemas encontrados, errores conocidos, sus causas y estado actual.
Revisar antes de investigar un problema nuevo. No borrar entradas: cerrarlas
cambiando su estado.

## Problemas abiertos

| ID | Descripción | Causa conocida | Estado | Referencias |
|----|-------------|----------------|--------|-------------|
| GEOX-001 | GeoX no encuentra diseños que superen `min_r2=0.8` en ciertos datasets | El dataset no produce candidatas sobre el umbral | Diagnóstico controlado; no se fuerza ni relaja el diseño; Jamaica pendiente | `mmm_app/analyses/Geo Test Google (GeoX).py` |
| GEOX-002 | No existe análisis POST-test ni reutilización de `Design` | El flujo actual solo ejecuta `run_design`; no persiste las asignaciones | Pendiente de implementación | `mmm_app/analyses/Geo Test Google (GeoX).py`, `ui/dialogs/geox_dialog.py` |
| GEOX-003 | GeoX ofrece métodos que el motor instalado no ejecuta | `meridian_geox` 1.0.1 enumera SDID pero `design.py` solo implementa TBR | Mitigado en fuente; build/runtime pendientes | `mmm_app/analyses/Geo Test Google (GeoX).py`, `ui/dialogs/geox_dialog.py` |
| CI-001 | Calidad/contaminación de controles Causal Impact no se diagnostica completamente | El pipeline actual cubre datos numéricos, screening y backtest PRE, pero no spillover, breaks, placebos o sensibilidad | Parcial | `mmm_app/analyses/causal_impact.py` |
| REG-001 | Diagnóstico de ITS aún incompleto | ITS de una intervención HAC implementada; faltan estacionalidad, múltiples eventos/placebos y SHAP | Parcial | `mmm_app/analyses/regression.py`, `mmm_app/ui/dialogs/regression_dialog.py` |
| TEST-001 | No se ejecutó la suite pytest ni las integraciones reales de esta sesión | Python 3.12.14 disponible; faltan pytest, Matplotlib, scikit-learn y statsmodels | Abierto; compilación y smokes limitados comprobados | `tests/test_causal_impact_features.py`, `tests/test_geox_dialog.py`, `tests/test_regression_thread_events.py` |

## Problemas resueltos

| ID | Descripción | Solución | Fecha |
|----|-------------|----------|-------|
| GEOX-001 | Ninguna candidata alcanzaba el umbral R² solicitado | Primer cambio devolvía recuperación vía umbral menor; la nueva solicitud reemplazó esa metodología. Ahora se intenta solo el umbral pedido y se devuelve diagnóstico si falla; no se fuerza un diseño | 2026-09-28 |
| UI-001 | El panel omitía DataFrames dentro de listas/tuplas | Vista y exportación comparten ahora un recolector recursivo de tablas | 2026-09-28 |
| CI-002 | El resumen ejecutivo podía mostrar `NaN` y ordenar volúmenes ausentes de forma inestable | Valores no finitos se muestran como `—`; se usa una clave auxiliar numérica para ordenar | 2026-09-28 |
