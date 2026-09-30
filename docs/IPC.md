# IPC.md — Comunicación Tauri ↔ Python

> Fuente principal de verdad: **el código actual** (`src-tauri/src/lib.rs`,
> `MedicionAgil_Light/python/medicion_core/sidecar.py`,
> `src/shared/api.ts`). Si el código y este documento discrepan, se actualiza
> este documento.

## 1. Arquitectura anterior

La primera versión del puente era **síncrona y de un solo uso**:

- El host Rust lanzaba el sidecar Python y enviaba **una** petición por
  `stdin`, esperando **una** respuesta por `stdout`.
- La espera bloqueaba el hilo que atendía la petición, de modo que una
  operación larga (carga de un CSV de varios GB, análisis, exportación)
  congelaba la interfaz: no se podía consultar `health`, cancelar ni abrir
  otra vista.
- No existía multiplexación: dos peticiones simultáneas no eran posibles.
- No existía cancelación real: una operación en curso terminaba siempre.
- No existía observabilidad por petición.

## 2. Arquitectura actual

```
┌──────────────┐   invoke("sidecar_request")   ┌────────────────────┐
│  React (TS)  │ ────────────────────────────► │  Tauri host (Rust) │
│  api.ts      │ ◄──── evento sidecar://progress│  SidecarManager    │
└──────────────┘                               └─────────┬──────────┘
                                                         │ JSON-lines
                                                         │ stdin/stdout
                                               ┌─────────▼──────────┐
                                               │  Sidecar Python    │
                                               │  SidecarServer     │
                                               │  ├─ reader thread  │
                                               │  ├─ interactive×2  │
                                               │  └─ background×1   │
                                               └────────────────────┘
```

### 2.1 Host Rust (`src-tauri/src/lib.rs`)

- `SidecarProcess` mantiene el proceso, su `stdin` y un **mapa de peticiones
  pendientes** (`HashMap<String, mpsc::Sender<Value>>`).
- Un **hilo lector permanente** parsea cada línea de `stdout` y la enruta:
  - `type == "progress"` → se emite el evento `sidecar://progress` al webview.
  - cualquier otra → se entrega al `Sender` cuyo `id` coincide.
- `route_line()` es una función pura y testeable que implementa ese enrutado.
- `sidecar_request` es un comando **async** que ejecuta la espera bloqueante
  en `spawn_blocking`, de modo que el runtime async y el hilo de UI nunca se
  bloquean.
- `SidecarManager` posee el ciclo de vida: `ensure()` (arranque perezoso),
  `restart()` (reinicio único si el sidecar murió a mitad de petición) y
  `stop()` (al cerrar la app).
- Al recibir EOF en `stdout`, el hilo lector **vacía el mapa de pendientes**:
  las peticiones en vuelo fallan rápido en lugar de esperar al timeout.

### 2.2 Sidecar Python (`medicion_core/sidecar.py`)

- Un **hilo lector** consume `stdin` línea a línea.
- Las operaciones de **control** (`health`, `cancel_request`, `shutdown`,
  `get_job_status`, `cancel_job`, `list_jobs`) se ejecutan **inline** en el
  hilo lector: nunca esperan detrás de trabajo pesado.
- El resto se despacha a dos `ThreadPoolExecutor`:
  - `interactive` (2 workers) para peticiones de UI.
  - `background` (1 worker) para trabajo de prioridad baja.
- `_RequestRegistry` mapea `request_id → _PendingRequest` (un `threading.Event`
  de cancelación y la conexión DuckDB del worker). Cancelar marca el evento y
  llama a `conn.interrupt()`, abortando la consulta en curso.
- `_Writer` serializa todas las escrituras a `stdout` bajo un lock.

### 2.3 Frontend (`src/shared/api.ts`)

- `request()` genera un `requestId`, se suscribe a `sidecar://progress`
  filtrando por ese id cuando se pasa `onProgress`, invoca `sidecar_request` y
  lanza `SidecarError` si la respuesta no es `ok`.
- `cancelRequest(requestId)` invoca el comando `cancel_request`.
- El objeto `api` mantiene su firma original (compatible hacia atrás) y las
  operaciones pesadas aceptan un `RequestOptions` opcional.

## 3. Protocolo

Todos los mensajes son **una línea JSON**. El canal `stdout` es exclusivo del
protocolo; el motor científico escribe en `stderr`.

### 3.1 Petición

```json
{"id": "req-1", "type": "request", "token": "<48 chars>",
 "operation": "get_table_page", "params": {"dataset_id": "d", "offset": 0},
 "priority": "interactive"}
```

### 3.2 Progreso (fire-and-forget)

```json
{"id": "req-1", "type": "progress", "percent": 42, "message": "Leyendo"}
```

### 3.3 Respuesta correcta

```json
{"id": "req-1", "type": "response", "ok": true, "result": { ... }}
```

### 3.4 Respuesta de error

```json
{"id": "req-1", "type": "response", "ok": false,
 "error": {"code": "CANCELLED", "message": "...", "details": {}}}
```

Códigos conocidos: `CANCELLED`, `memory_error`, `invalid_request`,
`unauthorized`, `unsupported_operation` y los códigos de `AppError`.

### 3.5 Cancelación

```json
{"id": "c-1", "type": "cancel", "token": "<48 chars>", "target_id": "req-1"}
```

Respuesta:

```json
{"id": "c-1", "type": "cancel_ack", "ok": true, "result": {"cancelled": true}}
```

`cancelled` es `false` si el id no está en vuelo (ya terminó o no existe).

### 3.6 Apagado

```json
{"id": "s-1", "type": "shutdown"}
```

Responde `{"status": "stopping"}` y termina el bucle.

## 4. Prioridades

| Prioridad     | Valor | Pool         | Uso                                  |
|---------------|-------|--------------|--------------------------------------|
| `control`     | 0     | inline       | health, cancel, estado de jobs       |
| `interactive` | 1     | interactive  | peticiones de UI (por defecto)       |
| `background`  | 2     | background   | trabajo de baja prioridad            |

Las operaciones de control se reconocen por nombre y **siempre** se ejecutan
inline, independientemente de la prioridad declarada.

## 5. Ciclo de vida

1. La app arranca sin sidecar (arranque perezoso).
2. La primera petición llama a `ensure()`, que lanza el proceso con un token
   aleatorio de 48 caracteres.
3. Si el proceso muere a mitad de una petición, `SidecarManager` reinicia
   **una vez** y reintenta.
4. Al cerrar la app (`Exit`/`ExitRequested`) se llama a `stop()`, que mata el
   proceso y limpia el mapa de pendientes. No quedan procesos huérfanos.

## 6. Cancelación por operación

| Operación            | Cancelación                          |
|----------------------|--------------------------------------|
| `get_table_page`     | Real (DuckDB `interrupt()`)          |
| `get_column_values`  | Real (DuckDB `interrupt()`)          |
| `get_date_range`     | Real (DuckDB `interrupt()`)          |
| `run_analysis`       | Real (puente a `JobManager.cancel`)  |
| `merge_datasets`     | Real (DuckDB `interrupt()`)          |
| `export_result`      | Real (comprobación cooperativa)      |
| `export_dataset`     | Real (comprobación cooperativa)      |
| `load_dataset`       | No cancelable (carga atómica)        |

## 7. Observabilidad

Cada petición escribe una línea en `logs/sidecar.jsonl` con:
`operation`, `request_id`, `priority`, `queue_ms`, `execution_ms`,
`total_ms`, `status`, `cancelled`, `error_code`. **Nunca** se registran
contenidos de datasets, solo tiempos y resultado.

## 8. Decisiones

- **stdin/stdout + JSON-lines** en lugar de HTTP/localhost: sin puertos, sin
  superficie de red, sin dependencias extra; el host ya controla el proceso.
- **Hilos + pools** en lugar de asyncio: el motor científico es síncrono y
  DuckDB es thread-safe por conexión (`threading.local`).
- **Control inline**: garantiza que `health`/`cancel` respondan aunque todos
  los workers estén ocupados.
- **`spawn_blocking` en Rust**: la espera bloqueante nunca ocupa el runtime
  async ni el hilo de UI.

## 9. Arranque coordinado (FASE 2)

El arranque deja de ser "lanzar la ventana y esperar" y pasa a ser una
máquina de estados dirigida por eventos reales.

### 9.1 Ventanas

| Ventana  | `visible` inicial | Contenido                          |
|----------|-------------------|------------------------------------|
| `splash` | `true`            | `public/splash.html` (HTML plano)  |
| `main`   | `false`           | bundle React embebido              |

El splash es HTML plano con estilos inline: pinta su primer frame sin React,
sin bundler y sin red, de modo que no hay destello blanco/azul. La ventana
principal permanece oculta hasta que el arranque termina.

### 9.2 Estados

```
STARTING → SPLASH_READY → ENGINE_STARTING → ENGINE_READY
         → FRONTEND_LOADING → FRONTEND_READY → READY
                                          ↘ FAILED
```

Cada transición la dispara un hito real:

- `SPLASH_READY`: el splash invoca `splash_ready` al pintar.
- `ENGINE_STARTING`/`ENGINE_READY`: el prewarm responde `health`.
- `FRONTEND_READY`: el shell React invoca `frontend_ready` desde un efecto.
- `READY`: motor **y** frontend listos; se revela la ventana principal.

**Ningún temporizador** decide la disponibilidad. El único uso del reloj es el
watchdog de timeout (45 s), que convierte un arranque colgado en un `FAILED`
recuperable.

### 9.3 Revelado idempotente

`reveal_once(engine_ready, frontend_ready, revealed)` usa
`revealed.swap(true)` sobre un `AtomicBool`: aunque motor y frontend reporten
disponibilidad a la vez desde hilos distintos, la ventana se muestra una sola
vez. El orden del revelado es fijo (`reveal_plan()`): mostrar `main`, enfocar
`main`, cerrar `splash`, emitir `startup://ready`. El splash se cierra **solo
después** de que la ventana principal es visible, así que nunca hay un
escritorio vacío entre ambas.

### 9.4 Prewarm y watchdog

- El sidecar se arranca en el `setup` de Tauri, en paralelo con la carga del
  webview (no lo dispara un `api.health()` de React). Reutiliza el pipeline de
  FASE 1 sin cambios.
- `spawn_startup_watchdog` falla el arranque si el motor no está listo tras
  `STARTUP_TIMEOUT` (45 s) y el estado no es ya `FAILED`.
- `retry_startup` detiene cualquier sidecar parcial, reinicia el coordinador y
  relanza el prewarm **sin duplicar procesos**.

### 9.5 Eventos y comandos

| Nombre                  | Dirección | Uso                                   |
|-------------------------|-----------|---------------------------------------|
| `startup://stage`       | Rust→UI   | hito de arranque (mensaje + ms)       |
| `startup://failed`      | Rust→UI   | fallo recuperable (mensaje)           |
| `startup://ready`       | Rust→UI   | arranque completo (ms totales)        |
| `splash_ready`          | UI→Rust   | el splash pintó                       |
| `frontend_ready`        | UI→Rust   | el shell React se montó               |
| `retry_startup`         | UI→Rust   | reintentar tras un fallo              |
| `quit_app`              | UI→Rust   | salir desde el splash                 |

### 9.6 Traza de arranque

Cada hito escribe una línea en `stderr` para poder medir el TTI sin depurar:

```text
[startup] setup t=2116ms
[startup] state t=2136ms ENGINE_STARTING
[startup] state t=2439ms FRONTEND_READY
[startup] frontend_ready t=2454ms
[startup] state t=7757ms ENGINE_READY
[startup] engine_ready t=7770ms
[startup] state t=7783ms READY
[startup] ready t=7801ms
```

### 9.7 Build

El release debe compilarse con la feature `custom-protocol`
(`cargo build --release --features custom-protocol`). Sin ella el webview
apunta a `build.devUrl` (`http://localhost:1420`) y la app empaquetada nunca
carga el frontend embebido. `tauri build` la activa automáticamente.