# DECISIONS.md

Decisiones técnicas importantes, con fecha y motivo. No borrar decisiones
anteriores: quedan como historial. Si una decisión se revierte, añadirla
como nueva entrada que la sustituye.

## Formato de entrada

**Fecha - Decisión**: qué se decidió.
**Motivo**: por qué se eligió esa opción y qué alternativas se descartaron.

## Decisiones registradas

### 2026-09-30 - Splash HTML plano, independiente de React

**Decisión**: la ventana `splash` carga `public/splash.html`, un documento
HTML con estilos inline y sin bundler, React ni red.

**Motivo**: el splash debe pintar su primer frame antes de que el webview
principal haya cargado el bundle. Un splash basado en React heredaría el
mismo coste de arranque que intenta ocultar y podría mostrar un destello
blanco/azul. El HTML plano garantiza el primer frame desde el propio
documento y habla con Rust por el puente global (`withGlobalTauri`).

### 2026-09-30 - Disponibilidad por eventos reales, nunca por temporizador

**Decisión**: `StartupCoordinator` solo avanza cuando ocurre un hito real: el
splash pintó (`splash_ready`), el motor respondió `health` (`engine_ready`) y
el shell React se montó (`frontend_ready`). La ventana principal se revela
cuando ambos están listos.

**Motivo**: un temporizador o un porcentaje simulado mostraría la ventana
antes de que la app sea usable. Los hitos reales hacen que `READY` signifique
"la app puede usarse", no "ha pasado el tiempo estimado".

### 2026-09-30 - `READY` idempotente con `AtomicBool::swap`

**Decisión**: `reveal_once()` revela la ventana solo la primera vez que motor
y frontend están listos, usando `swap(true)` sobre un `AtomicBool`.

**Motivo**: el motor y el frontend pueden reportar disponibilidad casi a la
vez desde hilos distintos. `swap` garantiza que exactamente una señal gane la
carrera, de modo que la ventana nunca se muestra ni se cierra dos veces.

### 2026-09-30 - Prewarm del sidecar en el `setup` de Tauri

**Decisión**: el sidecar se arranca durante el `setup` de Tauri, en paralelo
con la carga del webview, en lugar de esperar a un `api.health()` de React.

**Motivo**: el arranque del sidecar congelado es la parte más lenta del TTI.
Lanzarlo en `setup` solapa ese coste con la carga del frontend en vez de
sumarlo, y mantiene el pipeline de FASE 1 (multiplexación, cancelación,
reinicio) intacto.

### 2026-09-30 - Feature `custom-protocol` explícita en `Cargo.toml`

**Decisión**: declarar la feature `custom-protocol = ["tauri/custom-protocol"]`
y compilar el release con `--features custom-protocol`.

**Motivo**: sin ella, un `cargo build --release` deja el webview apuntando a
`build.devUrl` (`http://localhost:1420`) y la app empaquetada nunca carga el
frontend embebido. `tauri build` la activa sola; un `cargo build` manual debe
pasarla explícitamente.

### 2026-09-29 - IPC asíncrono sobre stdin/stdout con JSON-lines

**Decisión**: mantener stdin/stdout + JSON-lines como transporte Tauri ↔
Python, pero convertirlo en asíncrono, multiplexado, cancelable y observable.

**Motivo**: no introduce puertos de red ni HTTP (menor superficie y sin
dependencias), el host ya controla el proceso y el protocolo es trivial de
auditar. Se descartó HTTP/localhost por seguridad y complejidad, y asyncio
porque el motor científico es síncrono y DuckDB es thread-safe por conexión.

### 2026-09-29 - Operaciones de control inline en el hilo lector

**Decisión**: `health`, `cancel_request`, `shutdown`, `get_job_status`,
`cancel_job` y `list_jobs` se ejecutan inline, nunca en los pools de trabajo.

**Motivo**: garantiza que la UI pueda consultar estado y cancelar aunque todos
los workers estén ocupados con una operación larga.

### 2026-09-29 - Cancelación real vía DuckDB `interrupt()`

**Decisión**: cancelar una petición marca un `threading.Event` y llama a
`conn.interrupt()` sobre la conexión del worker.

**Motivo**: aborta la consulta en curso de verdad, en lugar de esperar a que
termine. Las operaciones no cancelables (carga atómica) se documentan como
tales en `docs/IPC.md`.

### 2026-09-29 - `spawn_blocking` para la espera bloqueante en Rust

**Decisión**: el comando `sidecar_request` ejecuta la espera bloqueante en
`tauri::async_runtime::spawn_blocking`.

**Motivo**: la espera nunca ocupa el runtime async ni el hilo de UI, de modo
que la interfaz permanece fluida mientras Python trabaja.

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
