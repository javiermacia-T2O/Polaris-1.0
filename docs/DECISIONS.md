# DECISIONS.md

Decisiones técnicas importantes, con fecha y motivo. No borrar decisiones
anteriores: quedan como historial. Si una decisión se revierte, añadirla
como nueva entrada que la sustituye.

## Formato de entrada

**Fecha - Decisión**: qué se decidió.
**Motivo**: por qué se eligió esa opción y qué alternativas se descartaron.

## Decisiones registradas

### 2026-09-28 - Reutilizar el recolector de tablas

**Decisión**: recorrer diccionarios, listas y tuplas con el mismo recolector
para presentar y exportar resultados.

**Motivo**: la vista y la exportación deben incluir las mismas tablas; la vista
omitía DataFrames anidados en listas.

### 2026-09-28 - Mostrar valores no finitos como no disponibles

**Decisión**: el resumen de Causal Impact presenta valores no finitos como
`—` y ordena por volumen con clave numérica auxiliar.

**Motivo**: evita `NaN` ambiguos y problemas de orden cuando una salida carece
de volumen, sin modificar las métricas originales.

### 2026-09-28 - Sistema de documentación de sesión en /docs

**Decisión**: mantener la documentación de reanudación en Markdown plano en
/docs (PROJECT, PLAN, PROGRESS, ISSUES, ATTEMPTS, DECISIONS, CHECKPOINT).

**Motivo**: cualquier agente o persona puede retomar el proyecto leyendo
estos archivos sin depender del historial de conversación, sin herramientas
ni formatos adicionales.

### 2026-09-28 - Recuperación controlada del filtro R² de GeoX

**Decisión**: cuando GeoX rechace todas las candidatas por `min_r2`, ejecutar
un único reintento con un umbral 0,2 menor, con suelo de 0,5. Mantener el
umbral pedido, el umbral aplicado y una advertencia en el resultado.

**Motivo**: el umbral original se prueba siempre primero y no se presenta un
diseño con calidad degradada como si cumpliese el criterio inicial. Un único
fallback evita el bloqueo del análisis para datasets con menor correlación y
mantiene visible la decisión estadística.

### 2026-09-28 - Sustituir el fallback automático R² de GeoX

**Decisión**: conservar un único intento con el `min_r2` solicitado. No
reducirlo automáticamente.

**Motivo**: la solicitud metodológica posterior prioriza no cambiar el criterio
de calidad ni presentar candidatos relajados como equivalentes. La decisión
anterior permanece como historial, pero no describe el código actual.

### 2026-09-28 - Selección PRE para Causal Impact

**Decisión**: ordenar controles y combinaciones mediante hasta tres
pseudo-cortes PRE con Ridge ligero, y limitar a cinco finalistas para el
backend causal. Elegir por RMSE/MAE PRE OOS; POST solo estima y reporta efecto.

**Motivo**: prevenir selección por significancia o magnitud POST y reducir
modelos costosos. Se exige superar el baseline del promedio del prefijo para
evitar aceptar una serie que no predice mejor que ese referente. Este filtro
no sustituye comprobaciones pendientes de contaminación, placebo, structural
breaks ni sensibilidad.

### 2026-09-28 - Validación y contribuciones de Regresión

**Decisión**: reportar un holdout cronológico OOS, excluir filas con variables
ausentes y omitir la descomposición aditiva de árboles mientras no haya una
implementación SHAP verificada.

**Motivo**: evitar imputación implícita como cero y no presentar
`feature_importances_` como contribuciones locales de predicción.

### 2026-09-28 - Restringir GeoX a TBR

**Decisión**: mostrar y aceptar únicamente TBR para el diseño PRE-test.

**Motivo**: Meridian GeoX 1.0.1 enumera SDID, pero `run_design` lanza
`Unsupported methodology` para SDID; ocultarlo evita una opción que no puede
ejecutarse.

### 2026-09-28 - Evento ITS como modo separado de Regresión

**Decisión**: añadir una opción explícita de ITS segmentada que requiere un
evento/grupo marcado, estima nivel y pendiente con covarianza HAC, y no genera
atribuciones de contribución.

**Motivo**: mantener separados el objetivo predictivo y la estimación asociada
a una intervención temporal; los cambios concurrentes y la endogeneidad
impiden interpretar automáticamente el coeficiente como causal.

### 2026-09-28 - Validación rolling OOS para Regresión

**Decisión**: reportar hasta cinco folds expanding mediante `TimeSeriesSplit`
para el modelo seleccionado, además del holdout cronológico final. Ajustar el
escalador dentro de cada fold y comparar con el promedio del entrenamiento.

**Motivo**: evaluar estabilidad temporal y evitar que métricas in-sample o un
único corte determinen por sí solos la lectura predictiva. No se aplica gap
hasta que el usuario pueda definir horizonte y solapamiento temporal.
