import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../shared/api";
import { useUiStore } from "./store";
import { useAnalysis } from "./AnalysisContext";
import { Logo } from "./Logo";
import { Loading } from "../shared/Loading";

const GRANULARITY_LABELS: Record<string, string> = {
  D: "Diario", W: "Semanal", M: "Mensual", Q: "Trimestral", Y: "Anual", Original: "Original",
};

export function Sidebar() {
  const client = useQueryClient();
  const { activeDatasetId, setActiveDataset, setStatus } = useUiStore();
  const { analyses, selected, selectAnalysis, run, runPending, jobId, job } = useAnalysis();
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: api.listDatasets });
  const active = datasets.data?.find((item) => item.dataset_id === activeDatasetId) ?? datasets.data?.[0];

  useEffect(() => {
    const first = datasets.data?.[0];
    if (!activeDatasetId && first) {
      setActiveDataset(first.dataset_id);
    }
  }, [activeDatasetId, datasets.data, setActiveDataset]);

  const [dateColumn, setDateColumn] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [granularity, setGranularity] = useState("Original");

  const dateColumns = useQuery({
    queryKey: ["date-columns", active?.dataset_id],
    queryFn: () => api.dateColumns(active!.dataset_id),
    enabled: Boolean(active?.dataset_id),
  });

  useEffect(() => {
    const first = dateColumns.data?.[0];
    if (first && !dateColumns.data?.some((item) => item.column === dateColumn)) setDateColumn(first.column);
  }, [dateColumn, dateColumns.data]);

  const range = useQuery({
    queryKey: ["date-range", active?.dataset_id, dateColumn],
    queryFn: () => api.dateRange(active!.dataset_id, dateColumn),
    enabled: Boolean(active?.dataset_id && dateColumn),
  });

  useEffect(() => {
    if (range.data) {
      setStart(range.data.start ?? "");
      setEnd(range.data.end ?? "");
      setGranularity(range.data.granularity ?? "Original");
    }
  }, [range.data]);

  const load = useMutation({
    mutationFn: async () => {
      const path = await api.selectDataset();
      return path ? api.loadDataset(path) : null;
    },
    onMutate: () => setStatus({ message: "Abriendo archivo…", kind: "info" }),
    onSuccess: async (data) => {
      if (!data) { setStatus(null); return; }
      setActiveDataset(data.dataset_id);
      setStatus({ message: `'${data.name}' cargado · ${data.rows.toLocaleString("es-ES")} filas`, kind: "success" });
      await client.invalidateQueries({ queryKey: ["datasets"] });
    },
    onError: (error: Error) => setStatus({ message: `No se pudo abrir el archivo: ${error.message}`, kind: "error" }),
  });

  const close = useMutation({
    mutationFn: api.closeDataset,
    onMutate: () => setStatus({ message: "Cerrando dataset…", kind: "info" }),
    onSuccess: async (_data, datasetId) => {
      if (activeDatasetId === datasetId) setActiveDataset(null);
      setStatus({ message: "Dataset cerrado", kind: "success" });
      await client.invalidateQueries({ queryKey: ["datasets"] });
    },
    onError: (error: Error) => setStatus({ message: `No se pudo cerrar: ${error.message}`, kind: "error" }),
  });

  const applyRange = useMutation({
    mutationFn: () => api.applyDateRange(active!.dataset_id, dateColumn, start || null, end || null),
    onMutate: () => setStatus({ message: "Aplicando filtro temporal…", kind: "info" }),
    onSuccess: async () => {
      setStatus({ message: `Periodo aplicado: ${start || "inicio"} → ${end || "fin"}`, kind: "success" });
      await client.invalidateQueries({ queryKey: ["datasets"] });
      await client.invalidateQueries({ queryKey: ["preview", active?.dataset_id] });
    },
    onError: (error: Error) => setStatus({ message: error.message, kind: "error" }),
  });

  const resetRange = useMutation({
    mutationFn: () => api.resetDateRange(active!.dataset_id),
    onMutate: () => setStatus({ message: "Restableciendo periodo…", kind: "info" }),
    onSuccess: async () => {
      setStatus({ message: "Periodo restablecido", kind: "success" });
      await client.invalidateQueries({ queryKey: ["datasets"] });
      await client.invalidateQueries({ queryKey: ["preview", active?.dataset_id] });
    },
    onError: (error: Error) => setStatus({ message: error.message, kind: "error" }),
  });

  const running = Boolean(jobId && job && ["QUEUED", "RUNNING"].includes(job.state));

  return <aside className="sidebar">
    <div className="brand"><Logo size={32} /><div><span className="brand-title">Polaris</span><small>Marketing Science</small></div></div>

    <div className="sidebar-scroll">
      <p className="section-title">01 · Archivo</p>
      <p className="file-label">{active ? active.name : "(ninguno)"}</p>
      <button className="primary block" onClick={() => load.mutate()} disabled={load.isPending}>
        {load.isPending ? "Abriendo…" : "Abrir archivo"}
      </button>
      <div className="row-2">
        <button className="quiet-button" onClick={() => load.mutate()} disabled={load.isPending}>Añadir</button>
        <button className="quiet-button" onClick={() => useUiStore.getState().setScreen("datos")}>Unir</button>
      </div>
      <label className="field-label">Dataset activo
        <select value={active?.dataset_id ?? ""} onChange={(event) => setActiveDataset(event.target.value || null)}>
          {!datasets.data?.length && <option value="">(sin datasets)</option>}
          {datasets.data?.map((item) => <option key={item.dataset_id} value={item.dataset_id}>{item.name}</option>)}
        </select></label>
      <button className="quiet-button block" onClick={() => active && close.mutate(active.dataset_id)}
        disabled={!active || close.isPending}>Quitar del pool</button>

      <p className="section-title">02 · Periodo</p>
      <label className="field-label">Columna de fecha
        <select value={dateColumn} onChange={(event) => setDateColumn(event.target.value)}
          disabled={!dateColumns.data?.length}>
          {!dateColumns.data?.length && <option value="">(sin columnas de fecha)</option>}
          {dateColumns.data?.map((item) => <option key={item.column} value={item.column}>{item.column}</option>)}
        </select></label>
      <p className="range-label">{range.data
        ? `${range.data.start ?? "—"} → ${range.data.end ?? "—"} · ${GRANULARITY_LABELS[range.data.granularity] ?? range.data.granularity}`
        : "(sin datos)"}</p>
      <div className="row-2">
        <label className="field-label">Desde<input type="date" value={start} onChange={(event) => setStart(event.target.value)} /></label>
        <label className="field-label">Hasta<input type="date" value={end} onChange={(event) => setEnd(event.target.value)} /></label>
      </div>
      <label className="field-label inline">Granularidad
        <select value={granularity} onChange={(event) => setGranularity(event.target.value)}>
          {Object.entries(GRANULARITY_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select></label>
      <div className="row-2">
        <button className="primary" onClick={() => applyRange.mutate()} disabled={!active || !dateColumn || applyRange.isPending}>Aplicar</button>
        <button className="quiet-button" onClick={() => resetRange.mutate()} disabled={!active || resetRange.isPending}>Reset</button>
      </div>

      <p className="section-title">03 · Análisis</p>
      <p className="analysis-cat">{selected?.category ?? "—"}</p>
      <select className="analysis-select" value={selected?.id ?? ""} onChange={(event) => selectAnalysis(event.target.value)}>
        <option value="">Seleccionar análisis…</option>
        {analyses.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select>
      <p className="analysis-desc">{selected?.description ?? "Elige un método para ver su descripción."}</p>
      <div className="format-hint">
        <span className="format-hint-title">Formato tabla:</span>
        <span className="format-hint-body">{selected?.table_format?.summary ?? "—"}</span>
      </div>
      <button className="primary block" onClick={run}
        disabled={!selected || !active || runPending || running}>
        {runPending ? "Enviando…" : running ? "Ejecutando…" : "Ejecutar análisis"}
      </button>
      {analyses.length === 0 && <Loading label="Leyendo catálogo…" inline />}
    </div>

    <div className="sidebar-foot">
      <span className={`connection-dot ${active ? "connected" : ""}`} />
      {active ? "Dataset seleccionado" : "Sin dataset activo"}
    </div>
  </aside>;
}