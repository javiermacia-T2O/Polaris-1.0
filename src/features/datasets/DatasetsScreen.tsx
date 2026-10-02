import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../shared/api";
import { useUiStore } from "../../app/store";
import { MergePanel } from "./MergePanel";
import { TableBuilder } from "../tables/TableBuilder";
import { Loading } from "../../shared/Loading";
import { guessType } from "../../shared/columnTypes";
import type { DatasetMetadata } from "../../shared/types";

export function DatasetsScreen() {
  const client = useQueryClient();
  const { activeDatasetId, setActiveDataset, setStatus, setColumnTypes } = useUiStore();

  const datasets = useQuery({
    queryKey: ["datasets"],
    queryFn: api.listDatasets,
    // A huge source is counted in the background; poll until the exact total
    // arrives so the provisional "0 filas" is replaced without a manual refresh.
    refetchInterval: (query) => {
      if (!(query.state.data ?? []).some((item) => item.rows_approximate)) return false;
      return query.state.error ? 5000 : 1000;
    },
  });
  const active = datasets.data?.find((item) => item.dataset_id === activeDatasetId) ?? datasets.data?.[0];

  useEffect(() => {
    if (!active) return;
    const next: Record<string, string> = {};
    active.columns.forEach((name, index) => {
      next[name] = guessType(active.types[index] ?? "");
    });
    setColumnTypes(next);
  }, [active?.dataset_id, active?.columns, active?.types, setColumnTypes]);

  const columns = active?.columns.map((name, index) => ({
    name, type: active.types[index] ?? "",
  })) ?? [];

  const load = useMutation({
    mutationFn: async () => {
      const path = await api.selectDataset();
      if (!path) return null;
      const replacedId = active?.dataset_id;
      if (replacedId) {
        const metadata = await api.loadDataset(path);
        try {
          await api.closeDataset(replacedId);
        } catch (error) {
          await api.closeDataset(metadata.dataset_id).catch(() => undefined);
          throw error;
        }
        return { metadata, replacedId };
      }
      return { metadata: await api.loadDataset(path), replacedId: undefined };
    },
    onMutate: () => setStatus({ message: "Importando archivo…", kind: "info" }),
    onSuccess: async (result) => {
      if (!result) { setStatus(null); return; }
      const { metadata, replacedId } = result;
      client.setQueryData<DatasetMetadata[]>(["datasets"], (current) => [
        metadata,
        ...(current ?? []).filter((item) => item.dataset_id !== metadata.dataset_id
          && item.dataset_id !== replacedId),
      ]);
      setActiveDataset(metadata.dataset_id);
      const rows = metadata.rows_approximate ? "contando filas…" : `${metadata.rows.toLocaleString("es-ES")} filas`;
      setStatus({ message: `'${metadata.name}' cargado (${rows})`, kind: "success" });
      void client.invalidateQueries({ queryKey: ["datasets"] });
    },
    onError: (error: Error) => {
      void client.invalidateQueries({ queryKey: ["datasets"] });
      setStatus({ message: `No se pudo cargar: ${error.message}`, kind: "error" });
    },
  });

  const close = useMutation({
    mutationFn: (datasetId: string) => api.closeDataset(datasetId),
    onMutate: () => setStatus({ message: "Cerrando dataset…", kind: "info" }),
    onSuccess: async () => {
      setActiveDataset(null);
      setStatus({ message: "Dataset cerrado", kind: "success" });
      await client.invalidateQueries({ queryKey: ["datasets"] });
    },
    onError: (error: Error) => setStatus({ message: `No se pudo cerrar: ${error.message}`, kind: "error" }),
  });

  const exportDataset = useMutation({
    mutationFn: async () => {
      if (!active) throw new Error("Sin dataset activo.");
      const path = await api.selectExportPath(`${active.name}.csv`, "csv");
      if (!path) return null;
      return api.exportDataset(active.dataset_id, path, "csv");
    },
    onMutate: () => setStatus({ message: "Exportando dataset…", kind: "info" }),
    onSuccess: (path) => setStatus(path ? { message: "Dataset exportado", kind: "success" } : null),
    onError: (error: Error) => setStatus({ message: `No se pudo exportar: ${error.message}`, kind: "error" }),
  });

  if (datasets.isLoading) return <section><Loading label="Cargando datasets…" /></section>;

  if (!datasets.data?.length) return <section>
    <header className="page-header"><div><p className="eyebrow">DATOS</p><h1>Datos y modelado</h1>
      <p>Carga un archivo para empezar a trabajar.</p></div></header>
    <div className="empty"><h2>Aún no hay datasets</h2>
      <p>Importa un CSV, Excel o Parquet para comenzar.</p>
      <button className="primary" onClick={() => load.mutate()} disabled={load.isPending}>
        {load.isPending ? "Importando…" : "Abrir archivo"}</button></div>
  </section>;

  return <section>
    <header className="page-header">
      <div><p className="eyebrow">DATOS</p><h1>Datos y modelado</h1>
        <p>Explora el dataset y constrúyelo en la misma vista.</p></div>
      <div className="toolbar-actions">
        <button className="quiet-button" onClick={() => exportDataset.mutate()} disabled={!active || exportDataset.isPending}>
          Exportar</button>
        <button className="primary" onClick={() => load.mutate()} disabled={load.isPending}>
          {load.isPending ? "Importando…" : "Abrir archivo"}</button>
      </div>
    </header>

    <div className="dataset-toolbar">
      <div className="dataset-pool">
        <span className="field-label">Dataset activo</span>
        <select value={active?.dataset_id ?? ""} onChange={(event) => setActiveDataset(event.target.value)}>
          {datasets.data.map((item) => <option key={item.dataset_id} value={item.dataset_id}>
            {item.name} · {item.rows_approximate
              ? item.rows > 0 ? `~${item.rows.toLocaleString("es-ES")} filas · exacto en curso` : "contando filas…"
              : `${item.rows.toLocaleString("es-ES")} filas`}</option>)}
        </select>
        <span className="active-badge">● {active?.name}</span>
      </div>
    </div>

    <section className="data-modeling-workspace">
      {active
        ? <TableBuilder source={active} columns={columns} />
        : <div className="empty-inline">Selecciona un dataset para modelarlo.</div>}
    </section>

    <MergePanel datasets={datasets.data} />
  </section>;
}
