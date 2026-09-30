# PROJECT.md

> Fuente principal de verdad: **el codigo actual**. Este documento solo
> describe el estado conocido del proyecto. Si el codigo y este documento
> discrepan, se actualiza este documento.

## Objetivo del proyecto

Aplicación de escritorio local para equipos de medición y marketing que cargan,
preparan y exploran datos CSV, Excel y Parquet, ejecutan análisis de KPI y
exportan resultados. La variante Light funciona sin servicios remotos ni IA.

## Arquitectura general

La aplicación es un escritorio local con tres capas:

1. **Frontend React 19 + TypeScript + Vite** (`src/`): interfaz, estado y
   cliente IPC único en `src/shared/api.ts`.
2. **Host Tauri 2 (Rust)** (`src-tauri/src/lib.rs`): ventana nativa, diálogos
   de archivo y puente IPC hacia el sidecar. No contiene lógica de negocio.
3. **Sidecar Python 3.12** (`MedicionAgil_Light/python/medicion_core/`):
   servicio `MedicionApplication` que ejecuta carga, preparación, análisis y
   exportación. DuckDB/Arrow gestionan datos grandes; pandas se usa en
   análisis y vistas materializadas. Los análisis son plugins bajo
   `mmm_app/analyses/` cargados bajo demanda.

La comunicación Tauri ↔ Python es **JSON-lines sobre stdin/stdout**, sin
puertos de red ni HTTP. El canal es asíncrono, multiplexado, cancelable y
observable (ver `docs/IPC.md`).

## Datos masivos y preparación

La pestaña **Datos** integra preview, tipado por cabecera y constructor de
tablas. CSV/Parquet se navegan desde disco con páginas acotadas; el conteo no
bloquea la primera vista y una conversión Parquet opcional se ejecuta en
segundo plano. Filtros, orden, tablas, exportaciones y análisis comparten el
gestor de recursos y la cancelación IPC. Los resultados agregados y joins se
materializan atómicamente en Parquet para paginar sin recalcular.

## Arranque

El arranque está coordinado por eventos reales (ver `docs/IPC.md` §9):

- Una ventana **splash** independiente (`public/splash.html`, HTML plano sin
  React) es lo único visible al inicio; pinta su primer frame desde estilos
  inline, sin destello blanco/azul.
- La ventana **principal** arranca oculta (`visible: false`) y solo se muestra
  cuando el motor Python respondió `health` **y** el shell React señaló
  `frontend_ready`.
- El sidecar se **precalienta** durante el `setup` de Tauri, en paralelo con la
  carga del webview, reutilizando el pipeline de FASE 1.
- Un **watchdog** de 45 s convierte un arranque colgado en un fallo recuperable
  con reintento, sin duplicar procesos.
- El release se compila con `--features custom-protocol` para que el webview
  cargue el frontend embebido en lugar de `localhost:1420`.

## Estructura del directorio

Ruta raiz del proyecto: C:\Users\javier.macia\Desktop\APP MEDICIÓN

- src/ - frontend React/TypeScript (app, features, shared).
- src-tauri/ - host Tauri (Rust) y configuracion de empaquetado.
- MedicionAgil_Light/python/medicion_core/ - nucleo del sidecar (aplicacion,
  sidecar, jobs, esquemas, observabilidad, errores).
- MedicionAgil_Light/mmm_app/ - motor cientifico heredado:
  - core/ - motor, carga de datos, presupuesto de memoria, tareas,
    exportacion, rutas de ejecucion.
  - services/ - servicios de datos, tablas, analisis, union de datasets,
    cache de resultados.
  - analyses/ - analisis cargables bajo demanda (descriptivo, correlacion,
    regresion, GeoX, Causal Impact, etc.).
  - models/ - estado de sesion y recetas de tabla.
  - ui/ - utilidades de interfaz.
  - output/ - salidas generadas.
- MedicionAgil_Light/tests/ - suite de pruebas (pytest).
- docs/ - continuidad del trabajo e informes técnicos; el catálogo de análisis
  está en docs/ANALISIS/README.md.
- scripts/ - utilidades de construccion, verificacion y sondeo.
- build_windows.ps1, MedicionAgil.spec, Iniciar.cmd - construccion y
  arranque del paquete portable para Windows.
- dist/ y build/ - artefactos de compilacion (no editar a mano).

(Actualizar esta sección si cambia la estructura o la arquitectura.)

## Documentación de sesión

Los archivos de /docs (PLAN, PROGRESS, ISSUES, ATTEMPTS, DECISIONS,
CHECKPOINT) permiten detener y reanudar el trabajo sin depender del historial
de conversación. Leerlos antes de empezar una sesión y actualizarlos al
terminar.
