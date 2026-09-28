# PROGRESS.md

Registro de progreso. Actualizar obligatoriamente antes de terminar cada
sesión. No borrar entradas: lo completado queda como historial.

## Tareas completadas

- Se corrigió la vista de resultados para recorrer tablas dentro de listas y
  tuplas, igual que la exportación; ambas usan ahora el mismo recolector.
- El resumen ejecutivo de Causal Impact representa métricas, efectos y
  probabilidades no finitos como `—`; el orden por KPI/volumen tolera valores
  de volumen ausentes.
- Se actualizaron los informes de Regresión, Causal Impact y GeoX para reflejar
  selección temporal automática, estacionalidad inferida con PRE y parámetros
  internos que ya no se presentan en el diálogo.
- `compileall` de `mmm_app/` y `tests/` pasó. Smokes aislados del recolector de
  tablas y del resumen causal con NaN pasaron con Python 3.12.14.

- En Regresión, se añadió evaluación rolling/expanding OOS de hasta cinco
  folds cronológicos para el modelo seleccionado, con escalado dentro del
  entrenamiento de cada fold y baseline de promedio PRE por fold. El resultado
  conserva también el holdout final. Se volvió seleccionable en el diálogo el
  modo ITS que el motor ya implementaba.
- Se añadió una prueba para rolling OOS y una aserción de interfaz para ITS;
  compilación sintáctica y smoke OLS determinista pasan. No se pudo ejecutar
  pytest por falta de instalación en los runtimes accesibles.

- Se inventariaron los siete plugins de análisis registrados en
  `mmm_app/analyses/` y se creó un informe Markdown detallado por análisis, más
  un índice en `docs/ANALISIS/README.md` y una portada en `docs/README.md`.
- Se documentaron entradas, parámetros, valores predeterminados, preparación,
  pasos de cálculo, salidas, gráficos y límites según el código.
- Se actualizó `PROJECT.md` con la nueva ubicación de documentación técnica.
- Se revisó que los siete nombres expuestos por los plugins tengan una entrada
  en el índice; esta sesión solo modificó documentación.
- En Causal Impact, los controles y combinaciones ahora se preseleccionan con
  pseudo-cortes expanding PRE (Ridge ligero; deben superar el baseline de
  promedio de entrenamiento; hasta cinco finalistas); el
  ganador se ordena por RMSE/MAE PRE OOS, no por p-value/efecto POST. KPI
  ausentes omiten el KPI, y el resumen informa métricas PRE OOS, backend y
  etiqueta descriptiva de calidad, además de controles descartados y motivo.
- En GeoX, se rechaza KPI ausente, panel no diario, fechas con huecos y panel
  geo-día incompleto; ya no se prorratea, no se filtra el universo por Top-N
  volumen y no se relaja automáticamente `min_r2`. Se pasa explícitamente
  `QualityCheckConfig()` de Meridian GeoX.
- En Regresión, se añadió holdout cronológico OOS (RMSE, MAE, WAPE, sesgo y
  R²), se excluyen filas incompletas en vez de imputar cero y se suprimen las
  contribuciones aditivas falsas de modelos de árbol. Se añadió el modo de
  Evento/ITS segmentada con cambio de nivel/pendiente, HAC/Newey-West, IC y
  efecto acumulado; el diálogo exige un evento/grupo y avisa de límites causales.
- Se añadieron avisos metodológicos breves en los tres diálogos y pruebas
  unitarias nuevas/actualizadas para backtesting, missing vs cero, frecuencia
  GeoX y ausencia del fallback R².
- Se actualizaron informes técnicos de Causal Impact, GeoX y Regresión para
  reflejar cambios y límites que siguen pendientes.
- Se incorporó ITS como modo explícito de Regresión: indicador desde la primera
  fecha marcada, tendencia lineal, salto de nivel, cambio de pendiente,
  covarianza HAC/Newey-West, IC 95 %, efecto por periodo y acumulado. El diálogo
  actualiza su aviso, permite ejecutar ITS sin controles explicativos y exige
  fecha/evento; se añadieron pruebas unitarias de fórmula y guardrails.
- Se identificó el fallo de GeoX: ninguna candidata superaba el R² mínimo
  solicitado de 0,8.
- Se añadió una recuperación controlada en el plugin GeoX: se respeta primero
  el umbral solicitado y, ante el rechazo específico de GeoX, se reintenta una
  vez con `max(0,5, R² solicitado - 0,2)`.
- El resultado recuperado incluye advertencia y registra el R² solicitado y el
  aplicado. Si tampoco hay diseños, devuelve un diagnóstico accionable sin
  traceback esperado.
- Se sincronizó el plugin dentro de la distribución portable existente.
- Se actualizó también la entrada equivalente dentro de
  `dist/MedicionAgil_Light_Portable.zip` y se verificó que coincide por hash
  con la carpeta portable.
- La compilación sintáctica de la fuente y de la prueba nueva terminó
  correctamente.

## Tareas en curso

- Validar gráficos y tablas de los análisis contra dependencias reales y datos
  representativos; el entorno disponible no permite ejecutar esos flujos.
- Implementación global parcial; las capacidades restantes están enumeradas
  en `PLAN.md`, `ISSUES.md` y los informes de análisis.
- El runtime Python 3.12.14 empaquetado en Codex permitió compilar sintácticamente
  los nueve archivos tocados. También pasaron comprobaciones aisladas de
  backtest Causal Impact, holdout OLS, contribuciones de árbol y rechazos
  GeoX de semanal/missing/SDID usando stubs para dependencias UI.
- La suite pytest y las integraciones reales no se ejecutaron: pytest no está
  instalado y las bibliotecas empaquetadas de la app no forman un entorno de
  importación completo. No es validación end-to-end.

## Tareas pendientes

- Ejecutar pruebas metodológicas y de regresión con Python 3.12 funcional.
- Completar controles de contaminación, placebos y sensibilidad Causal Impact.
- Completar GeoX POST-test, guardado/reutilización del `Design` y selección de
  duración/ranking diagnóstico de diseños.
- Completar selección automática de complejidad, tuning temporal y SHAP
  compatible en Regresión; ITS actual aún requiere estacionalidad, eventos
  concurrentes y mayor validación.
- Completar estados de calidad con causas concretas y validación UI end-to-end.
- Reconstruir/verificar la distribución portable y validar con CSV Jamaica.
- Completar POST-test y serialización GeoX, placebos/sensibilidad Causal
  Impact, y diagnósticos ITS/SHAP de Regresión.

## Historial de sesiones

| Fecha | Sesión | Resumen |
|-------|--------|---------|
| 2026-09-28 | Creación del sistema de documentación | Se creó /docs con los 7 archivos de documentación de sesión. Sin otros cambios de código. |
| 2026-09-28 | Corrección de GeoX | Se añadió fallback transparente para el rechazo por R² mínimo; se actualizó la distribución portable, se añadió prueba y se hizo verificación sintáctica. pytest queda pendiente por el entorno Python. |
| 2026-09-28 | Informes técnicos de análisis | Se documentaron los siete análisis encontrados en el cargador de plugins en `docs/ANALISIS/`. Sin cambios de código. |
| 2026-09-28 | Mejoras metodológicas parciales | Causal Impact usa selección PRE OOS; GeoX exige panel diario íntegro, solo TBR y umbral R² estricto; Regresión reporta holdout temporal y no atribuye árboles falsamente. Compilación sintáctica y smoke tests aislados pasaron; pytest, integraciones y funcionalidades restantes pendientes. |
| 2026-09-28 | Regresión rolling OOS / selector ITS | Se añadieron folds expanding OOS y se expuso ITS en el selector. Compilación y smoke OLS determinista pasaron; suite pytest e integración aún no disponibles. |
| 2026-09-28 | Presentación y simplificación de análisis | Se corrigió el renderizado de tablas anidadas y los valores no disponibles de Causal Impact; se actualizaron informes sobre parámetros automatizados. Compilación/smokes aislados pasan; validación gráfica y end-to-end pendiente. |
