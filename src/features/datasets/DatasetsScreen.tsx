import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../shared/api";
import { useUiStore } from "../../app/store";
import { DataPreview } from "./DataPreview";

export function DatasetsScreen() {
  const client = useQueryClient();
  const { activeDatasetId, setActiveDataset } = useUiStore();
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: api.listDatasets });
  const load = useMutation({
    mutationFn: async () => {
      const path = await api.selectDataset();
      return path ? api.loadDataset(path) : null;
    },
    onSuccess: (data) => {
      if (!data) return;
      setActiveDataset(data.dataset_id);
      void client.invalidateQueries({ queryKey: ["datasets"] });
    },
  });
  const active = datasets.data?.find((item) => item.dataset_id === activeDatasetId) ?? datasets.data?.[0];

  return <section>
    <header className="page-header"><div><p className="eyebrow">Workspace</p><h1>Datos</h1>
      <p>Carga, inspecciona y prepara datasets sin enviarlos fuera del equipo.</p></div>
      <button className="primary" onClick={() => load.mutate()} disabled={load.isPending}>
        {load.isPending ? "Cargando…" : "Cargar archivo"}
      </button></header>
    {load.error && <div className="error" role="alert">{load.error.message}</div>}
    {!active && !datasets.isLoading && <div className="empty"><h2>Aún no hay datasets</h2>
      <p>Abre un CSV, Excel o Parquet para empezar.</p></div>}
    {active && <><div className="cards">
      <article><span>Dataset activo</span><strong>{active.name}</strong></article>
      <article><span>Filas</span><strong>{active.rows.toLocaleString("es-ES")}</strong></article>
      <article><span>Columnas</span><strong>{active.columns.length}</strong></article>
      <article><span>Motor</span><strong>{active.uses_disk ? "DuckDB · disco" : "Pandas · memoria"}</strong></article>
    </div><DataPreview datasetId={active.dataset_id} totalRows={active.rows} /></>}
  </section>;
}
