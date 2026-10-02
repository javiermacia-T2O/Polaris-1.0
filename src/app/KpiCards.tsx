import { useQuery } from "@tanstack/react-query";
import { api } from "../shared/api";
import { useAnalysis } from "./AnalysisContext";
import { useUiStore } from "./store";

const NUMERIC_TYPE = /int|float|double|decimal|numeric|number|real|bigint|smallint|hugeint/i;

export function KpiCards() {
  const activeDatasetId = useUiStore((state) => state.activeDatasetId);
  const activeResultId = useUiStore((state) => state.activeResultId);
  const screen = useUiStore((state) => state.screen);
  const regressionOverview = useUiStore((state) => state.regressionOverview);
  const { analyses, selected, job, result } = useAnalysis();
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: api.listDatasets });
  const outputResult = useQuery({ queryKey: ["analysis-result", activeResultId],
    queryFn: () => api.analysisResult(activeResultId!),
    enabled: Boolean(activeResultId && (screen === "resultados" || screen === "graficos")) });
  const shownResult = outputResult.data ?? (result?.result_id === activeResultId ? result : undefined);
  const resultAnalysisName = analyses.find((item) => item.id === shownResult?.analysis_id)?.name
    ?? (selected && shownResult && selected.id === shownResult.analysis_id ? selected.name : "—");
  const active = datasets.data?.find((item) => item.dataset_id === activeDatasetId) ?? datasets.data?.[0];

  // Prefer the types already present in the dataset metadata so the KPI row
  // never depends on a second round-trip (which used to time out on huge
  // datasets and left the counters at zero).
  const types = active?.types ?? [];
  const numeric = types.filter((type) => NUMERIC_TYPE.test(type)).length;
  const categorical = Math.max(0, (active?.columns.length ?? 0) - numeric);

  const datasetSpecs = [
    { label: "Filas", icon: "▤", value: active
      ? active.rows_approximate
        ? active.rows > 0 ? `~${active.rows.toLocaleString("es-ES")}` : "Contando…"
        : active.rows.toLocaleString("es-ES")
      : "—", tone: "primary" },
    { label: "Columnas", icon: "▥", value: active ? active.columns.length : "—", tone: "purple" },
    { label: "Numéricas", icon: "▴", value: active ? numeric : "—", tone: "success" },
    { label: "Categóricas", icon: "✧", value: active ? categorical : "—", tone: "warning" },
    { label: "Motor", icon: "⚙", value: active ? (active.uses_disk ? "DuckDB" : "Pandas") : "—", tone: "primary" },
  ];
  const analysisSpecs = screen === "analisis" && selected?.id === "regression" ? [
    { label: "Variables activas", icon: "▥", value: regressionOverview?.datasetId === activeDatasetId
      ? `${regressionOverview.active} / ${regressionOverview.total}` : "—", tone: "primary" },
    { label: "Correlación media", icon: "∿", value: regressionOverview?.datasetId === activeDatasetId
      && regressionOverview.correlation !== null ? regressionOverview.correlation.toFixed(2) : "—", tone: "purple" },
    { label: "VIF máximo", icon: "≋", value: regressionOverview?.datasetId === activeDatasetId
      && regressionOverview.vif !== null
      ? Number.isFinite(regressionOverview.vif) ? regressionOverview.vif.toFixed(1) : "∞" : "—", tone: "success" },
    { label: "Eventos creados", icon: "▣", value: regressionOverview?.datasetId === activeDatasetId
      ? regressionOverview.events : 0, tone: "warning" },
    { label: "Estimación", icon: "⚙", value: regressionOverview?.datasetId === activeDatasetId
      ? regressionOverview.method : "—", tone: "primary" },
  ] : screen === "analisis" && selected ? [
    { label: "Análisis", icon: "▥", value: selected.name, tone: "primary" },
    { label: "Categoría", icon: "◈", value: selected.category, tone: "purple" },
    { label: "Filas disponibles", icon: "▤", value: active ? active.rows.toLocaleString("es-ES") : "—", tone: "success" },
    { label: "Columnas disponibles", icon: "▥", value: active ? active.columns.length : "—", tone: "warning" },
    { label: "Estado", icon: "⚙", value: job?.state === "COMPLETED" ? "Completado"
      : job?.state === "RUNNING" ? "En ejecución" : "Por configurar", tone: "primary" },
  ] : null;
  const outputSpecs = (screen === "resultados" || screen === "graficos") ? [
    { label: "Análisis", icon: "▥", value: resultAnalysisName, tone: "primary" },
    { label: "Tablas", icon: "▤", value: shownResult?.tables.length ?? 0, tone: "purple" },
    { label: "Gráficos", icon: "∿", value: shownResult?.charts.length ?? 0, tone: "success" },
    { label: "Indicadores", icon: "◈", value: shownResult ? Object.keys(shownResult.scalars).length : 0, tone: "warning" },
    { label: "Estado", icon: "⚙", value: shownResult ? "Listo" : "—", tone: "primary" },
  ] : null;
  const diagnosticSpecs = screen === "diagnostico" ? [
    { label: "Dataset", icon: "▥", value: active?.name ?? "—", tone: "primary" },
    { label: "Filas", icon: "▤", value: active ? active.rows.toLocaleString("es-ES") : "—", tone: "purple" },
    { label: "Columnas", icon: "▥", value: active?.columns.length ?? "—", tone: "success" },
    { label: "Motor", icon: "⚙", value: active ? active.uses_disk ? "DuckDB" : "Pandas" : "—", tone: "warning" },
    { label: "Estado", icon: "◈", value: active ? "Disponible" : "Sin dataset", tone: "primary" },
  ] : null;
  const specs = analysisSpecs ?? outputSpecs ?? diagnosticSpecs ?? datasetSpecs;

  return <div className="kpi-row">
    {specs.map((spec) => <article key={spec.label} className={`kpi-card tone-${spec.tone}`} title={String(spec.value)}>
      <i className="kpi-icon" aria-hidden="true">{spec.icon}</i>
      <span>{spec.label.toUpperCase()}</span>
      <strong>{spec.value}</strong>
    </article>)}
  </div>;
}
