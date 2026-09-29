import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useUiStore } from "../../app/store";
import { api } from "../../shared/api";
import { Loading } from "../../shared/Loading";

export function ChartsScreen() {
  const resultId = useUiStore((state) => state.activeResultId);
  const summary = useQuery({ queryKey: ["analysis-result", resultId],
    queryFn: () => api.analysisResult(resultId!), enabled: Boolean(resultId) });
  const [chartName, setChartName] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    setChartName(summary.data?.charts[0] ?? "");
  }, [summary.data]);

  const chart = useQuery({ queryKey: ["result-chart", resultId, chartName],
    queryFn: () => api.resultChart(resultId!, chartName),
    enabled: Boolean(resultId && chartName) });

  const saveChart = useMutation({
    mutationFn: async () => {
      if (!chart.data) return null;
      return api.saveChart(`${chartName || "grafico"}.png`, chart.data.data_base64);
    },
    onSuccess: (path) => setNotice(path ? `Gráfico guardado en ${path}` : ""),
  });

  const saveAllCharts = useMutation({
    mutationFn: async () => {
      const names = summary.data?.charts ?? [];
      let saved = 0;
      for (const name of names) {
        const artifact = await api.resultChart(resultId!, name);
        const path = await api.saveChart(`${name}.png`, artifact.data_base64);
        if (path) saved += 1;
      }
      return saved;
    },
    onSuccess: (saved) => setNotice(saved ? `${saved} gráfico(s) guardado(s).` : ""),
  });

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
      {(saveChart.error || saveAllCharts.error) && <div className="error" role="alert">
        No se pudo guardar el gráfico: {(saveChart.error || saveAllCharts.error)?.message}</div>}
      <article className="panel result-chart-panel">
        <div className="panel-head"><div><h2>Gráfico</h2>
          <p>{summary.data.charts.length} figura(s) disponibles.</p></div>
          <div className="panel-actions">
            <select value={chartName} onChange={(event) => setChartName(event.target.value)}>
              {summary.data.charts.map((item) => <option key={item}>{item}</option>)}</select>
            <button className="quiet-button" onClick={() => saveChart.mutate()}
              disabled={saveChart.isPending || !chart.data}>Guardar PNG</button>
            {summary.data.charts.length > 1 && <button className="quiet-button"
              onClick={() => saveAllCharts.mutate()} disabled={saveAllCharts.isPending}>
              {saveAllCharts.isPending ? "Guardando…" : "Guardar todos"}</button>}
          </div>
        </div>
        {chart.isLoading && <Loading label="Renderizando gráfico…" />}
        {chart.error && <div className="error" role="alert">No se pudo representar el gráfico: {chart.error.message}</div>}
        {chart.data && <img className="result-chart" src={`data:${chart.data.mime_type};base64,${chart.data.data_base64}`} alt={chart.data.chart} />}
      </article>
    </>}
  </section>;
}