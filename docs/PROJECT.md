# PROJECT.md

> Fuente principal de verdad: **el codigo actual**. Este documento solo
> describe el estado conocido del proyecto. Si el codigo y este documento
> discrepan, se actualiza este documento.

## Objetivo del proyecto

Aplicación de escritorio local para equipos de medición y marketing que cargan,
preparan y exploran datos CSV, Excel y Parquet, ejecutan análisis de KPI y
exportan resultados. La variante Light funciona sin servicios remotos ni IA.

## Arquitectura general

La interfaz Tkinter coordina la sesión, los diálogos y tareas en segundo plano.
DuckDB/Arrow gestionan carga y preparación de datos grandes; pandas se usa en
análisis y vistas materializadas. Los análisis son plugins bajo
`mmm_app/analyses/` cargados bajo demanda. `services/` coordina datos, tablas y
análisis; `core/` contiene motor, presupuesto de memoria, tareas, rutas y
exportación. `tests/` contiene pruebas pytest.

## Estructura del directorio

Ruta raiz del proyecto: C:\Users\javier.macia\Desktop\APP MEDICIÓN

App principal: MedicionAgil_Light/ - aplicacion de escritorio local
(Python 3.12, entrada mmm_app/app_desktop.py, motor DuckDB/Arrow).
Estructura conocida a fecha de creacion de este documento:

- mmm_app/core/ - motor, carga de datos, presupuesto de memoria, tareas,
  exportacion, rutas de ejecucion.
- mmm_app/services/ - servicios de datos, tablas, analisis, union de
  datasets, cache de resultados.
- mmm_app/analyses/ - analisis cargables bajo demanda (descriptivo,
  correlacion, regresion, GeoX, Causal Impact, etc.).
- mmm_app/models/ - estado de sesion y recetas de tabla.
- mmm_app/ui/ - utilidades de interfaz.
- mmm_app/output/ - salidas generadas.
- tests/ - suite de pruebas (pytest).
- docs/ - continuidad del trabajo e informes técnicos; el catálogo de análisis
  está en docs/ANALISIS/README.md.
- build_windows.ps1, MedicionAgil.spec, Iniciar.cmd - construccion y
  arranque del paquete portable para Windows.
- dist/ y build/ - artefactos de compilacion (no editar a mano).

(Actualizar esta sección si cambia la estructura o la arquitectura.)

## Documentación de sesión

Los archivos de /docs (PLAN, PROGRESS, ISSUES, ATTEMPTS, DECISIONS,
CHECKPOINT) permiten detener y reanudar el trabajo sin depender del historial
de conversación. Leerlos antes de empezar una sesión y actualizarlos al
terminar.
