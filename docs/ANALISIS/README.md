# Catálogo técnico de análisis

Documentación funcional derivada de las implementaciones actuales de
`MedicionAgil_Light/mmm_app/analyses/`. El código de cada análisis es la fuente
de verdad; actualizar su informe cuando cambien parámetros, cálculos o salidas.

## Informes

| Análisis mostrado en la aplicación | Informe | Implementación |
|---|---|---|
| Estadísticas descriptivas | [01-estadisticas-descriptivas.md](01-estadisticas-descriptivas.md) | `analyses/descriptive.py` |
| Matriz de correlación | [02-matriz-correlacion.md](02-matriz-correlacion.md) | `analyses/correlation.py` |
| Corrplot (gradiente) | [03-corrplot-gradiente.md](03-corrplot-gradiente.md) | `analyses/Análisis de correlación.py` |
| Regresión (atribución de contribución) | [04-regresion.md](04-regresion.md) | `analyses/regression.py`, diálogo `ui/dialogs/regression_dialog.py` |
| Inversión ↔ KPI por Canal | [05-inversion-kpi-canal.md](05-inversion-kpi-canal.md) | `analyses/Análisis Multisoporte.py` |
| Causal Impact (BSTS) | [06-causal-impact.md](06-causal-impact.md) | `analyses/causal_impact.py`, diálogo `ui/dialogs/causal_impact_dialog.py` |
| Diseño de geo-experimento (GeoX) | [07-geox.md](07-geox.md) | `analyses/Geo Test Google (GeoX).py`, diálogo `ui/dialogs/geox_dialog.py` |

## Convenciones de lectura

- “Parámetro” describe la opción que recibe el motor; se indican valores por
  defecto cuando el código los establece.
- “Salida” enumera las tablas, textos y gráficos que produce la función `run`.
- Se distingue el cálculo propio de los cálculos delegados a statsmodels,
  scikit-learn, pycausalimpact o Meridian GeoX.
- Los umbrales de correlación y p-value descritos son filtros del código; no
  implican por sí solos causalidad ni significación estadística cuando no se
  calcula un contraste.
