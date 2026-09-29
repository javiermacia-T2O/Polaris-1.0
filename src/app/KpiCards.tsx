import { useQuery } from "@tanstack/react-query";
import { api } from "../shared/api";
import { useUiStore } from "./store";

const NUMERIC_TYPE = /int|float|double|decimal|numeric|number|real|bigint|smallint|hugeint/i;

export function KpiCards() {
  const activeDatasetId = useUiStore((state) => state.activeDatasetId);
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: api.listDatasets });
  const active = datasets.data?.find((item) => item.dataset_id === activeDatasetId) ?? datasets.data?.[0];

  // Prefer the types already present in the dataset metadata so the KPI row
  // never depends on a second round-trip (which used to time out on huge
  // datasets and left the counters at zero).
  const types = active?.types ?? [];
  const numeric = types.filter((type) => NUMERIC_TYPE.test(type)).length;
  const categorical = Math.max(0, (active?.columns.length ?? 0) - numeric);

  const specs = [
    { label: "Filas", value: active ? active.rows.toLocaleString("es-ES") : "—", tone: "primary" },
    { label: "Columnas", value: active ? active.columns.length : "—", tone: "purple" },
    { label: "Numéricas", value: active ? numeric : "—", tone: "success" },
    { label: "Categóricas", value: active ? categorical : "—", tone: "warning" },
    { label: "Motor", value: active ? (active.uses_disk ? "DuckDB" : "Pandas") : "—", tone: "primary" },
  ];

  return <div className="kpi-row">
    {specs.map((spec) => <article key={spec.label} className={`kpi-card tone-${spec.tone}`}>
      <span>{spec.label.toUpperCase()}</span>
      <strong>{spec.value}</strong>
    </article>)}
  </div>;
}