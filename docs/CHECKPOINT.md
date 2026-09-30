# CHECKPOINT.md

Estado exacto del proyecto al finalizar la última sesión y punto exacto desde
el que continuar. Actualizar obligatoriamente antes de terminar cada sesión.

## Última sesión

- **Fecha**: 2026-09-30
- **Estado**: implementación funcional de FASE 3 completada en
  `codex/fase3-complete`; validación Windows final pendiente. Navegación 5 GB,
  recursos/cancelación, tablas materializadas, pivot tipado, unión lazy y
  espacio unificado Datos+Constructor están implementados.

## Estado exacto

- **Rama/git**: `codex/fase3-complete`, base `4140069`, remoto `origin` =
  https://github.com/javiermacia-T2O/Polaris-1.0.git.
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

1. Ejecutar los comandos Windows exactos de `docs/PHASE3_EXECUTION.md`.
2. Si todos pasan, fusionar `codex/fase3-complete` en `main`.
3. No iniciar Fase 4 dentro de este cierre.

Los informes técnicos están indexados en `docs/README.md` y
`docs/ANALISIS/README.md`. La fuente de verdad sobre comportamiento sigue
siendo el código ejecutable.
