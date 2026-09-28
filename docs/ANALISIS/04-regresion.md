# Regresión (atribución de contribución)

## 1. Propósito

Ajustar un modelo para predecir un KPI con variables numéricas explicativas,
calcular métricas e intervalos de coeficientes y construir una descomposición
de predicción por variable.

## 2. Parámetros de entrada

| Parámetro | Descripción | Requisito / valor |
|---|---|---|
| `regression_type` | Algoritmo seleccionado | Automático (selección temporal OOS) |
| `date_col` | Fecha para ordenar y representar el eje temporal | Detectada automáticamente si falta |
| `target_col` | KPI objetivo `y` | Columna existente, numérica tras conversión, no constante |
| `input_cols` | Predictores `X` | Lista no vacía de columnas existentes |
| `anomaly_cols` | Indicadores temporales/eventos preparados por el diálogo | Se agregan a los predictores si existen |

El diálogo permite elegir Automático, OLS, Evento/ITS segmentada, Ridge, Lasso, Elastic Net, Bayesian Ridge,
Huber, Random Forest o Gradient Boosting. Los valores internos de
regularización y de árboles se fijan en el código; no son controles de la
interfaz. El diálogo también permite marcar eventos sobre el gráfico
temporal; antes del ajuste, el servicio prepara indicadores de esos eventos y
se entregan como `anomaly_cols` al plugin.

### Selección automática

1. Compara OLS, Ridge, Elastic Net, Huber, Random Forest y Gradient Boosting
   con los mismos folds temporales expanding; escalador y modelo se ajustan
   únicamente con el entrenamiento de cada fold.
2. Descarta candidatos sin validación válida o que no mejoren el RMSE del
   baseline de promedio PRE.
3. De los candidatos restantes, elige el más simple dentro del 2 % del mejor
   RMSE OOS. Si ninguno supera el baseline, devuelve un error explícito.
4. Ajusta el ganador con todos los datos para predicciones y gráficos
   descriptivos; la tabla comparativa conserva las métricas OOS de selección.

## 3. Preparación de datos

1. Valida objetivo y predictores.
2. Si la fecha existe, convierte a datetime, elimina fechas inválidas y ordena
   cronológicamente.
3. Convierte objetivo y predictores a numérico (`errors="coerce"`).
4. Convierte infinitos en ausentes y elimina las filas incompletas del
   objetivo o de cualquier predictor. No interpreta ausencias como cero.
5. Requiere al menos 10 observaciones utilizables y desviación de `y` mayor
   que `1e-12`.
6. Construye `X` y `y` con los predictores seleccionados más las columnas de
   anomalía válidas.

## 4. Ajuste e intervalos

### Evento / ITS segmentada

1. Requiere fecha, exactamente un evento/grupo marcado, al menos 8 filas PRE,
   4 POST y 10 utilizables en total. Las variables `input_cols` se usan como
   controles; los flags de evento no se agregan como controles.
2. Toma el primer periodo marcado como intervención (`T0`). Define
   `post_t = 1(t >= T0)` y `time_after_t = max(0, t - T0)`.
3. Ajusta OLS:

   `Y_t = β0 + β1·t + β2·post_t + β3·time_after_t + γ·X_t + ε_t`

4. Calcula covarianza HAC/Newey-West con `maxlags = min(floor(sqrt(n)), n-1)`.
   Informa salto de nivel `β2`, cambio de pendiente `β3` y sus IC 95 %; el
   efecto por periodo es `β2 + β3·time_after_t`.
5. El efecto acumulado suma esos efectos POST. Sus IC se derivan de la matriz
   de covarianza HAC de (`β2`, `β3`), incluidas sus covarianzas entre periodos.
6. Devuelve tabla de estimaciones, serie por fecha y gráfico de efecto por
   periodo/acumulado. No genera tabla de “contribuciones” para ITS.

El control requiere un solo evento/grupo. Si se marcan eventos con fechas de
inicio diferentes, el diálogo agrupa la información solo si pertenecen a la
misma serie agregada; interpretar el resultado como cambio asociado a la
primera fecha marcada.

### OLS

- Usa `statsmodels.OLS(y, add_constant(X)).fit()`.
- Devuelve coeficientes, error estándar, estadístico t y p-value.
- Calcula IC del 95 %, 90 % y 85 % con `model.conf_int(alpha=1-nivel)`.
- Predicciones: valores ajustados in-sample del propio modelo.

### Modelos scikit-learn

1. Estandariza predictores con `StandardScaler`.
2. Ajusta el estimador puntual con todos los datos.
3. Calcula predicciones in-sample para diagnóstico; la capacidad predictiva se
   informa con rolling/expanding OOS y un holdout cronológico final.
4. Genera remuestras bootstrap de tamaño `n`, con reemplazo, usando semillas
   reproducibles derivadas de la semilla fija 42.
5. Ajusta cada modelo bootstrap en un máximo de dos hilos; usa ejecución
   secuencial si falla el paralelismo.
6. Calcula los IC como cuantiles percentiles 2,5/97,5; 5/95 y 7,5/92,5.
   Error estándar bootstrap: desviación estándar muestral de los coeficientes.

| Modelo | Configuración fija | Iteraciones bootstrap |
|---|---|---:|
| Ridge | `alpha=1.0` | 500 |
| Lasso | `alpha=0.01`, `max_iter=10000` | 500 |
| Elastic Net | `alpha=0.01`, `l1_ratio=0.5`, `max_iter=10000` | 500 |
| Bayesian Ridge | configuración sklearn por defecto | 300 |
| Huber | configuración sklearn por defecto | 500 |
| Random Forest | 300 árboles, semilla bootstrap, `n_jobs=1` | 100 |
| Gradient Boosting | 300 estimadores, semilla bootstrap | 100 |

En modelos lineales con `coef_`, transforma coeficientes desde escala
estandarizada a unidades originales. En árboles usa `feature_importances_`.
Para modelos bootstrap no se calcula p-value: la tabla muestra `—`.
En Random Forest y Gradient Boosting, el valor llamado `Coeficiente` en la
tabla y el forest plot es una importancia de variable, no una pendiente en las
unidades del KPI.

## 5. Contribuciones propias

### Modelos con coeficientes lineales

Para cada fila `t` y variable `i`:

`Contribución_i(t) = coeficiente_i × X_i(t)`

`Baseline(t) = intercepto`

Por construcción, `baseline + suma(contribuciones) = predicción`.

### Random Forest y Gradient Boosting

La aplicación no obtiene contribuciones SHAP ni contribuciones exactas de
árbol. Por ello omite gráfico y columnas de contribución para árboles; el
forest plot conserva `feature_importances_`, como importancia y no contribución.

### Resumen de contribución

- **Total:** suma con signo.
- **Absoluta:** suma de valores absolutos.
- **Media:** promedio por observación.
- **% KPI total:** contribución absoluta dividida por la suma de `abs(y_pred)`.
- Orden descendente por contribución absoluta.

## 6. Métricas reportadas

**Métricas principales OOS:** RMSE, MAE, WAPE, sesgo y R² de hasta cinco
folds expanding generados con `TimeSeriesSplit`, sin barajar. El mínimo de
entrenamiento es `max(10, número de predictores + 3)`; si no caben al menos dos
folds, la validación rolling se informa como no disponible. En modelos
escalados, cada fold ajusta el escalador sólo con su prefijo de entrenamiento;
todos los modelos se ajustan sin observar su bloque de prueba. El resultado agrega
predicciones OOS y compara RMSE con el promedio del entrenamiento de cada
fold. Se conserva además un holdout final cronológico 80/20 independiente,
con métricas OOS y comparación con el promedio PRE. La calidad toma rolling
cuando está disponible y usa el holdout si no lo está.

**Diagnóstico in-sample:** R², R² ajustado, RMSE, MAE, MAPE, sMAPE, desviación estándar residual, AIC,
BIC y Durbin–Watson. MAPE omite objetivos de valor absoluto menor que `1e-9`;
sMAPE omite denominadores menores que `1e-9`. AIC/BIC usan `k = número de
predictores + 1`; Durbin–Watson usa la suma de diferencias residuales
cuadráticas dividida por la suma residual cuadrática.

## 7. Gráficos

1. **Real vs Predicho:** series real y ajustada por fecha/índice, banda
   `predicción ± |residuo|` y métricas principales.
2. **Contribución apilada:** baseline y contribuciones en cascada; línea del
   KPI real. En modelos lineales, el borde de la pila reproduce la predicción.
3. **Forest plot:** coeficientes y bandas IC 95/90/85, con línea vertical en 0.
4. **Diagnóstico de residuos:** residuos vs tiempo, residuos vs predicción,
   QQ plot normal y histograma.

## 8. Salidas tabulares

- Métricas del modelo.
- Resumen de contribución.
- Tabla de coeficientes, errores estándar, p-values disponibles, IC y marcas
  de significación (`*** <0,001`, `** <0,01`, `* <0,05`, `. <0,10`).
- Dataset aumentado con `Predicho_<target>`, `Residuo_<target>`,
  `Contrib · <variable>` y `Contrib · Baseline`.
- Resumen statsmodels solo para OLS; nota de bootstrap para modelos sklearn.

## 9. Límites de interpretación

- La validación rolling y el holdout evalúan el modelo seleccionado por el
  usuario; todavía no existe selección automática de complejidad ni tuning
  temporal de hiperparámetros. No se aplica `gap` porque la interfaz no define
  horizonte de predicción ni una ventana de solapamiento del target.
- Las métricas rolling reúnen predicciones de folds de distintos orígenes; no
  incluyen intervalos de predicción OOS.
- La nota de salida dice “500 iteraciones” para cualquier modelo bootstrap,
  aunque el número efectivo sea 300 en Bayesian Ridge y 100 en los dos modelos
  de árboles; consultar la tabla de este informe para el valor por modelo.
- Las filas con predictores ausentes se excluyen; esto reduce muestra si la
  ausencia no es aleatoria.
- Asociación del modelo no prueba causalidad. La lectura de coeficientes
  depende de especificación, colinealidad y escala.
- ITS utiliza una única intervención y tendencia lineal; todavía no incluye
  estacionalidad explícita, múltiples cambios, placebo o modelado de
  intervenciones concurrentes. El efecto asociado no prueba causalidad.
- El holdout OOS corresponde al modo predictivo; para ITS se marca “No aplica”.
- Los árboles solo reportan importancias, sin atribución aditiva de predicciones.

## 10. Fuente

`mmm_app/analyses/regression.py` y `mmm_app/ui/dialogs/regression_dialog.py`.
