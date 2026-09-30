import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../shared/api";
import { useUiStore } from "../../app/store";
import { DataPreview } from "./DataPreview";
import { MergePanel } from "./MergePanel";
import { TableScreen } from "../tables/TableScreen";
import { Loading } from "../../shared/Loading";

function guessType(type: string) {
  if (/int|float|double|decimal|numeric|number/i.test(type)) return "numero";
  if (/date|time|timestamp/i.test(type)) return "fecha";
  if (/bool|category|object|string/i.test(type)) return "categorica";
  return "texto";
}

export function DatasetsScreen() {
  const client = useQueryClient();
  const { activeDatasetId, setActiveDataset, setStatus, columnTypes,
    setColumnTypes, setColumnType } = useUiStore();
  const [previewKey, setPreviewKey] = useState(0);
  const typedDatasetId = useRef<string | null>(null);
  const datasets = useQuery({
    queryKey: ["datasets"], queryFn: api.listDatasets,
    refetchInterval: (query) => (query.state.data ?? []).some(
      (item) => item.rows_approximate) ? 1000 : false,
  });
  const active = datasets.data?.find((item) => item.dataset_id === activeDatasetId)
    ?? datasets.data?.[0];
  const columns = useQuery({
    queryKey: ["columns", active?.dataset_id],
    queryFn: () => api.columns(active!.dataset_id), enabled: Boolean(active),
  });

  useEffect(() => {
    if (!active || !columns.data || typedDatasetId.current === active.dataset_id) return;
    setColumnTypes(Object.fromEntries(columns.data.map(
      (column) => [column.name, guessType(column.type)])));
    typedDatasetId.current = active.dataset_id;
  }, [active, columns.data, setColumnTypes]);

  const load = useMutation({
    mutationFn: async () => { const path = await api.selectDataset(); return path ? api.loadDataset(path) : null; },
    onMutate: () => setStatus({ message: "Importando archivo…", kind: "info", sticky: true }),
    onSuccess: async (metadata) => {
      if (!metadata) { setStatus(null); return; }
      setActiveDataset(metadata.dataset_id);
      await client.invalidateQueries({ queryKey: ["datasets"] });
      await client.fetchQuery({ queryKey: ["preview", metadata.dataset_id, 0, 100, null],
        queryFn: () => api.tablePage(metadata.dataset_id, 0, 100) });
      const rows = metadata.rows === null ? "conteo en segundo plano" :
        `${metadata.rows.toLocaleString("es-ES")} filas`;
      setStatus({ message: `'${metadata.name}' listo (${rows})`, kind: "success" });
    },
    onError: (error: Error) => setStatus({ message: `No se pudo cargar: ${error.message}`, kind: "error" }),
  });
  const close = useMutation({
    mutationFn: (id: string) => api.closeDataset(id),
    onMutate: () => setStatus({ message: "Cerrando dataset…", kind: "info", sticky: true }),
    onSuccess: async () => { setActiveDataset(null); await client.invalidateQueries({ queryKey: ["datasets"] });
      setStatus({ message: "Dataset cerrado", kind: "success" }); },
    onError: (error: Error) => setStatus({ message: error.message, kind: "error" }),
  });
  const exportDataset = useMutation({
    mutationFn: async () => { if (!active) throw new Error("Sin dataset activo.");
      const path = await api.selectExportPath(`${active.name}.csv`, "csv");
      return path ? api.exportDataset(active.dataset_id, path, "csv") : null; },
    onMutate: () => setStatus({ message: "Exportando dataset…", kind: "info", sticky: true }),
    onSuccess: (path) => setStatus(path ? { message: "Dataset exportado", kind: "success" } : null),
    onError: (error: Error) => setStatus({ message: error.message, kind: "error" }),
  });

  if (datasets.isLoading) return <section><Loading label="Cargando datasets…" /></section>;
  if (!datasets.data?.length) return <section><header className="page-header"><div>
    <p className="eyebrow">DATOS</p><h1>Datos y tablas</h1><p>Carga un archivo para empezar.</p>
    </div></header><div className="empty"><h2>Aún no hay datasets</h2>
      <p>Importa un CSV, Excel o Parquet.</p><button className="primary"
        onClick={() => load.mutate()} disabled={load.isPending}>Abrir archivo</button></div></section>;

  return <section><header className="page-header"><div><p className="eyebrow">DATOS</p>
    <h1>Datos y constructor de tablas</h1><p>Explora, tipa, filtra, pivota y construye sin salir de esta vista.</p></div>
    <div className="toolbar-actions"><button className="quiet-button" onClick={() => setPreviewKey((key) => key + 1)}>Refrescar</button>
      <button className="quiet-button" onClick={() => exportDataset.mutate()} disabled={!active || exportDataset.isPending}>Exportar</button>
      <button className="primary" onClick={() => load.mutate()} disabled={load.isPending}>{load.isPending ? "Importando…" : "Abrir archivo"}</button></div>
  </header>
  <div className="dataset-toolbar"><button className="quiet-button" onClick={() => active && close.mutate(active.dataset_id)}
    disabled={!active || close.isPending}>Quitar del pool</button><div className="dataset-pool"><span className="field-label">Dataset activo</span>
    <select value={active?.dataset_id ?? ""} onChange={(event) => setActiveDataset(event.target.value)}>
      {datasets.data.map((item) => <option key={item.dataset_id} value={item.dataset_id}>{item.name} · {item.rows === null ? "contando…" : `${item.rows.toLocaleString("es-ES")} filas`}</option>)}</select>
    <span className="active-badge">● {active?.name}</span></div></div>
  {active && <div className="unified-data-workspace">
    <DataPreview key={`${active.dataset_id}-${previewKey}`} datasetId={active.dataset_id}
      totalRows={active.rows} totalRowsApproximate={active.rows_approximate}
      columnTypes={columnTypes} onColumnTypeChange={setColumnType} />
    <article className="panel builder-panel"><TableScreen embedded /></article>
  </div>}
  <MergePanel datasets={datasets.data} /></section>;
}
