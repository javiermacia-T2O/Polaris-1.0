import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api } from "../../shared/api";
import { useUiStore } from "../../app/store";
import type { DatasetMetadata } from "../../shared/types";
import { CheckboxList } from "../../shared/CheckboxList";

export function MergePanel({ datasets }: { datasets: DatasetMetadata[] }) {
  const client = useQueryClient();
  const { setActiveDataset, setStatus, mergeOpen, setMergeOpen } = useUiStore();
  const [selected, setSelected] = useState<string[]>([]);
  const [operation, setOperation] = useState<"concat" | "merge">("concat");
  const [mode, setMode] = useState<"all" | "common">("all");
  const [how, setHow] = useState<"inner" | "outer">("inner");
  const [keys, setKeys] = useState<string[]>([]);
  const [name, setName] = useState("");
  const open = mergeOpen;
  const setOpen = setMergeOpen;

  const commonColumns = useMemo(() => {
    const chosen = datasets.filter((item) => selected.includes(item.dataset_id));
    const first = chosen[0];
    if (chosen.length < 2 || !first) return [];
    return first.columns.filter((column) =>
      chosen.every((item) => item.columns.includes(column)));
  }, [datasets, selected]);

  const merge = useMutation({
    mutationFn: () => api.mergeDatasets(selected, {
      name, operation, mode, keys, how,
    }),
    onSuccess: async (metadata) => {
      setStatus({
        message: `'${metadata.name}' creado con ${metadata.rows.toLocaleString("es-ES")} filas.`,
        kind: "success",
      });
      setSelected([]);
      setKeys([]);
      setName("");
      setOpen(false);
      setActiveDataset(metadata.dataset_id);
      await client.invalidateQueries({ queryKey: ["datasets"] });
    },
  });

  const toggle = (id: string) => setSelected((current) =>
    current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);

  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [open, setOpen]);

  if (!open) return null;

  return <div className="filter-modal-backdrop" onMouseDown={(event) => {
    if (event.target === event.currentTarget) setOpen(false);
  }}>
    <section className="filter-modal merge-modal" role="dialog" aria-modal="true" aria-labelledby="merge-modal-title">
      <header className="filter-modal-head">
        <div><h2 id="merge-modal-title">Unir datasets</h2>
          <p>Combina dos o más datasets del pool en uno nuevo.</p></div>
        <button type="button" className="quiet-button" aria-label="Cerrar unión" onClick={() => setOpen(false)}>×</button>
      </header>
      {datasets.length < 2
        ? <div className="filter-modal-body"><div className="empty-inline">Necesitas al menos dos datasets cargados para poder unirlos.</div></div>
        : <div className="filter-modal-body merge-modal-body"><div className="merge-grid">
      <div className="merge-select">
        <span className="merge-label">Datasets a unir</span>
        <CheckboxList options={datasets.map((item) => ({ value: item.dataset_id,
          label: item.name, hint: `${item.rows.toLocaleString("es-ES")} filas` }))}
          selected={selected} onChange={setSelected} searchable={datasets.length > 6} />
      </div>
      <div className="merge-options">
        <label>Operación<select value={operation}
          onChange={(event) => setOperation(event.target.value as "concat" | "merge")}>
          <option value="concat">Apilar filas (concat)</option>
          <option value="merge">Unir por claves (merge)</option>
        </select></label>
        {operation === "concat" ? <label>Columnas<select value={mode}
          onChange={(event) => setMode(event.target.value as "all" | "common")}>
          <option value="all">Todas las columnas</option>
          <option value="common">Solo columnas comunes</option>
        </select></label> : <>
          <span className="merge-label">Claves comunes</span>
          <CheckboxList options={commonColumns.map((column) => ({ value: column, label: column }))}
            selected={keys} onChange={setKeys} searchable={commonColumns.length > 8}
            emptyLabel="Selecciona datasets con columnas en común." />
          <label>Modo<select value={how}
            onChange={(event) => setHow(event.target.value as "inner" | "outer")}>
            <option value="inner">inner (solo coincidencias)</option>
            <option value="outer">outer (todo, NaN donde falte)</option>
          </select></label>
        </>}
        <label>Nombre del resultado<input value={name}
          onChange={(event) => setName(event.target.value)} placeholder="unido" /></label>
        <button className="primary" onClick={() => merge.mutate()}
          disabled={selected.length < 2 || merge.isPending ||
            (operation === "merge" && keys.length === 0)}>
          {merge.isPending ? "Uniendo…" : "Unir datasets"}</button>
      </div>
        </div>
        {merge.error && <div className="error" role="alert">No se pudieron unir los datasets: {merge.error.message}</div>}
      </div>}
    </section>
  </div>;
}
