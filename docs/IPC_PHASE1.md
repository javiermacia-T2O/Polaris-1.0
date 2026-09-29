# Fase 1: transporte IPC

## Estado

Implementación local lista para revisión, con validación Python y frontend; falta ejecutar la validación Rust/Tauri en CI o en una máquina con Cargo. No iniciar la fase 2 hasta revisar ese resultado y probar el arranque real de Tauri.

## Arquitectura entregada

- Rust conserva un lector permanente de stdout, correlaciona cada respuesta por ID de transporte y entrega la respuesta al llamador. Los eventos de progreso no resuelven solicitudes. Un escritor dedicado envía JSON-lines sin retener el mutex del proceso mientras se espera una respuesta.
- El mutex de `SidecarManager` protege la creación y sustitución del proceso. Las llamadas Tauri usan `spawn_blocking`. Cada solicitud tiene un ID de llamador y un ID de transporte único, de modo que una respuesta tardía tras timeout no puede resolver una llamada posterior que reutilice el mismo ID.
- Python usa dos carriles con un worker cada uno: interactivo (hasta 24 solicitudes admitidas) y pesado (hasta 8, incluidos los trabajos en ejecución). `health`, `cancel_job` y `get_job_status` se atienden en la ruta de control. Las colas rechazan exceso con `busy`; las respuestas pueden llegar fuera de orden y conservan su ID.
- Rust admite hasta 64 solicitudes pendientes y reserva 8 plazas adicionales para control; el escritor tiene una cola acotada de 72 mensajes. Asigna plazos según operación: 15 s para `health`, 10 s para estado/cancelación de jobs, 30 s para lecturas y 300 s para operaciones pesadas. Si una llamada vence, envía cancelación y hace una comprobación independiente de salud de 3 s; invalida el proceso sólo ante desconexión o si esa sonda falla. Las mutaciones con resultado incierto no se repiten automáticamente.
- Cada worker Python posee su conexión DuckDB. El contexto de cancelación enlaza el marcador con la conexión activa y repite `interrupt()` hasta terminar la consulta; el worker cierra su conexión al cerrar el dispatcher. El hilo de interrupción no puede alcanzar una consulta posterior.
- Los análisis en background comparten la cancelación cooperativa, y el shutdown marca como cancelados los jobs en cola o en ejecución. El cierre del sidecar drena trabajo aceptado en EOF; el comando explícito `shutdown` primero solicita cancelar el trabajo pendiente.
- El frontend pasa `AbortSignal` a lecturas y sondeos, envía cancelación al cancelar/sustituir una consulta y evita que una respuesta antigua reemplace el resultado más reciente. La cancelación de una lectura no equivale a rollback.
- Los registros estructurados del sidecar incluyen prioridad, ID, cola, duración de ejecución y duración total, estado y errores.

## Límites conocidos

- DuckDB puede interrumpir la consulta SQL activa. Trabajo nativo fuera de DuckDB, pasos pandas y otras secciones Python sólo comprueban cancelación cooperativamente o esperan a terminar. No se garantiza rollback de efectos laterales.
- Si el proceso sidecar se reinicia, se pierden los handles de datasets y jobs en memoria y el usuario puede tener que volver a cargar datos. El cierre mata y espera al proceso hijo directo; no se ha probado la terminación de posibles procesos descendientes.
- Los dos carriles tienen un worker cada uno para mantener aisladas sus conexiones. Más concurrencia dentro de un mismo carril queda en cola; no se afirma paralelismo ilimitado.
- El heartbeat es una consulta `health` por el canal de control; no hay stream de progreso genérico para todas las operaciones. Los trabajos analíticos existentes conservan su polling de estado.
- No se ha probado el ciclo completo dentro de una ventana Tauri ni el empaquetado portable con este cambio.
- Rust/Tauri no se ha compilado en este entorno porque no está instalado Cargo. Se añadió un workflow para compilar y ejecutar pruebas Rust en Windows; el resultado de CI aún debe revisarse.

## Matriz de evidencia

| Área | Evidencia disponible | Estado |
|---|---|---|
| Dispatcher Python | Suite completa: 253 pasaron, 6 omitidas; incluye salud concurrente, respuestas fuera de orden, reserva de carril, saturación, cancelación en cola, error de worker y mensajes inválidos. | Pasó localmente |
| Cancelación DuckDB | Prueba de consulta de mil millones de filas: interrupción, respuesta de cancelación y consulta posterior en el mismo worker; pruebas del ciclo de vida de jobs y scopes. | Pasó localmente (3 pruebas enfocadas) |
| Frontend | `npm run build`; Vitest: 6 pruebas, incluidas cancelación por cambio de página y protección frente a respuestas tardías. | Pasó |
| Rust/Tauri | 9 pruebas unitarias activas y 1 fixture de subproceso ignorada por defecto: correlación, EOF/crash, timeout, IDs repetidos, lifecycle y política de plazos. Workflow CI Windows configurado. | Pendiente de ejecutar/revisar; Cargo no disponible localmente; workflow aún no lanzado |
| Aplicación empaquetada | Arranque Tauri real y prueba funcional portable. | Pendiente |

## Criterios para cerrar la fase

1. Revisar un run verde del workflow Rust/Tauri y corregir cualquier fallo de compilación o test.
2. Ejecutar `tauri dev` y comprobar salud concurrente, cambio rápido de páginas, preview cancelado, cancelación SQL, recuperación tras timeout y cierre sin proceso huérfano.
3. Reconstruir/probar el sidecar portable y actualizar esta matriz con la evidencia observada.

La fase 2 empieza después de completar estos criterios.
