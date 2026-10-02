import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../shared/api";
import { Loading } from "../../shared/Loading";
import type { SchemaField } from "../../app/AnalysisContext";
import { useUiStore } from "../../app/store";

type RegressionEvent = { id: string; name: string; start: string; end: string };
type Interaction = { left: string; right: string };
type RegressionRow = Record<string, unknown>;
type Point = { date: string; time: number; target: number; vars: Record<string, number> };

const PALETTE = ["#ffb000", "#a449ff", "#ff454f", "#16ae78", "#46b9ff", "#ff8a00", "#16d3d0", "#f263b2"];
const NUMERIC_TYPE = /int|float|double|decimal|numeric|number|real|hugeint/i;
const shortName = (name: string) => name.includes(" · ") ? name.split(" · ").slice(1).join(" · ") : name;

export function RegressionWorkbench({ datasetId, fields, parameters, setParameter, run, runPending, runDisabled }:
  { datasetId: string; fields: SchemaField[]; parameters: Record<string, unknown>;
    setParameter: (key: string, value: unknown) => void; run: () => void; runPending: boolean; runDisabled: boolean }) {
  const availableColumns = useQuery({
    queryKey: ["regression-columns", datasetId], queryFn: () => api.columns(datasetId),
    enabled: Boolean(datasetId), refetchOnMount: "always",
  });
  const detectedDates = useQuery({
    queryKey: ["regression-dates", datasetId], queryFn: () => api.dateColumns(datasetId),
    enabled: Boolean(datasetId), refetchOnMount: "always", retry: false,
  });
  const knownColumns = availableColumns.data ? new Set(availableColumns.data.map((column) => column.name)) : null;
  const optionsFor = (key: string) => {
    const options = fields.find((field) => field.key === key)?.options?.map(String) ?? [];
    return knownColumns && key !== "regression_type"
      ? options.filter((name) => knownColumns.has(name)) : options;
  };
  const methodOptions = optionsFor("regression_type");
  const detectedDateOptions = detectedDates.data?.map((item) => item.column)
    .filter((name) => knownColumns?.has(name)) ?? [];
  const namedDateOptions = availableColumns.data?.filter((column) => /date|time|timestamp/i.test(column.type)
    || /fecha|date|semana|mes|year|año/i.test(column.name)).map((column) => column.name) ?? [];
  const dateOptions = detectedDateOptions.length ? detectedDateOptions
    : namedDateOptions.length ? namedDateOptions : optionsFor("date_col");
  const targetOptions = optionsFor("target_col").length ? optionsFor("target_col")
    : availableColumns.data?.filter((column) => NUMERIC_TYPE.test(column.type)).map((column) => column.name) ?? [];
  const dateColumn = dateOptions.includes(String(parameters.date_col ?? ""))
    ? String(parameters.date_col) : String(dateOptions[0] ?? "");
  const target = targetOptions.includes(String(parameters.target_col ?? ""))
    ? String(parameters.target_col) : String(targetOptions[0] ?? "");
  const inputOptions = (optionsFor("input_cols").length ? optionsFor("input_cols")
    : availableColumns.data?.filter((column) => NUMERIC_TYPE.test(column.type)).map((column) => column.name) ?? [])
    .filter((name) => name !== target);
  useEffect(() => {
    if (!availableColumns.data) return;
    if (dateColumn && parameters.date_col !== dateColumn) setParameter("date_col", dateColumn);
    if (target && parameters.target_col !== target) setParameter("target_col", target);
  }, [availableColumns.data, dateColumn, target, parameters.date_col, parameters.target_col, setParameter]);
  const configuredInputs = Array.isArray(parameters.input_cols) ? parameters.input_cols.map(String) : [];
  const inputs = configuredInputs.filter((name) => inputOptions.includes(name));
  useEffect(() => {
    if (availableColumns.data && inputs.length !== configuredInputs.length) setParameter("input_cols", inputs);
  }, [availableColumns.data, configuredInputs.join("\u001f"), inputs.join("\u001f"), setParameter]);
  const events = Array.isArray(parameters.events) ? parameters.events as RegressionEvent[] : [];
  const interactions = Array.isArray(parameters.interaction_terms)
    ? parameters.interaction_terms as Interaction[] : [];
  const chartTypes = (parameters.chart_types && typeof parameters.chart_types === "object"
    ? parameters.chart_types : {}) as Record<string, string>;
  const chartColors = (parameters.chart_colors && typeof parameters.chart_colors === "object"
    ? parameters.chart_colors : {}) as Record<string, string>;
  const colorFor = (column: string) => chartColors[column] ?? PALETTE[inputOptions.indexOf(column) % PALETTE.length];
  const setRegressionOverview = useUiStore((state) => state.setRegressionOverview);
  const [variableSearch, setVariableSearch] = useState("");
  const [marking, setMarking] = useState(false);
  const [showEvents, setShowEvents] = useState(true);
  const [showGrid, setShowGrid] = useState(true);
  const [dragStart, setDragStart] = useState<number | null>(null);
  const [dragEnd, setDragEnd] = useState<number | null>(null);
  const dragStartRef = useRef<number | null>(null);
  const [eventDraft, setEventDraft] = useState<RegressionEvent | null>(null);
  const plotContainer = useRef<HTMLDivElement>(null);
  const [plotSize, setPlotSize] = useState({ width: 900, height: 480 });
  const [interactionLeft, setInteractionLeft] = useState("");
  const [interactionRight, setInteractionRight] = useState("");

  useEffect(() => {
    const element = plotContainer.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      const next = { width: Math.max(250, element.clientWidth - 6),
        height: Math.max(170, element.clientHeight - 3) };
      setPlotSize((current) => Math.abs(current.width - next.width) > 2 ||
        Math.abs(current.height - next.height) > 2 ? next : current);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const WIDTH = plotSize.width;
  const HEIGHT = plotSize.height;
  const LEFT = 76;
  const RIGHT = WIDTH - 76;
  const TOP = 12;
  const BOTTOM = HEIGHT - 35;
  const dateTickCount = WIDTH < 680 ? 5 : 7;

  const chartColumns = [...new Set([target, ...inputOptions])].filter(Boolean);
  const preview = useQuery({
    queryKey: ["regression-preview", datasetId, dateColumn, chartColumns.join("\u001f")],
    queryFn: () => api.regressionPreview(datasetId, dateColumn, chartColumns, 1200),
    enabled: Boolean(datasetId && availableColumns.data && !availableColumns.isFetching
      && !detectedDates.isFetching && dateColumn && target && chartColumns.length),
    staleTime: 10 * 60 * 1000,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const previewLoading = availableColumns.isFetching || detectedDates.isFetching || preview.isFetching;

  const chartDateColumn = preview.data?.columns[0] ?? dateColumn;
  useEffect(() => {
    if (preview.data && chartDateColumn && chartDateColumn !== dateColumn) setParameter("date_col", chartDateColumn);
  }, [preview.data, chartDateColumn, dateColumn, setParameter]);
  const points = useMemo(() => {
    const rows = (preview.data?.rows ?? []) as RegressionRow[];
    return rows.map((row, index) => {
      const date = String(row[chartDateColumn] ?? "");
      const parsed = Date.parse(date);
      const vars: Record<string, number> = {};
      for (const column of inputOptions) {
        const value = numberValue(row[column]);
        if (value !== null) vars[column] = value;
      }
      const targetValue = numberValue(row[target]);
      return targetValue === null ? null : {
        date, time: Number.isFinite(parsed) ? parsed : index,
        target: targetValue, vars,
      } satisfies Point;
    }).filter((point): point is Point => point !== null)
      .sort((a, b) => a.time - b.time);
  }, [preview.data, chartDateColumn, target, inputOptions.join("\u001f")]);

  const correlations = useMemo(() => Object.fromEntries(inputOptions.map((column) => [
    column, correlation(points.map((point) => point.target),
      points.map((point) => point.vars[column] ?? Number.NaN)),
  ])), [points, inputOptions.join("\u001f")]);
  const vifValues = useMemo(() => calculateVif(points, inputs), [points, inputs.join("\u001f")]);
  const visiblePoints = points;
  const chartPoints = useMemo(() => makeChartPoints(visiblePoints, inputs), [visiblePoints, inputs.join("\u001f")]);
  const timeStart = points[0]?.time ?? 0;
  const timeEnd = points[points.length - 1]?.time ?? timeStart + 1;
  const xAt = (time: number) => LEFT + (RIGHT - LEFT) * ((time - timeStart) / Math.max(1, timeEnd - timeStart));
  const yValues = points.map((point) => point.target);
  const yMin = Math.min(0, ...yValues);
  const yMax = Math.max(1, ...yValues);
  const yAtTarget = (value: number) => BOTTOM - (BOTTOM - TOP) * ((value - yMin) / Math.max(1e-9, yMax - yMin));
  const zValues = chartPoints.flatMap((series) => series.values.map((point) => point.z));
  const zMin = Math.min(-2, Math.floor(Math.min(...zValues)));
  const zMax = Math.max(2, Math.ceil(Math.max(...zValues)));
  const yAtZ = (value: number) => BOTTOM - (BOTTOM - TOP) * ((value - zMin) / (zMax - zMin));
  const targetPath = visiblePoints.map((point, index) =>
    `${index ? "L" : "M"}${xAt(point.time).toFixed(1)},${yAtTarget(point.target).toFixed(1)}`).join(" ");
  const filteredInputs = inputOptions.filter((column) =>
    column.toLocaleLowerCase("es").includes(variableSearch.toLocaleLowerCase("es")));
  const fieldValue = (key: string) => String(parameters[key] ?? "");
  const setEvents = (next: RegressionEvent[]) => setParameter("events", next);
  const setInputs = (next: string[]) => {
    setParameter("input_cols", next);
    const validInteractions = interactions.filter((item) => next.includes(item.left) && next.includes(item.right));
    if (validInteractions.length !== interactions.length) setParameter("interaction_terms", validInteractions);
  };
  const setInteractions = (next: Interaction[]) => setParameter("interaction_terms", next);

  const pointerTime = (event: React.PointerEvent<SVGSVGElement>) => {
    const svg = event.currentTarget;
    const bounds = svg.getBoundingClientRect();
    // SVGs can be letterboxed when their viewBox ratio differs from the card.
    // Account for that offset before translating a drag to a calendar date.
    const scale = Math.min(bounds.width / WIDTH, bounds.height / HEIGHT);
    const offsetX = (bounds.width - WIDTH * scale) / 2;
    const pointX = (event.clientX - bounds.left - offsetX) / scale;
    const fraction = Math.min(1, Math.max(0, (pointX - LEFT) / (RIGHT - LEFT)));
    return timeStart + fraction * (timeEnd - timeStart);
  };
  const finishEvent = (event: React.PointerEvent<SVGSVGElement>) => {
    const startTime = dragStartRef.current;
    if (!marking || startTime === null || !points.length) return;
    const endTime = pointerTime(event);
    const startPoint = closestPoint(visiblePoints, Math.min(startTime, endTime));
    const endPoint = closestPoint(visiblePoints, Math.max(startTime, endTime));
    if (!startPoint || !endPoint) return;
    const start = startPoint.date.slice(0, 10);
    const end = endPoint.date.slice(0, 10);
    const nextIndex = events.length + 1;
    setEvents([...events, { id: crypto.randomUUID(), name: `Evento ${nextIndex}`, start, end }]);
    dragStartRef.current = null;
    setDragStart(null);
    setDragEnd(null);
    setMarking(false);
  };

  const method = fieldValue("regression_type") || String(methodOptions[0] ?? "");
  const itsMode = method.toLocaleLowerCase("es").startsWith("evento / its");
  const availableForInteractions = inputs;

  const activeCorrelations = inputs.map((name) => correlations[name])
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  const meanCorrelation = activeCorrelations.length ? mean(activeCorrelations.map(Math.abs)) : null;
  const finiteVifs = inputs.map((name) => vifValues[name])
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  const maxVif = inputs.some((name) => vifValues[name] === Number.POSITIVE_INFINITY)
    ? Number.POSITIVE_INFINITY : finiteVifs.length ? Math.max(...finiteVifs) : null;
  const ready = Boolean(target && dateColumn && (inputs.length || events.length) && (!itsMode || events.length === 1));
  useEffect(() => {
    setRegressionOverview({ datasetId, active: inputs.length, total: inputOptions.length,
      correlation: meanCorrelation, vif: maxVif, events: events.length, method });
    return () => setRegressionOverview(null);
  }, [datasetId, inputs.length, inputOptions.length, meanCorrelation, maxVif, events.length, method, setRegressionOverview]);
  const eventSave = () => {
    if (!eventDraft?.name.trim() || !eventDraft.start || !eventDraft.end || eventDraft.start > eventDraft.end) return;
    const item = { ...eventDraft, name: eventDraft.name.trim() };
    setEvents(events.some((current) => current.id === item.id)
      ? events.map((current) => current.id === item.id ? item : current)
      : [...events, item]);
    setEventDraft(null);
  };
  const eventNew = () => {
    const start = visiblePoints[Math.max(0, Math.floor(visiblePoints.length / 2) - 1)]?.date.slice(0, 10) ?? "";
    setEventDraft({ id: crypto.randomUUID(), name: `Evento ${events.length + 1}`, start, end: start });
  };
  return <div className="regression-workbench">
    <div className="regression-actions-bar">
      <label className="regression-date-control">Fecha
        <select aria-label="Columna temporal" value={dateColumn}
          onChange={(event) => setParameter("date_col", event.target.value)}>
          {dateOptions.map((option) => <option key={option}>{option}</option>)}
        </select>
      </label>
      <div className="regression-display-controls">
        <label>Objetivo <select value={String(parameters.chart_type ?? "Línea")}
          onChange={(event) => setParameter("chart_type", event.target.value)}>
          <option>Línea</option><option>Columnas</option></select></label>
        <label><input type="checkbox" checked={showEvents} onChange={(event) => setShowEvents(event.target.checked)} /> Eventos</label>
        <label><input type="checkbox" checked={showGrid} onChange={(event) => setShowGrid(event.target.checked)} /> Rejilla</label>
      </div>
      <div className="regression-actions-right">
        <button type="button" className={marking ? "primary active" : "quiet-button"}
          onClick={() => { setMarking(!marking); dragStartRef.current = null; setDragStart(null); setDragEnd(null); }}>
          {marking ? "Cancelar marca" : "◎  Marcar eventos"}</button>
        <button type="button" className="quiet-button" onClick={eventNew}>＋ Añadir evento</button>
        <button type="button" className="primary" onClick={run}
          disabled={runDisabled || runPending || !ready}>
          {runPending ? "Enviando…" : "▶  Ejecutar análisis"}</button>
      </div>
    </div>
    <section className="regression-card regression-config-card">
      <div className="regression-card-heading"><h2>Configuración del modelo</h2></div>
      <div className="regression-steps">
        <div className="regression-step">
          <span className="step-number">1</span>
          <div className="step-content">
            <h3>Método de estimación</h3><p>Selecciona el método de regresión.</p>
            <select aria-label="Método de estimación" value={fieldValue("regression_type")}
              onChange={(event) => setParameter("regression_type", event.target.value)}>
              {methodOptions.map((option) => <option key={option}>{option}</option>)}</select>
          </div>
        </div>
        <div className="regression-step">
          <span className="step-number">2</span>
          <div className="step-content">
            <h3>Variable objetivo</h3><p>Selecciona la variable a predecir.</p>
            <select aria-label="Variable objetivo" value={target} onChange={(event) => {
              const next = event.target.value; setParameter("target_col", next);
              setInputs(inputs.filter((column) => column !== next));
            }}>{targetOptions.map((option) => <option key={option}>{option}</option>)}</select>
          </div>
        </div>
        <div className="regression-step regression-predictor-step">
          <span className="step-number">3</span>
          <div className="step-content">
            <h3>Variables explicativas</h3><p>Selecciona las variables que quieres incluir.</p>
            <div className="regression-search"><span aria-hidden="true">⌕</span>
              <input aria-label="Buscar variables" placeholder="Buscar variables..." value={variableSearch}
                onChange={(event) => setVariableSearch(event.target.value)} /></div>
            <div className="regression-variable-table regression-config-variables">
              <div className="regression-variable-head"><span>Variable</span><span>Tipo</span><span>r</span><span>VIF</span><span>Color</span></div>
              <div className="regression-variable-list">
              {filteredInputs.map((column) => {
                const checked = inputs.includes(column);
                const r = correlations[column] ?? Number.NaN;
                const vif = checked ? vifValues[column] ?? null : null;
                return <div className={`regression-variable-row ${checked ? "selected" : ""}`} key={column}>
                  <label className="regression-variable-name"><input type="checkbox" checked={checked}
                    onChange={() => setInputs(checked ? inputs.filter((item) => item !== column) : [...inputs, column])} />
                    <span title={column}>{shortName(column)}</span></label>
                  <select aria-label={`Tipo de gráfico de ${column}`} value={chartTypes[column] ?? "Línea"}
                    onChange={(event) => setParameter("chart_types", { ...chartTypes, [column]: event.target.value })}>
                    <option>Línea</option><option>Columnas</option></select>
                  <span className="correlation-value" title={`Correlación de ${column} con ${target} en toda la serie temporal`}>
                    {Number.isFinite(r) ? `${r >= 0 ? "+" : ""}${r.toFixed(2)}` : "—"}</span>
                  <span className={`vif-value ${vif !== null && vif >= 5 ? "high" : ""}`}
                    title="Inflación de la varianza entre las variables seleccionadas">
                    {vif === null ? "—" : Number.isFinite(vif) ? vif.toFixed(1) : "∞"}</span>
                  <input className="regression-color-picker" type="color" aria-label={`Color de ${column}`}
                    value={colorFor(column)} onChange={(event) => setParameter("chart_colors",
                      { ...chartColors, [column]: event.target.value })} />
                </div>;
              })}
              {!filteredInputs.length && <p className="empty-inline">Sin variables numéricas disponibles.</p>}
              </div>
            </div>
            <p className="regression-stat-note">r utiliza toda la serie agregada por fecha. El VIF se actualiza al seleccionar variables; recomendado &lt;3.</p>
            <div className="regression-list-actions">
              <button type="button" onClick={() => setInputs(inputOptions)}>Seleccionar todas</button>
              <button type="button" onClick={() => { setInputs([]); setInteractions([]); }}>Limpiar todo</button>
            </div>
            <span className="selected-caption">Variables seleccionadas ({inputs.length})</span>
            <div className="regression-chips">
              {inputs.map((name) => <span key={name} title={name}>{shortName(name)}</span>)}
              {!inputs.length && <span className="muted">Ninguna todavía</span>}
            </div>
          </div>
        </div>
        <div className="regression-step regression-event-step">
          <span className="step-number">4</span>
          <div className="step-content">
            <h3>Eventos <span>(opcional)</span></h3><p>Marca periodos en la serie temporal.</p>
            <details className="regression-interactions">
              <summary>Interacciones (hasta 3)</summary>
              <p>Multiplica dos variables seleccionadas para añadir su efecto conjunto al modelo.</p>
              <div className="interaction-controls">
                <select aria-label="Primera variable de interacción" value={interactionLeft}
                  onChange={(event) => setInteractionLeft(event.target.value)}>
                  <option value="">Variable…</option>{availableForInteractions.map((column) => <option key={column}>{column}</option>)}</select>
                <span>×</span>
                <select aria-label="Segunda variable de interacción" value={interactionRight}
                  onChange={(event) => setInteractionRight(event.target.value)}>
                  <option value="">Variable…</option>{availableForInteractions.filter((item) => item !== interactionLeft)
                    .map((column) => <option key={column}>{column}</option>)}</select>
                <button type="button" disabled={!interactionLeft || !interactionRight || interactions.length >= 3
                  || interactions.some((item) => [item.left, item.right].sort().join("|") ===
                    [interactionLeft, interactionRight].sort().join("|"))}
                  onClick={() => { setInteractions([...interactions, { left: interactionLeft, right: interactionRight }]);
                    setInteractionLeft(""); setInteractionRight(""); }}>Añadir</button>
              </div>
              {!!interactions.length && <ul className="interaction-list">{interactions.map((item, index) =>
                <li key={`${item.left}-${item.right}`}>{item.left} × {item.right}
                  <button type="button" aria-label="Eliminar interacción"
                    onClick={() => setInteractions(interactions.filter((_, i) => i !== index))}><DeleteIcon /></button></li>)}</ul>}
            </details>
          </div>
        </div>
      </div>
    </section>

    <div className="regression-center">
      <section className="regression-card regression-explore-card">
        <div className="regression-chart-legend" aria-label="Leyenda del gráfico">
          <span className="regression-legend-item"><i style={{ background: "#0672f9" }} />{shortName(target)}</span>
          {inputs.map((column) => <span className="regression-legend-item" key={column}>
            <i style={{ background: colorFor(column) }} />{shortName(column)}</span>)}
          <small>{points.length.toLocaleString("es-ES")} fechas</small>
        </div>
        <div ref={plotContainer} className={`regression-chart-wrap ${marking ? "marking-events" : ""}`}>
          {previewLoading && <Loading label="Preparando la serie temporal…" />}
          {availableColumns.error && <div className="error" role="alert">No se pudieron leer las columnas del dataset: {availableColumns.error.message}</div>}
          {!previewLoading && preview.error && <div className="error" role="alert">No se pudo preparar el gráfico: {preview.error.message}</div>}
          {!previewLoading && !availableColumns.error && !preview.error && visiblePoints.length > 1 && <>
            {marking && <p className="event-instruction">Arrastra sobre el gráfico para seleccionar el periodo del evento.</p>}
            <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img"
              aria-label={`Serie temporal de ${target} y variables explicativas`}
              onPointerDown={(event) => { if (marking) { event.currentTarget.setPointerCapture(event.pointerId);
                const time = pointerTime(event); dragStartRef.current = time; setDragStart(time); setDragEnd(time); } }}
              onPointerMove={(event) => { if (marking && dragStartRef.current !== null) setDragEnd(pointerTime(event)); }}
              onPointerUp={finishEvent}
              onPointerCancel={() => { dragStartRef.current = null; setDragStart(null); setDragEnd(null); }}>
              {showGrid && [0, 1, 2, 3, 4, 5].map((tick) => {
                const y = TOP + (BOTTOM - TOP) * tick / 5;
                return <line key={`h-${tick}`} x1={LEFT} x2={RIGHT} y1={y} y2={y} className="regression-gridline" />;
              })}
              {showGrid && [0, 1, 2, 3, 4, 5, 6].map((tick) => {
                const x = LEFT + (RIGHT - LEFT) * tick / 6;
                return <line key={`v-${tick}`} x1={x} x2={x} y1={TOP} y2={BOTTOM} className="regression-gridline" />;
              })}
              {showEvents && events.map((item) => {
                const x1 = xAt(Date.parse(item.start)); const x2 = xAt(Date.parse(item.end + "T23:59:59"));
                if (x2 < LEFT || x1 > RIGHT) return null;
                const first = Math.max(LEFT, x1); const last = Math.min(RIGHT, x2);
                return <g key={item.id}><rect x={first} y={TOP} width={Math.max(2, last - first)}
                  height={BOTTOM - TOP} className="regression-event-shade" />
                  <line x1={first} x2={first} y1={TOP} y2={BOTTOM} className="regression-event-edge" />
                  <line x1={last} x2={last} y1={TOP} y2={BOTTOM} className="regression-event-edge" />
                </g>;
              })}
              {dragStart !== null && dragEnd !== null && <rect x={Math.max(LEFT, Math.min(xAt(dragStart), xAt(dragEnd)))}
                y={TOP} width={Math.abs(xAt(dragEnd) - xAt(dragStart))} height={BOTTOM - TOP}
                className="regression-drag-shade" />}
              <rect x={LEFT} y={TOP} width={RIGHT - LEFT} height={BOTTOM - TOP} className="regression-plot-border" />
              {[0, 1, 2, 3, 4, 5].map((tick) => {
                const value = yMax - (yMax - yMin) * tick / 5;
                const z = zMax - (zMax - zMin) * tick / 5;
                const y = TOP + (BOTTOM - TOP) * tick / 5;
                return <g key={`ticks-${tick}`}>
                  <text x={LEFT - 12} y={y + 5} textAnchor="end" className="regression-y-target">
                    {Math.round(value).toLocaleString("es-ES")}</text>
                  <text x={RIGHT + 12} y={y + 5} className="regression-y-z">{z.toFixed(1)}</text>
                </g>;
              })}
              {parameters.chart_type === "Columnas"
                ? visiblePoints.map((point) => {
                  const valueY = yAtTarget(point.target); const zeroY = yAtTarget(0);
                  return <rect key={`target-${point.time}`} x={xAt(point.time) - Math.max(1, (RIGHT - LEFT) / visiblePoints.length * .3)}
                    y={Math.min(valueY, zeroY)} width={Math.max(1, (RIGHT - LEFT) / visiblePoints.length * .6)}
                    height={Math.max(1, Math.abs(zeroY - valueY))} fill="#0672f9" opacity=".75" />;
                })
                : <path d={targetPath} fill="none" stroke="#0672f9" strokeWidth="2.9" />}
              {chartPoints.map((series) => {
                const color = colorFor(series.name);
                const mode = chartTypes[series.name] ?? "Línea";
                if (mode === "Columnas") return series.values.map((point) => <rect key={`${series.name}-${point.time}`}
                  x={xAt(point.time) - Math.max(1, (RIGHT - LEFT) / visiblePoints.length * .22)}
                  y={yAtZ(Math.max(0, point.z))} width={Math.max(1, (RIGHT - LEFT) / visiblePoints.length * .44)}
                  height={Math.abs(yAtZ(point.z) - yAtZ(0))} fill={color} opacity=".55" />);
                const path = series.values.map((point, index) =>
                  `${index ? "L" : "M"}${xAt(point.time).toFixed(1)},${yAtZ(point.z).toFixed(1)}`).join(" ");
                return <path key={series.name} d={path} fill="none" stroke={color} strokeWidth="2.2" />;
              })}
              {showEvents && events.map((item) => {
                const x = xAt(Date.parse(item.start));
                if (x < LEFT || x > RIGHT) return null;
                const tagX = Math.max(LEFT, Math.min(RIGHT - 108, x + 3));
                const tagY = TOP + 8;
                return <g key={`tag-${item.id}`}>
                  <rect x={tagX} y={tagY} width="105" height="22" rx="5" className="regression-event-tag" />
                  <text x={tagX + 4} y={tagY + 15} className="regression-event-label">{item.name.slice(0, 16)}</text>
                </g>;
              })}
              {Array.from({ length: dateTickCount }, (_, tick) => {
                const fraction = tick / (dateTickCount - 1);
                const time = timeStart + (timeEnd - timeStart) * fraction;
                const date = new Date(time);
                const label = `${String(date.getUTCDate()).padStart(2, "0")}/${String(date.getUTCMonth() + 1).padStart(2, "0")}/${String(date.getUTCFullYear()).slice(-2)}`;
                return <text key={`date-${tick}`} x={LEFT + (RIGHT - LEFT) * fraction} y={BOTTOM + 19}
                  textAnchor={tick === 0 ? "start" : tick === dateTickCount - 1 ? "end" : "middle"}
                  className="regression-date-label">{label}</text>;
              })}
              <text x="16" y={(TOP + BOTTOM) / 2} transform={`rotate(-90 16 ${(TOP + BOTTOM) / 2})`}
                className="regression-axis-label target-axis-label">{target.slice(0, 28)}</text>
              <text x={WIDTH - 14} y={(TOP + BOTTOM) / 2}
                transform={`rotate(90 ${WIDTH - 14} ${(TOP + BOTTOM) / 2})`}
                className="regression-axis-label">Variables normalizadas (z-score)</text>
            </svg>
          </>}
          {!previewLoading && !availableColumns.error && !preview.error && visiblePoints.length < 2 &&
            <p className="muted regression-no-points">No hay suficientes observaciones numéricas para dibujar la serie.</p>}
        </div>
      </section>
      <section className="regression-card regression-events-card">
        <div className="regression-card-heading"><h2>Eventos registrados ({events.length})</h2></div>
        {itsMode && events.length !== 1 && <div className="notice-bar">El método ITS requiere exactamente un evento.</div>}
        <div className="regression-event-table">
          <div className="regression-event-table-head"><span>Nombre</span><span>Desde</span><span>Hasta</span>
            <span>Días</span><span>Acciones</span></div>
          {events.map((item) => <div className="regression-event-table-row" key={item.id}>
            <strong><i />{item.name}</strong><span>{item.start}</span><span>{item.end}</span>
            <span>{eventDuration(item.start, item.end)}</span>
            <div className="regression-event-actions">
              <button type="button" aria-label={`Editar ${item.name}`} onClick={() => setEventDraft({ ...item })}>✎</button>
              <button type="button" aria-label={`Eliminar ${item.name}`}
                onClick={() => setEvents(events.filter((current) => current.id !== item.id))}><DeleteIcon /></button>
            </div>
          </div>)}
          {!events.length && <p>Marca un periodo en la serie o añade un evento con fechas.</p>}
        </div>
      </section>
    </div>

    {eventDraft && <div className="regression-event-overlay" role="presentation">
      <div className="regression-event-dialog" role="dialog" aria-modal="true" aria-label="Editar evento">
        <h2>{events.some((item) => item.id === eventDraft.id) ? "Editar evento" : "Añadir evento"}</h2>
        <p>El periodo se añadirá al modelo como variable binaria.</p>
        <label>Nombre<input autoFocus value={eventDraft.name} onChange={(event) =>
          setEventDraft({ ...eventDraft, name: event.target.value })} /></label>
        <div className="regression-event-dates">
          <label>Desde<input type="date" value={eventDraft.start} onChange={(event) =>
            setEventDraft({ ...eventDraft, start: event.target.value })} /></label>
          <label>Hasta<input type="date" value={eventDraft.end} onChange={(event) =>
            setEventDraft({ ...eventDraft, end: event.target.value })} /></label>
        </div>
        {eventDraft.start > eventDraft.end && <p className="error">La fecha final debe ser igual o posterior a la inicial.</p>}
        <div className="regression-event-dialog-actions">
          <button type="button" className="quiet-button" onClick={() => setEventDraft(null)}>Cancelar</button>
          <button type="button" className="primary" disabled={!eventDraft.name.trim() || !eventDraft.start ||
            !eventDraft.end || eventDraft.start > eventDraft.end} onClick={eventSave}>Guardar evento</button>
        </div>
      </div>
    </div>}
  </div>;
}

function DeleteIcon() {
  return <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor"
    strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M2.5 4.5h11M6 4.5V3h4v1.5M4.5 4.5l.5 8.5h6l.5-8.5M6.5 7v4M9.5 7v4" />
  </svg>;
}

function eventDuration(start: string, end: string) {
  const days = Math.round((Date.parse(end) - Date.parse(start)) / 86400000) + 1;
  if (!Number.isFinite(days) || days < 1) return "—";
  return days >= 14 && days % 7 === 0 ? `${days / 7} semanas` : `${days} días`;
}
function numberValue(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const number = typeof value === "number" ? value : Number(String(value).replace(",", "."));
  return Number.isFinite(number) ? number : null;
}

function mean(values: number[]) { return values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length); }
function standardDeviation(values: number[]) {
  const average = mean(values);
  return Math.sqrt(mean(values.map((value) => (value - average) ** 2)));
}
function correlation(a: number[], b: number[]) {
  const pairs = a.map((value, index) => [value, b[index] ?? Number.NaN] as const)
    .filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y));
  if (pairs.length < 3) return Number.NaN;
  const xs = pairs.map(([x]) => x); const ys = pairs.map(([, y]) => y);
  const mx = mean(xs); const my = mean(ys);
  const covariance = mean(xs.map((x, index) => (x - mx) * ((ys[index] ?? my) - my)));
  const denominator = standardDeviation(xs) * standardDeviation(ys);
  return denominator < 1e-12 ? Number.NaN : covariance / denominator;
}
function calculateVif(points: Point[], selected: string[]) {
  if (selected.length === 1) return { [selected[0]!]: 1 };
  if (selected.length < 2) return {} as Record<string, number>;
  const values = points.filter((point) => selected.every((name) => Number.isFinite(point.vars[name])))
    .map((point) => selected.map((name) => point.vars[name] ?? Number.NaN));
  if (values.length < selected.length + 2) return {} as Record<string, number>;
  const matrix = selected.map((_, i) => selected.map((_, j) => correlation(
    values.map((row) => row[i]!), values.map((row) => row[j]!))));
  if (matrix.flat().some((value) => !Number.isFinite(value))) return Object.fromEntries(selected.map((name) => [name, Number.POSITIVE_INFINITY]));
  const augmented = matrix.map((row, i) => [...row, ...row.map((_, j) => i === j ? 1 : 0)]);
  for (let col = 0; col < selected.length; col++) {
    let pivot = col;
    for (let row = col + 1; row < selected.length; row++) if (Math.abs(augmented[row]![col]!) > Math.abs(augmented[pivot]![col]!)) pivot = row;
    if (Math.abs(augmented[pivot]![col]!) < 1e-9) return Object.fromEntries(selected.map((name) => [name, Number.POSITIVE_INFINITY]));
    const previous = augmented[col]!;
    augmented[col] = augmented[pivot]!;
    augmented[pivot] = previous;
    const scale = augmented[col]![col]!; augmented[col] = augmented[col]!.map((value) => value / scale);
    for (let row = 0; row < selected.length; row++) if (row !== col) {
      const factor = augmented[row]![col]!;
      augmented[row] = augmented[row]!.map((value, index) => value - factor * augmented[col]![index]!);
    }
  }
  return Object.fromEntries(selected.map((name, i) => [name, Math.max(1, augmented[i]![selected.length + i]!)]));
}
function makeChartPoints(points: Point[], inputs: string[]) {
  return inputs.map((name) => {
    const vals = points.flatMap((point) => Number.isFinite(point.vars[name]) ? [point.vars[name]!] : []);
    const average = mean(vals); const sd = standardDeviation(vals) || 1;
    return { name, values: points.flatMap((point) => {
      const value = point.vars[name];
      return Number.isFinite(value) ? [{ time: point.time, z: (value! - average) / sd }] : [];
    }) };
  });
}
function closestPoint(points: Point[], time: number) {
  if (!points.length) return undefined;
  return points.reduce((best, point) => Math.abs(point.time - time) < Math.abs(best.time - time) ? point : best, points[0]!);
}
