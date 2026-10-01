import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../shared/api";
import { useUiStore } from "../../app/store";
import { DataPreview } from "./DataPreview";
import { MergePanel } from "./MergePanel";
import { TableBuilder } from "../tables/TableBuilder";
import { Loading } from "../../shared/Loading";
import { CheckboxList } from "../../shared/CheckboxList";
import { TYPE_OPTIONS, guessType } from "../../shared/columnTypes";

export function DatasetsScreen() {
  const client = useQueryClient();
  const { activeDatasetId, setActiveDataset, setStatus, columnTypes, setColumnTypes, setColumnType } = useUiStore();
  const [filterColumn, setFilterColumn] = useState("");
  const [filterValues, setFilterValues] = useState<string[]>([]);
  const [previewKey, setPreviewKey] = useState(0);

  const datasets = useQuery({
    queryKey: ["datasets"],
    queryFn: api.listDatasets,
    // A huge source is counted in the background; poll until the exact total
    // arrives so the provisional "0 filas" is replaced without a manual refresh.
    refetchInterval: (query) => (query.state.data ?? []).some((item) => item.rows_approximate) ? 1000 : false,
  });
  const active = datasets.data?.find((item) => item.dataset_id === activeDatasetId) ?? datasets.data?.[0];

  const columnsQuery = useQuery({
    queryKey: ["columns", active?.dataset_id],
    queryFn: () => api.columns(active!.dataset_id),
    enabled: Boolean(active?.dataset_id),
  });

  const profile = useQuery({
    queryKey: ["column-values", active?.dataset_id, filterColumn],
    queryFn: () => api.columnValues(active!.dataset_id, filterColumn),
    enabled: Boolean(active?.dataset_id && filterColumn),
    staleTime: 5 * 60 * 1000,
  });

  const filterOptions = profile.data?.values ?? [];

  useEffect(() => {
    if (!columnsQuery.data) return;
    const next: Record<string, string> = {};
    for (const column of columnsQuery.data) next[column.name] = guessType(column.type);
    setColumnTypes(next);
  }, [columnsQuery.data, setColumnTypes]);

  const load = useMutation({
    mutationFn: async () => {
      const path = await api.selectDataset();
      if (!path) return null;
      return api.loadDataset(path);
    },
    onMutate: () => setStatus({ message: "Importando archivo…", kind: "info" }),
    onSuccess: async (metadata) => {
      if (!metadata) { setStatus(null); return; }
      setActiveDataset(metadata.dataset_id);
      const rows = metadata.rows_approximate ? "contando filas…" : `${metadata.rows.toLocaleString("es-ES")} filas`;
      setStatus({ message: `'${metadata.name}' cargado (${rows})`, kind: "success" });
      await client.invalidateQueries({ queryKey: ["datasets"] });
    },
    onError: (error: Error) => setStatus({ message: `No se pudo cargar: ${error.message}`, kind: "error" }),
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

  const applyFilters = useMutation({
    mutationFn: () => {
      if (!active || !filterColumn) throw new Error("Selecciona una columna.");
      return api.applyFilters(active.dataset_id, [{ column: filterColumn, values: filterValues }]);
    },
    onMutate: () => setStatus({ message: "Aplicando filtro…", kind: "info" }),
    onSuccess: async () => {
      setStatus({ message: "Filtro aplicado", kind: "success" });
      await client.invalidateQueries({ queryKey: ["datasets"] });
      // Refresh the preview so the applied filter is visible immediately.
      await client.invalidateQueries({ queryKey: ["preview"] });
      await client.invalidateQueries({ queryKey: ["table-page"] });
    },
    onError: (error: Error) => setStatus({ message: `No se pudo filtrar: ${error.message}`, kind: "error" }),
  });

  const resetFilters = useMutation({
    mutationFn: () => api.resetFilters(active!.dataset_id),
    onMutate: () => setStatus({ message: "Restableciendo filtros…", kind: "info" }),
    onSuccess: async () => {
      setFilterValues([]);
      setStatus({ message: "Filtros restablecidos", kind: "success" });
      await client.invalidateQueries({ queryKey: ["datasets"] });
      await client.invalidateQueries({ queryKey: ["preview"] });
      await client.invalidateQueries({ queryKey: ["table-page"] });
    },
    onError: (error: Error) => setStatus({ message: `No se pudo restablecer: ${error.message}`, kind: "error" }),
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
        <button className="quiet-button" onClick={() => setPreviewKey((key) => key + 1)}>Refrescar vista</button>
        <button className="quiet-button" onClick={() => { setFilterValues([]); resetFilters.mutate(); }}
          disabled={!active || resetFilters.isPending}>Reset filtros</button>
        <button className="quiet-button" onClick={() => exportDataset.mutate()} disabled={!active || exportDataset.isPending}>
          Exportar</button>
        <button className="primary" onClick={() => load.mutate()} disabled={load.isPending}>
          {load.isPending ? "Importando…" : "Abrir archivo"}</button>
      </div>
    </header>

    <div className="dataset-toolbar">
      <div className="toolbar-actions">
        <button className="quiet-button" onClick={() => active && close.mutate(active.dataset_id)}
          disabled={!active || close.isPending}>Quitar del pool</button>
      </div>
      <div className="dataset-pool">
        <span className="field-label">Dataset activo</span>
        <select value={active?.dataset_id ?? ""} onChange={(event) => setActiveDataset(event.target.value)}>
          {datasets.data.map((item) => <option key={item.dataset_id} value={item.dataset_id}>
            {item.name} · {item.rows_approximate ? "contando…" : `${item.rows.toLocaleString("es-ES")} filas`}</option>)}
        </select>
        <span className="active-badge">● {active?.name}</span>
      </div>
    </div>

    <section className="fused-section">
      <h2 className="section-heading">Vista previa</h2>
      <div className="dataset-workspace">
        <div className="dataset-main">
          {active && <DataPreview key={`${active.dataset_id}-${previewKey}`} datasetId={active.dataset_id} totalRows={active.rows} totalRowsApproximate={active.rows_approximate} />}
        </div>
        <div className="column-panel">
          <article className="panel">
            <div className="panel-head"><div><h2>Tipos de columna</h2>
              <p>{columnsQuery.data?.length ?? 0} columnas</p></div>
              <div className="panel-actions">
                {columnsQuery.isLoading && <Loading label="Leyendo columnas…" inline />}
                <button className="quiet-button" onClick={() => {
                  if (!columnsQuery.data) return;
                  const next: Record<string, string> = {};
                  for (const column of columnsQuery.data) next[column.name] = guessType(column.type);
                  setColumnTypes(next);
                  setStatus({ message: "Tipos detectados", kind: "info" });
                }}>Detectar</button>
              </div></div>
            <div className="column-list">
              {columnsQuery.data?.map((column) => <div key={column.name} className="column-row">
                <span className="column-name" title={column.name}>{column.name}</span>
                <select value={columnTypes[column.name] ?? guessType(column.type)}
                  onChange={(event) => setColumnType(column.name, event.target.value)}>
                  {TYPE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
              </div>)}
            </div>
          </article>
        </div>
        <div className="column-panel">
          <article className="panel">
            <div className="panel-head"><div><h2>Filtros</h2>
              <p>Filtra el dataset por valores de una columna.</p></div></div>
            <div className="filter-controls">
              <label className="field-label">Columna
                <select value={filterColumn} onChange={(event) => { setFilterColumn(event.target.value); setFilterValues([]); }}>
                  <option value="">— Selecciona —</option>
                  {columnsQuery.data?.map((column) => <option key={column.name} value={column.name}>{column.name}</option>)}
                </select></label>
              {filterColumn && profile.isLoading && <Loading label="Leyendo valores…" inline />}
              {filterColumn && profile.data?.truncated &&
                <div className="empty-inline">Demasiados valores distintos; usa el buscador del dataset.</div>}
              {filterColumn && !profile.isLoading && !profile.data?.truncated &&
                <CheckboxList options={filterOptions} selected={filterValues}
                  onChange={setFilterValues} emptyLabel="Sin valores." maxHeight={160} />}
              <div className="row-2">
                <button className="primary" onClick={() => applyFilters.mutate()}
                  disabled={!filterColumn || !filterValues.length || applyFilters.isPending}>
                  {applyFilters.isPending ? "Aplicando…" : "Aplicar"}</button>
                <button className="quiet-button" onClick={() => resetFilters.mutate()}
                  disabled={!active || resetFilters.isPending}>Reset</button>
              </div>
            </div>
          </article>
        </div>
      </div>
    </section>

    <section className="fused-section">
      <h2 className="section-heading">Modelado</h2>
      {active
        ? <TableBuilder source={active} columns={columnsQuery.data ?? []} />
        : <div className="empty-inline">Selecciona un dataset para modelarlo.</div>}
    </section>

    <MergePanel datasets={datasets.data} />
  </section>;
}