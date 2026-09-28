# Estadísticas descriptivas

## 1. Propósito

Inspeccionar rápidamente forma, tipos, valores ausentes y distribución básica
del DataFrame recibido. No transforma ni filtra los datos.

## 2. Entrada y parámetros

- **Entrada:** DataFrame completo entregado al plugin.
- **Parámetros configurables:** ninguno. `run(df, **kwargs)` ignora opciones
  adicionales.
- **Columnas requeridas:** ninguna.

## 3. Ejecución paso a paso

1. Lee `df.shape` y forma el texto `filas × columnas`.
2. Copia los nombres de columnas en una lista.
3. Construye una tabla con el dtype de cada columna (`df.dtypes`).
4. Cuenta nulos por columna con `isnull().sum()` y calcula su porcentaje como
   `isnull().mean() × 100`, redondeado a dos decimales.
5. Calcula `df.describe(include="all").T`: estadísticos numéricos y
   descriptivos compatibles con columnas categóricas.

## 4. Salidas

- **Dimensiones:** texto con número de filas y columnas.
- **Columnas:** lista de nombres.
- **Tipos de dato:** columnas `columna`, `tipo`.
- **Valores nulos:** columnas `columna`, `nulos`, `porcentaje`.
- **Resumen numérico:** resultado de `describe(include="all")`, transpuesto
  para que cada variable sea una fila.

No genera gráficos.

## 5. Interpretación y límites

- `count`, `mean`, `std`, cuartiles y otros campos dependen del dtype y de la
  implementación de pandas.
- El porcentaje de nulos usa todas las filas como denominador.
- Es un resumen descriptivo; no contrasta hipótesis ni estima efectos.
- Las conversiones y limpieza que se hayan hecho antes de llamar al plugin
  afectan directamente a los estadísticos.

## 6. Fuente

`MedicionAgil_Light/mmm_app/analyses/descriptive.py`.
