# Causal Impact (BSTS)

## 1. Propósito

Estimar qué habría ocurrido con un KPI objetivo durante un periodo de campaña
si su trayectoria hubiera continuado según el periodo previo y series de
control externas. Ejecuta un análisis por combinación de KPI y target.

## 2. Datos y parámetros

| Parámetro | Función | Predeterminado del motor |
|---|---|---:|
| `date_col` | Fecha; autodetección entre columnas datetime si falta | Detectado |
| `kpi_cols` | Uno o varios KPI objetivo | Compatible con `kpi_col` legado |
| `dim_cols` | Dimensiones cuya combinación forma series objetivo/control | Ninguna |
| `target_tasks` | Grupos de valores de dimensión que definen targets | Si falta, una tarea por valor de la primera dimensión |
| `controles_excluidos` | Columnas o tareas que no pueden ser controles | Vacío; los targets se excluyen automáticamente |
| `sesgos_cols` | Covariables externas agregadas por periodo | Ninguna |
| `fecha_campana` | Inicio del periodo de intervención | Obligatoria |
| `fecha_fin_datos` | Fin del periodo post incluido | Obligatoria |
| `granularidad` | `semanal`, `mensual` o diaria | Semanal |
| `umbral_correlacion` | Señal ligera para ordenar shortlist; no es un filtro duro ni prueba de elegibilidad | 0,5 |
| `min_controles`, `max_controles` | Tamaño de combinaciones de control | 1 y 3 en el motor; el diálogo muestra 2 y 3 |
| `top_n_controles` | Máximo de controles candidatos ordenados por correlación | 10 en el motor; diálogo 8 |
| `max_combinaciones` | Parámetro legado aceptado por compatibilidad; ya no gobierna la búsqueda | El motor calcula hasta cinco finalistas PRE |
| `alpha` | Nivel usado por intervalos/modelos | 0,05 |
| `prior_level_sd` | Prior de variación del nivel para pycausalimpact | 0,01 en motor; diálogo 0,05 |
| Estacionalidad | Inferida solo con PRE; se activa con ≥3 ciclos y ≥15 % de varianza explicada tras retirar tendencia | Automática; `nseasons` y `season_duration` no aparecen en la interfaz |
| `dynamic_regression` | Solicita regresión dinámica al backend pycausalimpact | Falso en el motor; activado internamente por el diálogo |
| `standardize_data` | Estandariza controles/covariables en fallback statsmodels | Falso en el motor; activado internamente por el diálogo |

El diálogo aporta valores propios y permite seleccionar series, excluir
controles, agregar covariables de sesgo y definir grupos de targets. Las fechas
del selector se envían como fecha de inicio post y fecha final post.
Los parámetros de estacionalidad y opciones técnicas del backend se fijan
internamente; el usuario conserva el control de `alpha`.

## 3. Preparación de series

1. Valida al menos un KPI y ambas fechas de campaña.
2. Convierte fecha a datetime y elimina filas sin fecha.
3. Convierte covariables seleccionadas a numérico.
4. Por KPI, convierte a numérico; si hay valores ausentes/no convertibles,
   omite ese KPI y registra el conteo en el log. No los convierte a cero.
5. Agrega datos a una matriz ancha por periodo y combinación de dimensiones:
   `sum(KPI)`.
6. Semanal: inicio de periodo `W-SUN`; mensual: inicio de mes; diaria: fecha
   normalizada. Las celdas ausentes de la matriz se rellenan con 0.
7. Mapea las fechas de campaña al primer periodo disponible en o después del
   inicio indicado y al último periodo en o antes del final.
8. Una tarea target puede incluir varios valores de dimensión; la serie
   objetivo es la suma de las columnas que coinciden.
9. Requiere al menos 15 periodos PRE y 1 periodo POST; además, el KPI PRE no
   puede ser constante.

## 4. Selección de controles y modelos

1. Excluye de controles todos los targets definidos y las exclusiones
   explícitas.
2. Calcula Pearson PRE para informar y Spearman PRE como señal auxiliar. La
   correlación por sí sola no admite ni selecciona controles.
3. Para candidatos válidos estima hasta tres pseudo-cortes expanding dentro
   del PRE. Estandariza según cada prefijo, ajusta Ridge ligero (`lambda=0,001`)
   y calcula RMSE, MAE, WAPE, sesgo y R² OOS. Rechaza un candidato cuyo RMSE
   no mejore frente al promedio del prefijo de entrenamiento.
4. Penaliza débilmente inestabilidad rolling y Spearman bajo; limita a 12
   controles y evalúa combinaciones mediante el mismo backtesting PRE.
5. Envía como máximo cinco finalistas al motor. Ordena el ganador por RMSE
   PRE OOS y desempata por MAE PRE OOS; p-values, Inc_Prob y magnitud POST
   solo se informan, nunca intervienen en la selección.
6. Suma controles finalistas y covariables de sesgo como regresores.
7. Motor por orden: `pycausalimpact`; si falla, `statsmodels.UnobservedComponents`
   con tendencia lineal local; si también falla/no está, OLS PRE con IC
   normales.
8. Descarta modelos fallidos. No exige un p-value POST mínimo.
9. Congela el candidato de menor error PRE OOS que pudo ajustarse en el POST.

## 5. Efecto reportado

- **Absoluto:** suma del efecto observado menos predicho durante POST.
- **Relativo:** efecto absoluto como porcentaje de la suma predicha POST
  (según el backend, se extrae el resumen del motor o se calcula localmente).
- **P_Valor:** valor del backend; en fallback statsmodels se deriva de una
  normal con el error estándar acumulado.
- **Inc_Prob:** probabilidad/indicador de signo favorable derivado por el
  backend o la aproximación local.
- **R_Pre:** media de las correlaciones PRE de los controles ganadores.
- **Backtest PRE OOS:** RMSE, MAE, WAPE, sesgo, R² y número de pseudo-cortes,
  mejora frente al baseline promedio y número de pseudo-cortes, visibles en el
  resumen ejecutivo.
- **Controles descartados:** nombre y motivo (target tratado, exclusión,
  constante/no finito, backtest insuficiente o fuera de shortlist) en el
  resumen ejecutivo.
- **Backend:** motor real (`pycausalimpact`, `statsmodels` u OLS aproximado).

Si se usa `UnobservedComponents`, el modelo se ajusta solo en PRE con nivel de
tendencia lineal local, regresores exógenos y componente irregular; pronostica
POST y obtiene bandas con `alpha`. El fallback OLS ajusta PRE, predice PRE y
POST y usa error residual PRE con banda aproximada ±1,96 errores estándar.

## 6. Gráficos y salidas

Por cada combinación KPI–target que termine correctamente:

1. **CI clásico (tres paneles):** observado y contrafactual con banda en el
   panel superior; efecto puntual y referencia cero en el panel central; efecto
   acumulado en el panel inferior. Los tres marcan PRE/POST y la intervención;
   las fechas aparecen en el panel inferior.
2. **CI Serie:** observado y contrafactual con puntos observados, intervalo,
   sombreado POST y fecha de intervención.
3. **CI Efecto puntual:** efecto por periodo, puntos, referencia cero,
   intervalo y marca POST.
4. **CI Efecto acumulado:** `cumsum(effect)` desde el comienzo de la serie,
   mostrado desde POST. La varianza acumulada se aproxima sumando varianzas
   por periodo, sin covarianzas.
5. **Boxplot:** distribución de efectos relativos de las combinaciones de
   controles. Color/opacidad codifica p-value y el gráfico resume recuentos
   por cortes `p≤0,01`, `p≤0,05`, `p≤0,10` y `p≤0,20`.

También devuelve resumen ejecutivo y matriz ancha usada para cada combinación
válida. Si no produce ningún target con resultados, devuelve error global.

## 7. Límites y lectura correcta

- La inferencia causal depende de que los controles reproduzcan el
  contrafactual y no reciban el tratamiento. La correlación PRE por sí sola no
  garantiza esas condiciones.
- La selección favorece correlaciones positivas y modelos con `p <= 0,10`;
  por tanto, los resultados dependen de estos filtros y de la búsqueda de
  combinaciones.
- Un KPI con nulos/no convertibles se omite. La matriz aún rellena con cero
  combinaciones fecha-dimensión sin fila; el origen debe distinguir ausencia
  estructural de cero real.
- Aún no hay descarte automático por contaminación/spillover, structural
  breaks/outliers, funnel, placebos temporales, leave-one-control-out ni
  intervalos OOS.
- La etiqueta de calidad actual es descriptiva; no es un score calibrado.
- El backend efectivo puede ser pycausalimpact, statsmodels u OLS. Los
  registros de ejecución identifican qué fallbacks se activaron.
- El gráfico clásico calcula bandas de efecto con `1,96` (aproximación 95 %)
  aunque `alpha` sea distinto. Algunos paneles individuales rotulan el nivel
  desde `alpha`; la acumulación también usa `1,96`. Interpretar bandas según
  el backend y el parámetro aplicado.
- `prior_level_sd` y `dynamic_regression` se pasan a pycausalimpact. El
  fallback `UnobservedComponents` no aplica esos parámetros con el mismo
  mecanismo; `standardize_data` solo interviene en ese fallback.
- Si se selecciona granularidad mensual, `season_duration` se pasa al backend
  como número de observaciones por paso estacional; el código no lo convierte
  a días.

## 8. Fuente

`mmm_app/analyses/causal_impact.py` y
`mmm_app/ui/dialogs/causal_impact_dialog.py`.
