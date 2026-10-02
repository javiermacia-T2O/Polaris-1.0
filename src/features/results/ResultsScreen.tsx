import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useUiStore } from "../../app/store";
import { api } from "../../shared/api";
import { Loading } from "../../shared/Loading";
import { ExportAnalysisDialog, type ExportKind } from "./ExportAnalysisDialog";

const PAGE_SIZE = 100;
const SUMMARY = "__summary__";
const tableLabels: Record<string, string> = {
  "Validación temporal (OOS)": "Validación",
  "Validación temporal (rolling OOS)": "Validación temporal",
  "Métricas in-sample (diagnóstico)": "Ajuste",
  "Coeficientes (con IC 95/90/85%)": "Coeficientes",
  "Resumen de contribución por variable": "Contribuciones",
  "Calidad del resultado": "Calidad",
  "Comparativa temporal de modelos": "Modelos",
  "Datos con anomalías y predicciones": "Predicciones",
  "Nota metodológica": "Notas",
};

function displayValue(value: unknown): string {
  if (value == null || value === "") return "—";
  if (typeof value === "number") return Number.isFinite(value)
    ? new Intl.NumberFormat("es-ES", { maximumFractionDigits: 4 }).format(value) : "—";
  if (typeof value === "boolean") return value ? "Sí" : "No";
  return String(value);
}

export function ResultsScreen() {
  const resultId = useUiStore((state) => state.activeResultId);
  const summary = useQuery({ queryKey: ["analysis-result", resultId],
    queryFn: () => api.analysisResult(resultId!), enabled: Boolean(resultId) });
  const [activeTab, setActiveTab] = useState(SUMMARY);
  const [offset, setOffset] = useState(0);
  const [notice, setNotice] = useState("");
  const [exportKind, setExportKind] = useState<ExportKind | null>(null);
  useEffect(() => { setActiveTab(SUMMARY); setOffset(0); }, [resultId]);
  const tableName = activeTab === SUMMARY ? "" : activeTab;
  const table = useQuery({ queryKey: ["result-table", resultId, tableName, offset],
    queryFn: () => api.resultTable(resultId!, tableName, offset, PAGE_SIZE),
    enabled: Boolean(resultId && tableName) });
  if (!resultId) return <section><div className="empty"><h2>Aún no hay resultados</h2>
    <p>Ejecuta un análisis para explorar sus tablas.</p></div></section>;

  const metrics = Object.entries(summary.data?.scalars ?? {}).filter(([label, value]) =>
    typeof value === "number" || ["Estado", "Modelo solicitado", "Modelo ajustado",
      "quality_status", "Correlación real-predicho"].includes(label));
  return <section className="results-screen">
    {summary.isLoading && <Loading label="Cargando resultados…" />}
    {summary.error && <div className="error" role="alert">No se pudo abrir el resultado: {summary.error.message}</div>}
    {summary.data && <>
      <header className="results-header"><h1>Resultados</h1>
        <p>{summary.data.tables.length} tablas · {summary.data.charts.length} gráficos</p></header>
      <div className="result-navigation"><nav className="result-tabs" aria-label="Secciones de resultados">
        <button type="button" className={activeTab === SUMMARY ? "active" : ""}
          aria-current={activeTab === SUMMARY ? "page" : undefined}
          onClick={() => { setActiveTab(SUMMARY); setOffset(0); }}>Resumen</button>
        {summary.data.tables.map((name) => <button key={name} type="button"
          className={activeTab === name ? "active" : ""}
          aria-current={activeTab === name ? "page" : undefined}
          title={name} onClick={() => { setActiveTab(name); setOffset(0); }}>
          {tableLabels[name] ?? name}</button>)}
      </nav><button type="button" className="quiet-button result-save-all"
        onClick={() => setExportKind("all_results")}>Guardar todos los resultados</button></div>
      {notice && <div className="notice-bar" role="status">{notice}</div>}
      {activeTab === SUMMARY && <article className="panel result-section">
        <div className="panel-head"><h2>Indicadores del modelo</h2>
          <button className="quiet-button" onClick={() => setExportKind("metrics")}>Guardar CSV</button></div>
        {metrics.length ? <div className="result-table-scroll"><table className="result-data-table result-summary-table">
          <thead><tr><th>Métrica</th><th>Valor</th></tr></thead>
          <tbody>{metrics.map(([label, value]) => <tr key={label}><th scope="row">
            {label === "quality_status" ? "Calidad" : label}</th><td>{displayValue(value)}</td></tr>)}</tbody>
        </table></div> : <div className="empty"><p>No hay indicadores para este análisis.</p></div>}
      </article>}
      {tableName && <article className="panel result-section">
        <div className="panel-head"><div><h2>{tableName}</h2>
          <p>{table.data?.total_rows.toLocaleString("es-ES") ?? ""} filas</p></div>
          <div className="panel-actions">
            <button className="quiet-button" onClick={() => setExportKind("table")}>Guardar CSV</button>
          </div></div>
        {table.isLoading && <Loading label="Cargando tabla…" />}
        {table.error && <div className="error" role="alert">No se pudo cargar la tabla: {table.error.message}</div>}
        {table.data && <><div className="result-table-scroll"><table className="result-data-table">
          <thead><tr>{table.data.columns.map((column) => <th key={column} scope="col">{column}</th>)}</tr></thead>
          <tbody>{table.data.rows.map((row, index) => <tr key={offset + index}>
            {table.data!.columns.map((column) => <td key={column}>{displayValue(row[column])}</td>)}
          </tr>)}</tbody></table></div>
          {table.data.total_rows > PAGE_SIZE && <div className="result-pager"><span>
            {offset + 1}–{Math.min(table.data.total_rows, offset + PAGE_SIZE)} / {table.data.total_rows.toLocaleString("es-ES")}</span>
            <button onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))} disabled={offset === 0}>Anterior</button>
            <button onClick={() => setOffset(offset + PAGE_SIZE)} disabled={offset + PAGE_SIZE >= table.data.total_rows}>Siguiente</button>
          </div>}</>}
      </article>}
    </>}
    {exportKind && <ExportAnalysisDialog resultId={resultId} kind={exportKind}
      name={exportKind === "table" ? tableName : undefined}
      onClose={() => setExportKind(null)} onSaved={setNotice} />}
  </section>;
}
