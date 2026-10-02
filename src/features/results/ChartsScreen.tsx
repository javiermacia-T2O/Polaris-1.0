import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useUiStore } from "../../app/store";
import { api } from "../../shared/api";
import { Loading } from "../../shared/Loading";
import { ExportAnalysisDialog, type ExportKind } from "./ExportAnalysisDialog";

export function ChartsScreen() {
  const resultId = useUiStore((state) => state.activeResultId);
  const summary = useQuery({ queryKey: ["analysis-result", resultId],
    queryFn: () => api.analysisResult(resultId!), enabled: Boolean(resultId) });
  const [chartIndex, setChartIndex] = useState(0);
  const [notice, setNotice] = useState("");
  const [exportKind, setExportKind] = useState<ExportKind | null>(null);

  useEffect(() => {
    setChartIndex(0);
  }, [summary.data]);

  const chartName = summary.data?.charts[chartIndex] ?? "";

  const chart = useQuery({ queryKey: ["result-chart", resultId, chartName],
    queryFn: () => api.resultChart(resultId!, chartName),
    enabled: Boolean(resultId && chartName) });
  const isFitChart = summary.data?.analysis_id === "regression" && chartName.startsWith("Ajuste");
  const fitMetrics = useQuery({ queryKey: ["result-fit-metrics", resultId],
    queryFn: () => api.resultTable(resultId!, "Métricas in-sample (diagnóstico)", 0, 30),
    enabled: Boolean(resultId && isFitChart && summary.data?.tables.includes("Métricas in-sample (diagnóstico)")) });

  if (!resultId) return <section>
    <div className="empty"><h2>Aún no hay gráficos</h2><p>Ejecuta un análisis para generar visualizaciones.</p></div>
  </section>;

  return <section>
    {summary.isLoading && <Loading label="Cargando gráficos…" />}
    {summary.error && <div className="error" role="alert">No se pudo abrir el resultado: {summary.error.message}</div>}
    {summary.data && summary.data.charts.length === 0 &&
      <div className="empty"><h2>Este resultado no incluye gráficos</h2><p>Consulta la pestaña Resultados.</p></div>}
    {summary.data && summary.data.charts.length > 0 && <>
      {notice && <div className="notice-bar" role="status">{notice}</div>}
      <article className="panel result-chart-panel">
        <div className="panel-head"><div><h2>{chartName}</h2>
          <p>{chartIndex + 1} de {summary.data.charts.length}</p></div>
          <div className="panel-actions">
            <button className="quiet-button" aria-label="Gráfico anterior"
              onClick={() => setChartIndex((index) => Math.max(0, index - 1))}
              disabled={chartIndex === 0}>←</button>
            <button className="quiet-button" aria-label="Gráfico siguiente"
              onClick={() => setChartIndex((index) => Math.min(summary.data!.charts.length - 1, index + 1))}
              disabled={chartIndex >= summary.data.charts.length - 1}>→</button>
          </div>
        </div>
        <div className="result-navigation"><nav className="result-chart-tabs" aria-label="Navegar por los gráficos">
          {summary.data.charts.map((name, index) => <button key={name} type="button"
            className={index === chartIndex ? "active" : ""}
            onClick={() => setChartIndex(index)}>{index + 1}. {name}</button>)}
        </nav><div className="result-save-actions">
          <button type="button" className="quiet-button" onClick={() => setExportKind("chart")}>Guardar PNG</button>
          <button type="button" className="quiet-button" onClick={() => setExportKind("all_charts")}>Guardar todos los gráficos</button>
        </div></div>
        {chart.isLoading && <Loading label="Renderizando gráfico…" />}
        {chart.error && <div className="error" role="alert">No se pudo representar el gráfico: {chart.error.message}</div>}
        {chart.data && <div className="result-chart-body">
          <img className="result-chart" src={`data:${chart.data.mime_type};base64,${chart.data.data_base64}`} alt={chart.data.chart} />
          {isFitChart && <aside className="result-chart-metrics"><h3>Métricas de ajuste</h3>
            {fitMetrics.isLoading && <Loading label="Cargando métricas…" inline />}
            {fitMetrics.data && <table className="result-data-table"><thead><tr><th>Métrica</th><th>Valor</th></tr></thead>
              <tbody>{fitMetrics.data.rows.map((row, index) => <tr key={index}>
                <th scope="row">{String(row["Métrica"] ?? "—")}</th>
                <td>{typeof row["Valor"] === "number" ? Number(row["Valor"]).toLocaleString("es-ES", { maximumFractionDigits: 4 }) : String(row["Valor"] ?? "—")}</td>
              </tr>)}</tbody></table>}
          </aside>}
        </div>}
      </article>
    </>}
    {exportKind && <ExportAnalysisDialog resultId={resultId} kind={exportKind}
      name={exportKind === "chart" ? chartName : undefined}
      onClose={() => setExportKind(null)} onSaved={setNotice} />}
  </section>;
}
