# Inversión ↔ KPI por Canal (acumulado, saturación y marginal)

## 1. Propósito

Comparar la relación observada entre inversión y uno o varios KPI para cada
canal y para cada soporte dentro del canal. Calcula curvas descriptivas
logarítmicas; no asigna causalidad ni estima un MMM con controles.

## 2. Parámetros

| Parámetro | Uso |
|---|---|
| `date_col` | Fecha que ordena los registros; autodetección si se omite |
| `canal_col` | Dimensión canal; autodetección por nombre con `canal` o `channel` |
| `soporte_col` | Dimensión proveedor/plataforma; autodetección por nombres como `soporte`, `partner`, `vendor`, `platform`, `source` |
| `inv_col` | Inversión; autodetección por `inversión`, `spend`, `cost`, `gasto`, `investment`, etc., solo entre numéricas |
| `kpi_cols` | Lista de KPIs numéricos que se analizan |
| `filters` | Valores permitidos por columna; filtro textual exacto tras convertir a `str` |
| `ma_window` | Número de posiciones de la media móvil hacia delante; por defecto 2 |

El diálogo propone columnas con esos nombres y ofrece filtros para columnas
categóricas/textuales y numéricas con hasta 500 valores distintos. La fecha se
elige por rango en la interfaz principal cuando corresponde; el plugin recibe
el dataset filtrado.

## 3. Preparación paso a paso

1. Aplica filtros recibidos. Si dejan el DataFrame vacío, devuelve `Error`.
2. Resuelve columnas faltantes mediante autodetección.
3. Convierte fecha a datetime y elimina fechas inválidas.
4. Convierte inversión a número; los errores y ausencias se rellenan con 0.
5. Normaliza canal y soporte a texto; ausencias/vacíos pasan a `(vacío)`.
6. Itera cada canal observado y cada KPI seleccionado.
7. Conserva fecha, soporte, inversión y KPI; elimina filas incompletas y
   después las que tengan inversión `<=0` o KPI `<=0`.
8. Ordena por soporte y fecha.

## 4. Cálculo de suavizado y curvas

### Media móvil usada por el código

Para una secuencia `x` y ventana `k`, en posición `i` calcula la media de
`x[i : min(i+k, n)]`. Es una ventana **hacia delante**, no centrada ni
retrospectiva. En el extremo final promedia las posiciones restantes. Se
calcula por separado para inversión y KPI dentro de cada soporte; en el total
del canal primero agrega por fecha y después aplica el suavizado. La ventana
por defecto es 2.

### Ajuste logarítmico

Para observaciones suavizadas válidas ajusta por mínimos cuadrados:

`KPI = a + b × ln(Inversión)`

Implementación: `numpy.polyfit(log(inversión), KPI, 1)`. Requiere al menos tres
puntos, inversión positiva, variación de `log(inversión)` y del KPI. Calcula
R² como `1 − SSE/SST`. Si el ajuste produce pendiente negativa, las curvas
dibujadas y el marginal usan `abs(b)`; la tabla conserva `b` firmado.

Marginal mostrado:

`Marginal por cada 100 $ = abs(b) / Inversión × 100`

No representa un efecto incremental identificado experimentalmente.

## 5. Seis gráficos por cada pareja canal–KPI

1. **Canal · Acumulado:** KPI acumulado frente a inversión acumulada por fecha.
2. **Canal · Saturación:** puntos de medias móviles y curva `a + abs(b) ln(x)`.
3. **Canal · Marginal:** curva `100 × abs(b)/x` y puntos correspondientes.
4. **Soportes · Acumulado:** una línea de inversión/KPI acumulados por soporte.
5. **Soportes · Saturación:** puntos suavizados y ajuste log de cada soporte
   que reúna datos suficientes.
6. **Soportes · Marginal:** curva marginal y puntos para cada soporte con
   ajuste válido.

Los acumulados de cada soporte avanzan en orden de fecha. Se generan aunque
falle algún ajuste logarítmico; en ese caso pueden no aparecer la curva o la
tabla de parámetros correspondiente.

## 6. Tablas de salida

- **Parámetros (KPI = a + b·log(Inv)):** canal, KPI, nivel (`Canal` o
  `Soporte`), soporte, `a`, `b`, R² y marginal por 100 $ en la mediana de
  inversión suavizada.
- **Datos normalizados (MA):** filas válidas por soporte con fecha, inversión,
  KPI, ambas medias móviles, acumulados, canal y nombre del KPI.
- **Resumen:** canales detectados, KPIs, ventana y nombres de columnas.

Cada figura se etiqueta internamente con canal y KPI para los filtros de
visualización de la aplicación.

## 7. Límites

- Los registros de KPI o inversión iguales a cero o negativos se descartan;
  esto afecta a ambas curvas y acumulados.
- El promedio hacia delante usa valores futuros respecto de la fila, por lo
  que suaviza para exploración, no para pronóstico en tiempo real.
- La ecuación logarítmica es una aproximación descriptiva, no una curva de
  respuesta causal ni una recomendación automática de presupuesto.
- El R² mide ajuste dentro de los puntos usados y no demuestra causalidad.

## 8. Fuente

`mmm_app/analyses/Análisis Multisoporte.py`.
