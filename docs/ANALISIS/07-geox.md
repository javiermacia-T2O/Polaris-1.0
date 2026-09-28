# Diseño de geo-experimento (GeoX)

## 1. Propósito

Usar Meridian GeoX para proponer asignaciones geográficas de control y
tratamiento que cumplan restricciones de presupuesto, potencia y calidad de
ajuste. Es un análisis de diseño del experimento; no estima el efecto de una
campaña ya ejecutada.

## 2. Parámetros de datos y diseño

| Parámetro | Descripción | Predeterminado |
|---|---|---|
| `date_col` | Fecha del panel | Selección/autodetección |
| `region_col` | Identificador geográfico | Selección/autodetección |
| `kpi_col` | Métrica agregada como `conversions` para GeoX | Selección/autodetección |
| `market_col`, `market_value` | Filtro de mercado opcional | Ninguno/vacío |
| `filters` | Filtros exactos adicionales por columna | Ninguno |
| `duration_days` | Duración del experimento | 14 |
| `budget` | Presupuesto de cada celda | 50.000 |
| `cost_per_incremental_conversion` | Coste usado en el diseño Holdback | 1,0 |
| `experiment_type` | `HOLDBACK`, `GO_DARK`, `HEAVY_UP` | `HOLDBACK` |
| `methodology` | Método de cálculo | Fijo internamente en `TBR`; GeoX 1.0.1 no implementa SDID en `run_design` |
| `geo_assignment_rule` | Regla de asignación | Fija internamente en `STRATIFIED_SAMPLING` |
| `included_control_geos` | Geos fijados en el control | Ninguno |
| `excluded_geos` | Geos excluidos del diseño | Ninguno |
| `max_conversions_percent` | Máxima proporción de conversiones asignable al tratamiento | 0,30 |
| `design_output_count`, `cell_count` | Diseños/celdas solicitados | Fijos internamente: 5 y 1 |
| `alpha`, `power`, `test_type` | Error tipo I, potencia y contraste | Fijos internamente: 0,10; 0,80; `TWO_SIDED` |
| `n_candidates`, `n_ranked_candidates`, `seed` | Búsqueda y reproducibilidad | Fijos internamente: 100.000; 100; 42 |
| `slope_tolerance`, `min_r2` | Tolerancia de pendiente y R² mínimo | Fijos internamente: 0,20; 0,80 |
| `num_strata`, `k_means_iterations` | Estratos e iteraciones internas | Fijos internamente: 4; 10 |

La interfaz personalizada solicita datos, filtros, tipo de experimento,
duración, presupuesto, coste por conversión y proporción máxima de tratamiento.
El motor propone el tratamiento; los parámetros técnicos restantes son
internos. El usuario puede fijar controles conocidos y excluir regiones.

## 3. Preparación paso a paso

1. Aplica filtros adicionales y, si corresponde, filtro de mercado sin
   distinguir mayúsculas/minúsculas.
2. Resuelve columnas ausentes por nombres típicos de fecha, región y KPI.
3. Convierte fechas; elimina fechas inválidas. KPI no numérico y ausente
   generan error separado; los ceros observados se mantienen como cero.
4. Normaliza nombres de regiones y elimina valores vacíos, `nan`, `none`,
   `unknown` y `(not set)`.
5. Agrupa por fecha y región y suma el KPI.
6. Rechaza granularidad no diaria y exige fechas consecutivas normalizadas a
   medianoche; no expande ni prorratea observaciones.
7. Exige que cada geo tenga una observación en cada fecha del panel; rechaza
   huecos en lugar de imputarlos como cero.
8. Exige al menos 7 fechas únicas y al menos 4 regiones con volumen positivo.
9. Incluye todas las regiones positivas salvo exclusiones; conserva controles
   fijados. El volumen no limita el universo.
10. Llama a `meridian_geox.run_design` con `QualityCheckConfig()` explícito y
    restricciones; GeoX ejecuta sus Quality Checks oficiales en esa llamada.

## 4. Recuperación del filtro R²

El código prueba solo el `min_r2` solicitado. Si GeoX no encuentra diseños
que lo superen, devuelve error y explica que un cambio de umbral debe ser
manual. No ejecuta un reintento con umbral relajado.

## 5. Resultados y gráficos

- **Configuración usada:** columnas, presupuesto, experimento, celdas,
  restricciones, R² solicitado/aplicado, candidatos y tamaño del panel.
- **Métricas detectadas:** atributos de los diseños con nombres asociados a
  lift, incrementalidad, coste, MDE, potencia, p-value y otros.
- **Dump de resultados y diseños:** extracción recursiva de atributos
  disponibles en objetos GeoX.
- **Regiones de control y tratamiento** del mejor diseño (`designs[0]`).
- **Tabla por región:** volumen total del KPI y grupo asignado.
- **Asignaciones por diseño:** pertenencia de cada geo a control o tratamiento.
- **Distribución control/tratamiento:** barras horizontales por región,
  coloreadas por grupo.
- **Balance de volumen:** volumen agregado y porcentaje de control y
  tratamiento.
- **Diagnóstico de calidad:** indica panel diario completo y ejecución de los
  Quality Checks oficiales; revisar también las métricas de GeoX.

## 6. Interpretación y límites

- No hay fallback automático de R²; si falla el criterio se detiene el diseño.
- No está implementado el modo POST-test ni guardar/recargar el objeto
  `Design`; GeoX permanece PRE-test en esta iteración.
- La metodología `SDID` está en el enum público, pero `run_design` de la
  versión empaquetada solo implementa `TBR`; la UI ahora ofrece solo TBR y el
  plugin rechaza SDID explícito.
- El universo completo puede aumentar tiempo de cálculo y congestionar los
  gráficos con muchos geos.

## 7. Fuente

`mmm_app/analyses/Geo Test Google (GeoX).py` y
`mmm_app/ui/dialogs/geox_dialog.py`.
