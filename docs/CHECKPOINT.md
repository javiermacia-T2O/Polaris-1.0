# CHECKPOINT.md

Estado exacto del proyecto al finalizar la última sesión y punto exacto desde
el que continuar. Actualizar obligatoriamente antes de terminar cada sesión.

## Última sesión

- **Fecha**: 2026-09-30
- **Estado**: FASE 2 completada. El arranque es ahora coordinado por eventos
  reales: splash independiente (HTML plano, sin React), ventana principal
  oculta hasta que motor y frontend están listos, `StartupCoordinator` con
  estados explícitos, prewarm del sidecar durante el `setup` de Tauri y
  watchdog con reintento. Ver `docs/IPC.md` §7.

## Estado exacto

- **Rama/git**: `main`, remoto `origin` =
  https://github.com/javiermacia-T2O/Polaris-1.0.git. HEAD previo `ee94e6f`.
- **Cambios de esta sesión (FASE 2 arranque)**:
  - `src-tauri/src/lib.rs`: `StartupCoordinator` (8 estados), `reveal_plan()`
    puro, `reveal_once()` idempotente, `pending_state()`, `prewarm_engine()`,
    `watchdog_should_fail()` + `spawn_startup_watchdog()`, comandos
    `splash_ready`/`frontend_ready`/`retry_startup`/`quit_app`, traza
    `[startup]` a stderr. 20 tests unitarios.
  - `src-tauri/Cargo.toml`: feature `custom-protocol` (sin ella un
    `cargo build --release` deja el webview apuntando a `localhost:1420`).
  - `src-tauri/tauri.conf.json`: ventana `splash` (visible, sin decoración,
    `alwaysOnTop`, fondo `#0b1f3a`) y `main` (`visible: false`).
  - `src-tauri/capabilities/splash.json` (nuevo): permisos mínimos del splash.
  - `public/splash.html` (nuevo): splash HTML plano, escucha
    `startup://stage`/`startup://failed`, botones Reintentar/Detalles/Salir.
  - `src/main.tsx`: `signalFrontendReady()` idempotente desde un efecto React
    real (sin `requestAnimationFrame`, que WebView2 pausa en ventanas ocultas).
  - `src/test/frontendReady.test.ts` (nuevo): 3 tests de la señal de arranque.
- **Pruebas**: `cargo test --lib` 20/20; Vitest 4/4; `tsc -b` limpio; suite
  Python 269 pasan, 2 fallos **preexistentes** de Tkinter
  (`test_light_geox_layout.py`). Gate FASE 1 (`scripts/gate_fase1.py`) PASS.
- **Arranque release medido** (`medicion-agil.exe`, sidecar `--onedir`):
  `setup 2450 ms`, `FRONTEND_READY 2835 ms`, `ENGINE_READY 7086 ms`,
  `READY 7121 ms`; sin procesos huérfanos al cerrar
  (`SIDECARS_DURING=1 SIDECARS_AFTER=0`).
- **Entorno**: Python 3.12.14 en `.venv`; Node v24.21.0; Rust 1.98.1.

## Continuar desde

1. FASE 3 (no iniciada): no avanzar sin instrucción explícita.
2. Opcional: corregir los 2 fallos preexistentes de `test_light_geox_layout.py`.
3. Opcional: reducir el tiempo de `ENGINE_READY` (el arranque del sidecar
   congelado domina el TTI; ver `docs/IPC.md` §7.4).

Los informes técnicos están indexados en `docs/README.md` y
`docs/ANALISIS/README.md`. La fuente de verdad sobre comportamiento sigue
siendo el código ejecutable.
