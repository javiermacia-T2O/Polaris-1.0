import { useCallback, useState } from "react";
import { useAnalysis, schemaFields } from "../../app/AnalysisContext";
import { useUiStore } from "../../app/store";
import { Loading } from "../../shared/Loading";
import { CheckboxList } from "../../shared/CheckboxList";
import { RegressionWorkbench } from "./RegressionWorkbench";

export function AnalysesScreen() {
  const datasetId = useUiStore((state) => state.activeDatasetId);
  const { selected,
    manifest, manifestLoading, manifestError, parameters, setParameter, run,
    runPending, runError, job, result, openResults } = useAnalysis();
  const [saved, setSaved] = useState(false);
  const setRegressionParameter = useCallback((key: string, value: unknown) => {
    setSaved(false);
    setParameter(key, value);
  }, [setParameter]);
  const fields = schemaFields(manifest?.parameter_schema);
  const status = job?.state;
  const format = selected?.table_format;

  if (datasetId && selected?.id === "regression") return <section className="regression-page">
    <header className="regression-page-header">
      <div><h1>{selected.name}</h1>
        <p>Analiza el impacto de tus variables de marketing mediante modelos de regresión con descomposición de contribuciones.</p></div>
      <details className="regression-save-menu">
        <summary className="quiet-button regression-save">▣ &nbsp; {saved ? "Análisis guardado" : "Guardar análisis"} <span>⌄</span></summary>
        <div><button type="button" onClick={() => {
          window.localStorage.setItem(`polaris-regression-${datasetId}`, JSON.stringify(parameters));
          setSaved(true);
        }}>Guardar configuración</button>
          <button type="button" onClick={() => {
            const raw = window.localStorage.getItem(`polaris-regression-${datasetId}`);
            if (!raw) return;
            try {
              const previous = JSON.parse(raw) as Record<string, unknown>;
              Object.entries(previous).forEach(([key, value]) => setParameter(key, value));
              setSaved(true);
            } catch { setSaved(false); }
          }}>Recuperar última configuración</button></div>
      </details>
    </header>
    {manifestLoading && <Loading label="Preparando esquema…" inline />}
    {manifestError && <div className="error" role="alert">No se pudo preparar el análisis: {manifestError.message}</div>}
    {manifest?.parameter_schema && <RegressionWorkbench datasetId={datasetId} fields={fields} parameters={parameters}
      setParameter={setRegressionParameter} run={run} runPending={runPending}
      runDisabled={manifestLoading || Boolean(manifestError) || Boolean(job &&
        !["COMPLETED", "FAILED", "CANCELLED"].includes(status ?? ""))} />}
    {runError && <div className="error" role="alert">No se pudo iniciar el análisis: {runError.message}</div>}
    {job && <div className="regression-job-status" role="status"><span>{statusLabel(job.state)} · {job.progress}%</span>
      <span>{job.message}</span>{result && <button type="button" className="primary" onClick={openResults}>Ver resultados</button>}</div>}
  </section>;

  if (!datasetId || !selected) return <section className="analysis-empty-page">
    <div className="analysis-empty-state"><span aria-hidden="true">▤</span>
      <h1>{datasetId ? "Selecciona un análisis" : "Selecciona primero un dataset"}</h1>
      <p>{datasetId ? "Elige un análisis en la barra lateral para configurar y ejecutar el modelo." :
        "Abre un archivo en la barra lateral para comenzar."}</p></div>
  </section>;

  return <section className="analysis-method-page">
    <header className="page-header"><div><p className="eyebrow">ANÁLISIS</p><h1>{selected.name}</h1>
      <p>{selected.description}</p></div></header>
        <article className="panel form-panel">
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
          {selected.id !== "regression" && manifest?.parameter_schema && <div className="fields">
            {fields.map((field, index) => field.type === "group" ?
              <h3 className="field-group" key={`${field.label}-${index}`}>{field.label}</h3> : field.key ?
                <AnalysisField key={field.key} field={field} value={parameters[field.key]}
                  onChange={(value) => setParameter(field.key!, value)} /> : null)}
            {fields.length === 0 && <p className="muted">Este análisis no requiere parámetros adicionales.</p>}
          </div>}
          {runError && <div className="error" role="alert">No se pudo iniciar el análisis.
            <details><summary>Detalles técnicos</summary><pre>{runError.message}</pre></details></div>}
          {selected.id !== "regression" && <div className="run-actions">
            <button className="primary" onClick={run}
              disabled={manifestLoading || Boolean(manifestError) || runPending ||
                Boolean(job && !["COMPLETED", "FAILED", "CANCELLED"].includes(status ?? ""))}>
              {runPending ? "Enviando…" : job ? "Ejecutar otra vez" : "Ejecutar análisis"}</button>
          </div>}
        </article>

        <article className="panel job-panel">
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
        </article>
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
