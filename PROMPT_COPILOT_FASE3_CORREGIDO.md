# Polaris — Fase 3: implementación supervisada

Implementa, prueba y documenta la FASE 3 en https://github.com/javiermacia-T2O/Polaris-1.0.git. Este documento SUSTITUYE el prompt anterior de Fase 3; es autocontenido. Ejecuta las decisiones siguientes sin reabrir una investigación arquitectónica. No confundas una instrucción concreta con permiso para ignorar incompatibilidades: si un contrato no puede cumplirse, informa del bloqueo con evidencia, sin inventar resultados ni cambiar la arquitectura silenciosamente.

## 0. Alcance y ejecución eficiente

- Base auditada: `5d1b2a6` (Fases 1/2 terminadas). Lee `AGENTS.md`, estado Git, dependencias y documentación de continuidad. Usa la versión actual compatible; no retrocedas el repositorio. Si hay cambios posteriores, revisa exclusivamente el delta relevante. Conserva todo trabajo ajeno.
- Trabaja en una rama de Fase 3. No hagas commit, push, reset destructivo ni limpiezas generales. No empieces Fase 4; conserva startup, splash, protocolo IPC y algoritmos científicos.
- Objetivo: navegar y operar con CSV/Parquet de 5 GB+ sin materializar íntegramente el origen en Pandas. Pandas solo para resultados acotados o análisis con reserva previa de memoria.
- Reutiliza servicios existentes. Sin migraciones de framework, dependencias nuevas innecesarias ni formateo masivo. Consulta documentación oficial únicamente ante una incompatibilidad concreta de la versión fijada.
- Ejecuta los bloques 1–6 en orden. Cada bloque exige tests focalizados antes del siguiente; suite completa al final. No ejecutes benchmarks grandes tras cada pequeño cambio.
- Si puedes delegar, máximo dos tareas independientes de tests/frontend con contratos ya definidos; un único responsable integra backend. No inventes selección de modelos ni disponibilidad. Reserva razonamiento adicional para contradicciones, concurrencia y equivalencia científica.
- Mantén un único `docs/PHASE3_EXECUTION.md`: base, bloques DONE/PARTIAL/BLOCKED, comandos/resultados, decisiones aplicadas y siguiente acción exacta. Actualízalo al cerrar cada bloque; evita informes duplicados y releer todo el repo.

Rutas base: `MedicionAgil_Light/mmm_app/{core,services,analyses}`, `MedicionAgil_Light/python/medicion_core`, `MedicionAgil_Light/tests`, `src/shared`, `src/features/datasets`, `scripts`.

## 1. Línea base y contratos

Antes de editar, ejecuta los tests relevantes disponibles y registra fallos preexistentes. Usa los entornos, locks y scripts del proyecto; instala dependencias solo en el entorno del proyecto si está permitido. No cambies versiones para ocultar errores. Ausencia de Windows, herramientas o recursos = validación pendiente, nunca PASS.

Añade fixtures pequeños con duplicados, nulos, cadenas vacías, `"(vacío)"`, `" | "`, claves compuestas, fechas y tipos incompatibles. Conserva outputs actuales como referencia para comparar operaciones válidas; documenta por separado cualquier corrección de comportamiento defectuoso.

Corrección de auditoría obligatoria: `_pivot_categories()` SÍ incorpora los filtros de receta mediante el `version_token()` del dataset filtrado que recibe de `_recipe_source()`. No implementes una reparación ficticia de esa clave. Añade un test que lo preserve.

## 2. Recursos, conexiones y cancelación

Archivos: `core/engine.py`, `core/memory_budget.py`, nuevo `core/resource_manager.py`, `services/active_dataset.py`, `services/table_result_cache.py`, `medicion_core/{application,jobs,sidecar}.py`.

- Crea un gestor único con reservas atómicas, espera cancelable y liberación en `finally`. Máximo 2 consultas activas, de ellas 1 pesada. Heavy: conversión, join, agregación completa, sort completo, exportación y materialización científica. Los controles health/cancel nunca esperan estas reservas.
- Perfiles iniciales configurables, NO garantías de RSS: RAM <12 GiB: consulta ligera/pesada 192/256 MiB, 1 thread, techo sidecar 1.25 GiB; RAM 12–<24: 256/384 MiB, 2 threads, techo 2 GiB; RAM >=24: 384/768 MiB, 4 threads, techo 4 GiB. Reserva SO = max(2 GiB, 25% RAM). Limita además por memoria disponible real.
- Admisión: memoria adicional libre = max(0, min(techo sidecar − RSS sidecar, RAM disponible − reserva SO) − reservas adicionales aún no consumidas). No contabilices dos veces memoria ya residente. Una operación imposible falla con mensaje accionable; una temporalmente ocupada espera con cancelación. No hagas reservas anidadas bloqueantes: propaga el contexto de la operación.
- Cuenta todos los datasets/resultados residentes. Sustituye el `retained_bytes=0` de `load_dataset`. Conserva estimaciones para archivos comprimidos; fuerza ruta lazy también para CSV/Parquet >=256 MiB.
- Gestiona como máximo dos conexiones DuckDB independientes reutilizables, con propietario exclusivo durante toda consulta y consumo del reader. El presupuesto incluye conexiones inactivas. Sin cierres concurrentes; libera conexiones inactivas bajo presión. No compartas cursores simultáneamente.
- Elimina cambios locales de límites que eludan al gestor, incluido el `512MB` de `row_count`. Configuración temporal siempre restaurada. Conserva adaptadores para las rutas legadas.
- Vincula request/job y generación con la conexión vigente. `interrupt()` solo afecta a su operación activa. Cancelar un job interrumpe su SQL; recrear la conexión actualiza el vínculo. Cerrar un dataset cancela sus tareas propias, sin invalidar derivados independientes.
- Tests: admisión concurrente, error/cancelación sin fugas de reservas, espera cancelable, conexión recreada, cancelación tardía sin afectar al siguiente trabajo y health disponible bajo carga. Conserva los gates IPC existentes.

## 3. Dataset, caché y navegación coherentes

Archivos: `active_dataset.py`, `engine.py`, nuevo `core/cache_store.py`, `application.py`, schemas/API y componentes de datasets.

- Conserva pushdown y páginas acotadas. Añade dependencias múltiples inmutables a `ActiveDataset`; recorre sus fuentes para validación y protección. Cuenta referencias de datasets Y consultas en curso. No borres archivos todavía referenciados.
- Usa directorio escribible de usuario: `POLARIS_CACHE_DIR` o caché local de Polaris. Separa caché reconstruible, resultados de sesión y temporales de cada proceso. No reutilices directorios temporales de otro proceso activo.
- Metadata de caché: versión del formato, identidad/ruta del origen, tamaño, mtime_ns, opciones de lectura/esquema, versión de consulta y tamaño de salida. Una huella parcial es solo detector adicional, nunca checksum completo. Contrato: origen inmutable mientras esté abierto; cambios detectados invalidan y piden recarga. Recarga explícita invalida incluso si tamaño/mtime coinciden; no prometas detectar modificaciones externas que preservan toda la metadata.
- Publica generaciones inmutables: archivo temporal completo -> validación -> manifest atómico. Nunca sobrescribas una generación en uso. Comprueba versión y cancelación antes de publicar y antes de activar. Fallos dejan intactos origen y versión activa.
- Caché reconstruible: cuota inicial min(20 GiB, 10% volumen), TTL origen 30 días/tablas 7 días, LRU solo de elementos no referenciados. Reserva libre max(2 GiB, 5% volumen). Calcula cuotas conjuntamente por volumen, incluyendo temporales y resultados de sesión. Estima espacio antes y comprueba durante escrituras; ENOSPC cancela limpiamente. Configura límite agregado de spill con el espacio realmente restante; no asignes todo el disco a cada conexión.
- Limpia temporales propios tras fallo. En arranque limpia temporales huérfanos >24 h únicamente tras comprobar que no pertenecen a una sesión viva.
- CSV usable sin esperar conversión ni COUNT. Programa una sola conversión por versión para CSV >=256 MiB tras dos consultas exitosas, cuando no haya trabajo interactivo pendiente y exista presupuesto. Genera desde el origen COMPLETO sin filtros/proyección; conserva opciones de lectura. Si no cabe, omite la optimización y sigue con CSV.
- Cuenta filas durante la conversión; evita un COUNT duplicado del mismo origen. Cambio a Parquet atómico, conservando filtros/proyección/fechas y comprobando generación. Consultas anteriores terminan sobre su snapshot.
- Orden: conserva orden de lectura en consultas directas de navegación. No añadas ORDER BY de todas las columnas ni un sort global a la primera página. Usa identidad interna de fila, oculta y libre de colisiones con nombres del usuario: ordinal estable del CSV antes de filtros, persistido en la caché; identidad física estable para Parquet directo. Para CSV, lectura de navegación/ordinal con preservación de orden y un thread. Verifica la API en DuckDB fijado mediante fixture; no supongas soporte.
- Sort solicitado: criterio del usuario + identidad interna de desempate; resultado ligado a versión. Agregados usan claves de grupo; joins materializados reciben identidad propia; concat usa identidad compuesta origen/fila. No reasignes identidades durante CSV->Parquet ni las exportes como columnas de negocio. Prueba continuidad entre páginas antes/después del cambio. Mantén OFFSET; no introduzcas keyset general sin evidencia.
- Total desconocido = null, no 0. Obtén `has_more` con una fila adicional interna, respetando máximo público 1000. Permite avanzar sin COUNT; actualiza metadata sin releer la página cada segundo.
- DISTINCT: búsqueda server-side parametrizada, límite máximo 100, cursor ligado a versión/columna/búsqueda/último valor y orden determinista. Preserva tipos y diferencia nulo/cadena vacía. Frontend debounce 250 ms, aborta solicitudes obsoletas e invalida por versión. LIMIT acota respuesta, no garantiza ausencia de scan: aplica reservas/cancelación.
- Tests: archivos en uso, dependencia múltiple, cambio/cierre durante conversión, publicación fallida, poco disco, limpieza segura, conteo desconocido, orden con empates y categorías de alta cardinalidad.

## 4. Tablas, pivot y uniones

Archivos: `services/{table_service,table_result_cache,merge_service}.py`, `application.py`.

- Conecta `materialize_table_result` con tablas agregadas Tauri: calcular una vez a Parquet y paginar ese resultado. Proyecciones/filtros simples siguen query-backed. No conviertas el resultado grande a Pandas. Su metadata tampoco dispara COUNT síncrono por tener `file_size=0`.
- Pivot: identidad de categorías como tuplas tipadas; comparaciones null-safe parametrizadas. Etiquetas separadas y desambiguadas; no concatenes valores para identificarlos. Corrige también conversiones previas que confundan NULL y el texto `(vacío)`. Conserva agregación condicional; máximo 50 categorías y 500 columnas finales en AMBAS rutas, Pandas y lazy. Error accionable antes del resultado ancho.
- Cachés en memoria con lock, LRU y TTL 5 min: preview 32 MiB/32 entradas; pivot 1 MiB/32; stats 8 MiB/32. No mantengas el lock durante SQL. Invalidación por versión; son límites de objetos contabilizados, no garantías de RSS total.
- Sustituye `application.merge_datasets -> analysis_frame` por UNION ALL BY NAME (all), proyección común + UNION ALL (common), o JOIN SQL. Valida keys/tipos; conserva duplicados, sufijos y modalidad. Compatibilidad con Pandas: claves nulas emparejan usando `IS NOT DISTINCT FROM`; no conviertas tipos silenciosamente si cambia el significado.
- Concat devuelve consulta lazy con todas sus dependencias. Join grande se materializa por streaming en Parquet de sesión, protegido hasta cerrar el resultado. Inputs Pandas existentes pueden persistirse por lotes; nunca materialices el input grande de disco para adaptarlo a Pandas.
- Preflight de join: estima cardinalidad exacta mediante conteos por claves antes de expandir, incluyendo filas no emparejadas según modalidad y cada paso en joins múltiples. Presupuesto de disco basado en filas y anchura estimada; permite advertencia/rechazo explícito, nunca truncamiento. Mantén salvaguardas actuales hasta tener cobertura equivalente.
- Tests de equivalencia SQL/Pandas y de explosión many-to-many; probar cold/warm y ausencia de recalcular agregados al paginar.

## 5. Análisis, resultados y exportación

- Inventaría todas las llamadas productivas a `analysis_frame`. En `application.run_analysis`, obtén columnas requeridas del contrato del plugin antes de materializar: variables, fecha, dimensiones, controles y anomalías. Aplica solo filtros/rangos semánticamente equivalentes. No recortes periodos de entrenamiento ni agregues/muestrees silenciosamente.
- Preflight: COUNT filtrado + muestra distribuida <=10000 filas para estimar anchura, incluyendo coste de muestreo en la reserva. Factores iniciales: regresión 6, causal 8, GeoX/multisoporte 6, descriptivo 4, correlación 3 más matriz 8*p*p bytes; añade estructuras específicas identificadas en cada plugin. Margen inicial 35%. Son estimaciones, no límites duros ni benchmarks.
- Adquiere reserva antes de convertir; comprueba bytes reales durante lectura por lotes y antes del análisis. Si excede, aborta sin ejecutar sobre datos parciales: muestra estimación/presupuesto y pide reducir periodo/variables/filtrar. Sustituye guards duplicados solo cuando la nueva cobertura esté probada. Conserva algoritmos y tolerancias científicas con tests de equivalencia.
- No expulses resultados de usuario por TTL/LRU. Persiste tablas grandes en Parquet de sesión y gráficos en formato ya soportado; conserva IDs, metadata y disponibilidad hasta cierre/eliminación explícita. Resultados no serializables permanecen contabilizados; rechaza trabajo adicional si no cabe. No uses pickle de contenido externo. Historial de jobs terminales: máximo 100, sin borrar resultados asociados ni jobs activos.
- Conserva exportación Arrow->archivo atómica, sin pasar datos completos por JSON/frontend. Lotes con objetivo 16 MiB, máximo 65536 filas y mínimo 1; estima filas inicialmente y comprueba tamaños reales. No prometas techo estricto por bytes; detecta filas extraordinarias y devuelve error controlado cuando no puedan procesarse. Cierra reader/writer y libera reserva incluso con cancelación/error. Conserva archivo destino anterior si falla.

## 6. Validación y entrega

- Añade generador reproducible por semilla y runner en `scripts`: CSV de aproximadamente 100 MB, 500 MB, 1 GB y 5 GB, más Parquet equivalente; baja/alta cardinalidad, nulls, fechas, texto irregular y caso ancho. Generación incremental fuera de medición. Verifica espacio antes de generar.
- Escenarios: open, primera/siguiente/página profunda, filtro, DISTINCT, sort, groupby, pivot, concat/join, análisis acotado, export y 10 repeticiones. Mide wall time, RSS/peak sidecar y árbol de procesos por separado, RAM posterior, disco temporal/cache y cancelación; registra hardware, versiones y bytes/filas reales.
- Haz smoke pequeño por bloque; matriz grande al final, 3 repeticiones cold/warm. Distingue caché de aplicación y SO: no declares SO cold sin haberlo garantizado. Ejecuta baseline y versión final en las mismas condiciones.
- Objetivo orientativo en 16 GB: navegación habitual 500–750 MiB de árbol de procesos y picos normales <1 GiB cuando sea razonable. Verifica ausencia de crecimiento aproximadamente lineal de RAM con tamaño del archivo en navegación equivalente; operaciones bloqueantes se evalúan aparte. No maquilles fallos relajando límites. Estabilización: reporta serie completa y objetos retenidos, no exijas que el asignador devuelva inmediatamente toda la RAM.
- Ejecuta Python, gates IPC, TypeScript, Vitest, build frontend, Cargo y smoke de release Windows/PyInstaller con scripts existentes. Verifica health/cancel/restart y que no haya procesos huérfanos. Un test mock no certifica 5 GB real.
- Revisa diff final: sin binarios/datasets generados, secretos, cambios ajenos ni modificaciones innecesarias de Fases 1/2. Actualiza documentación de continuidad y límites conocidos.

ENTREGA FINAL BREVE:
STATUS: DONE | PARTIAL | BLOCKED.
Bloques completados; archivos; comandos y resultados; tabla antes/después; pendientes/bloqueos; materializaciones Pandas restantes; riesgos y siguiente comando exacto.
DONE solo con contratos y tests verdes, benchmark real de 5 GB y smoke Windows release. Si falta alguno, PARTIAL o BLOCKED con evidencia. No afirmes que subiste cambios: no tienes autorización de commit/push en este encargo.
