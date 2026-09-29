# CHECKPOINT.md

## 2026-09-29 — fase 1 IPC, implementación lista para revisión

Trabajo local en la rama `codex/phase1-ipc-wip`, desde `ee94e6f`. Commits de esta actualización: `33a626c` (base IPC y gaps), `5b8248a` (cancelación frontend), `0527a81` (plazos/correlación/lifecycle Rust), `5a7639f` (carriles y cancelación DuckDB), `f4baf83` (workflow CI) y `a54fe48` (ventana de IDs cancelados tempranos). No se ha hecho push.

La implementación incluye lector multiplexado y escritor independiente en Rust, correlación de IDs ante respuestas tardías, colas limitadas CONTROL/INTERACTIVE/HEAVY, timeouts por operación, cancelación cooperativa/SQL y propagación de `AbortSignal` en consultas frontend. La cancelación DuckDB se verifica sobre una consulta real larga y se comprueba que una consulta siguiente funciona en el worker. Ver arquitectura, evidencia y límites en `docs/IPC_PHASE1.md`.

Verificaciones comunicadas por los agentes: `uv run --frozen --group dev pytest -q`: **255 passed, 6 skipped**; `npm run build` correcto y Vitest: **6 passed**. Repetí el conjunto IPC/cancelación en esta sesión: **6 passed**. Se añadieron **9 pruebas Rust activas** y un fixture de subproceso ignorado por defecto, pero no hay Cargo en el entorno; el workflow Windows aún no se ha lanzado porque los commits no se han publicado. Tampoco se ejecutó `tauri dev` ni se reconstruyó el portable.

Límites operativos: un reinicio del sidecar pierde handles de dataset y job; cancelación nativa/pandas fuera de DuckDB puede esperar a fin de operación; `stop` mata/recolecta el proceso hijo directo y no se ha validado el cierre de procesos descendientes.

Siguiente paso: esperar/revisar workflow Rust Windows; corregir si falla; luego validar el flujo Tauri real y el sidecar empaquetado. Mantener la fase 2 bloqueada hasta esos criterios.

Tras `72912be` se repitió pytest completo: **255 passed, 6 skipped**. Los callbacks IPC también resuelven errores de telemetría/serialización y liberan capacidad.
