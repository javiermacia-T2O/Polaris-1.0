# Matriz de correlación

## 1. Propósito

Calcular una matriz de correlación de Pearson entre las columnas numéricas y
señalar pares cuya correlación absoluta supere 0,7.

## 2. Entrada y parámetros

- **Entrada:** DataFrame.
- **Parámetros configurables:** ninguno; `**kwargs` se ignora.
- **Variables incluidas:** selección de pandas `select_dtypes("number")`.
- **Mínimo:** dos columnas numéricas; si no se cumple, devuelve `Error`.

## 3. Cálculo paso a paso

1. Selecciona solo columnas numéricas.
2. Si hay menos de dos, devuelve `Se necesitan al menos 2 columnas
   numéricas.`.
3. Calcula `num.corr(numeric_only=True)`, cuyo método por defecto es Pearson,
   y redondea la matriz a tres decimales.
4. Recorre cada pareja única de columnas (triángulo superior, sin diagonal).
5. Añade a la tabla de pares las parejas que cumplen estrictamente
   `abs(r) > 0.7`.

## 4. Salidas

- **Matriz de correlación:** DataFrame cuadrado de coeficientes redondeados.
- **Pares con |r| > 0.7:** tabla `Var 1`, `Var 2`, `r`; si no hay pares,
  texto `Ninguna`.

No genera gráficos.

## 5. Interpretación y límites

- `r` expresa asociación lineal; no prueba causalidad.
- El filtro 0,7 es un criterio práctico fijo del código, no un p-value ni un
  test de significación.
- pandas trata los valores ausentes según su cálculo de correlación por pares.
- Columnas constantes pueden producir `NaN`; esos valores no superan el
  filtro de pares fuertes.

## 6. Fuente

`MedicionAgil_Light/mmm_app/analyses/correlation.py`.
