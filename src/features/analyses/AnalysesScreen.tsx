import { useMemo, useState } from "react";
import { useAnalysis, schemaFields } from "../../app/AnalysisContext";
import { useUiStore } from "../../app/store";
import { Loading } from "../../shared/Loading";
import { CheckboxList } from "../../shared/CheckboxList";

export function AnalysesScreen() {
  const datasetId = useUiStore((state) => state.activeDatasetId);
  const { analyses, analysesLoading, analysesError, selected, selectAnalysis,
    manifest, manifestLoading, manifestError, parameters, setParameter, run,
    runPending, runError, job, result, openResults } = useAnalysis();
  const [search, setSearch] = useState("");
  const filtered = analyses.filter((analysis) =>
    `${analysis.name} ${analysis.category} ${analysis.description}`.toLocaleLowerCase("es")
      .includes(search.toLocaleLowerCase("es")));
  const groups = useMemo(() => {
    const map = new Map<string, typeof filtered>();
    for (const analysis of filtered) {
      const list = map.get(analysis.category) ?? [];
      list.push(analysis);
      map.set(analysis.category, list);
    }
    return [...map.entries()];
  }, [filtered]);
  const fields = schemaFields(manifest?.parameter_schema);
  const status = job?.state;
  const format = selected?.table_format;

  return <section>
    <header className="page-header">
      <div><p className="eyebrow">ANÁLISIS</p><h1>Análisis</h1>
        <p>Elige un método y configura la estimación para el dataset activo.</p></div>
      <input className="catalog-search" aria-label="Buscar análisis" placeholder="Buscar análisis…"
        value={search} onChange={(event) => setSearch(event.target.value)} />
    </header>

    {!datasetId && <div className="empty"><h2>Selecciona primero un dataset</h2>
      <p>La configuración se adapta a sus columnas.</p></div>}
    {analysesLoading && <Loading label="Leyendo catálogo…" />}
    {analysesError && <div className="error" role="alert">No se pudo cargar el catálogo.
      <details><summary>Detalles técnicos</summary><pre>{analysesError.message}</pre></details></div>}

    {datasetId && <div className="analysis-workspace">
      <nav className="analysis-nav" aria-label="Catálogo de análisis">
        {groups.map(([category, items]) => <div className="analysis-nav-group" key={category}>
          <p className="analysis-nav-cat">{category}</p>
          {items.map((analysis) => <button key={analysis.id} type="button"
            className={`analysis-nav-item ${selected?.id === analysis.id ? "active" : ""}`}
            onClick={() => selectAnalysis(analysis.id)}>
            <span className="analysis-nav-name">{analysis.name}</span>
            <span className="analysis-nav-desc">{analysis.description}</span>
          </button>)}
        </div>)}
        {!filtered.length && <p className="muted">Sin coincidencias.</p>}
      </nav>

      <div className="analysis-detail">
        {!selected && <div className="empty"><h2>Selecciona un análisis</h2>
          <p>Elige un método en la lista para ver su formato de tabla y configuración.</p></div>}

        {selected && <article className="panel form-panel">
          <div className="panel-head"><div><h2>{selected.name}</h2>
            <p>{selected.category} · configuración del motor Python.</p></div></div>
          {selected.caution && <div className="notice-bar">{selected.caution}</div>}

          <div className="format-hint">
            <span className="format-hint-title">Formato tabla:</span>
            <span className="format-hint-body">{format?.summary ?? "—"}</span>
            {format?.notes && <span className="format-hint-note">{format.notes}</span>}
          </div>

          {manifestLoading && <Loading label="Preparando esquema…" inline />}
          {manifestError && <div className="error" role="alert">No se pudo preparar el análisis.
            <details><summary>Detalles técnicos</summary><pre>{manifestError.message}</pre></details></div>}
          {manifest?.parameter_schema && <div className="fields">
            {fields.map((field, index) => field.type === "group" ?
              <h3 className="field-group" key={`${field.label}-${index}`}>{field.label}</h3> : field.key ?
                <AnalysisField key={field.key} field={field} value={parameters[field.key]}
                  onChange={(value) => setParameter(field.key!, value)} /> : null)}
            {fields.length === 0 && <p className="muted">Este análisis no requiere parámetros adicionales.</p>}
          </div>}
          {runError && <div className="error" role="alert">No se pudo iniciar el análisis.
            <details><summary>Detalles técnicos</summary><pre>{runError.message}</pre></details></div>}
          <div className="run-actions">
            <button className="primary" onClick={run}
              disabled={manifestLoading || Boolean(manifestError) || runPending ||
                Boolean(job && !["COMPLETED", "FAILED", "CANCELLED"].includes(status ?? ""))}>
              {runPending ? "Enviando…" : job ? "Ejecutar otra vez" : "Ejecutar análisis"}</button>
          </div>
        </article>}

        {selected && <article className="panel job-panel">
          <div className="panel-head"><div><h2>Ejecución</h2>
            <p>{status ? statusLabel(status) : "En espera"}</p></div>
            {job && <strong>{job.progress}%</strong>}</div>
          {job && <progress value={job.progress} max="100" aria-label="Progreso del análisis" />}
          {job && <div className="job-detail"><p>{job.message || statusLabel(job.state)}</p>
            {job.diagnostics.map((item, index) => <p className="muted" key={`${index}-${item}`}>{item}</p>)}
            {job.error && <div className="error" role="alert">{job.error.message}
              <details><summary>Detalles técnicos</summary><pre>{JSON.stringify(job.error.details, null, 2)}</pre></details></div>}
          </div>}
          {result && <div className="result-ready">
            <span>{result.tables.length} tablas · {result.charts.length} gráficos</span>
            <button className="primary" onClick={openResults}>Ver resultados</button></div>}
        </article>}
      </div>
    </div>}
  </section>;
}

function AnalysisField({ field, value, onChange }: {
  field: ReturnType<typeof schemaFields>[number];
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const label = field.label ?? field.key ?? "Parámetro";
  const options = field.options?.map(String) ?? [];
  if (field.type === "select") return <label>{label}
    <select value={String(value ?? "")} onChange={(event) => onChange(event.target.value)}>
      {field.default == null && <option value="">Seleccionar…</option>}
      {options.map((option) => <option key={option}>{option}</option>)}</select>
    {field.hint && <small>{field.hint}</small>}</label>;
  if (field.type === "multi") return <div className="field-multi"><span className="field-label">{label}</span>
    <CheckboxList options={options} selected={Array.isArray(value) ? value.map(String) : []}
      onChange={onChange} emptyLabel="Sin opciones disponibles." maxHeight={180} />
    {field.hint && <small>{field.hint}</small>}</div>;
  if (field.type === "checkbox" || field.type === "boolean") return <label className="check-field">
    <input type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} />
    {label}{field.hint && <small>{field.hint}</small>}</label>;
  const number = field.type === "number";
  return <label>{label}<input type={number ? "number" : field.type === "date" ? "date" : "text"}
    min={field.min} max={field.max} step={number ? "any" : undefined}
    value={String(value ?? "")} onChange={(event) => onChange(number
      ? (event.target.value === "" ? null : Number(event.target.value)) : event.target.value)} />
    {field.hint && <small>{field.hint}</small>}</label>;
}

function statusLabel(state: string) {
  return ({ QUEUED: "Esperando turno", RUNNING: "Ejecutando", COMPLETED: "Completado",
    FAILED: "Error", CANCELLED: "Cancelado" } as Record<string, string>)[state] ?? state;
}