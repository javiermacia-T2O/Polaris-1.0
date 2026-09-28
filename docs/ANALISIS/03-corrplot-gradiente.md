# Corrplot (gradiente)

## 1. Propósito

Presentar asociaciones entre variables numéricas mediante dos gráficos y una
tabla de parejas ordenada por magnitud.

## 2. Entrada y parámetros

- **Entrada:** DataFrame.
- **`method`:** método de `DataFrame.corr`; valores de pandas, normalmente
  `pearson`, `kendall` o `spearman`. Por defecto, `pearson`.
- **Resto de opciones:** `**kwargs` se ignora.
- **Variables incluidas:** numéricas con desviación estándar superior a
  `1e-12`.
- **Mínimo:** dos variables con varianza; si no, devuelve `Error`.

## 3. Cálculo paso a paso

1. Selecciona columnas numéricas.
2. Elimina columnas con `std <= 1e-12`.
3. Calcula `num.corr(method=method)` y redondea a tres decimales.
4. Forma todas las parejas únicas, omite `NaN` y calcula `|r|`.
5. Asigna intensidad: `>=0,9` muy fuerte; `>=0,7` fuerte; `>=0,5`
   moderada; `>=0,3` débil; inferior muy débil.
6. Ordena las parejas de mayor a menor `|r|`.
7. Separa las parejas con `|r| >= 0,5` (umbral inclusivo) para una tabla
   adicional.

## 4. Gráficos

### Corrplot de círculos

- Matriz completa, con una celda por pareja.
- El radio es `0,45 × |r|`; no dibuja círculos no diagonales con radio menor
  que `0,01`.
- El signo se codifica con mapa divergente azul–blanco–rojo; la diagonal se
  dibuja en gris.
- Incluye barra de color entre −1 y 1.

### Heatmap

- Una celda por coeficiente, con la misma escala divergente fija de −1 a 1.
- Anota el coeficiente con dos decimales.
- Texto blanco cuando `|r| > 0,6`; negro en los demás casos.

## 5. Salidas

- Corrplot, heatmap y matriz de correlación.
- **Pares ordenados por |r|:** `Var 1`, `Var 2`, `r`, `|r|`, `Intensidad`.
- **Pares con |r| ≥ 0.5:** subconjunto o texto `Ninguno`.
- Texto orientativo sobre tamaño y signo.

## 6. Interpretación y límites

- Aunque el texto de ayuda de la salida diga “correlaciones significativas”,
  este plugin no calcula p-values ni pruebas de significación: los círculos
  representan todos los coeficientes finitos no triviales.
- Correlación no implica causalidad. Correlaciones altas pueden reflejar
  tendencia, estacionalidad, escalas compartidas o colinealidad.
- El método elegido afecta a la matriz y a todos los gráficos, pero no se
  valida explícitamente antes de llamar a pandas.

## 7. Fuente

`MedicionAgil_Light/mmm_app/analyses/Análisis de correlación.py`.
