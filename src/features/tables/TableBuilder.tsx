import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../shared/api";
import { useUiStore } from "../../app/store";
import type { ColumnInfo, DatasetMetadata, TableRecipe } from "../../shared/types";
import { DataPreview } from "../datasets/DataPreview";
import { Loading } from "../../shared/Loading";
import { TYPE_LABELS } from "../../shared/columnTypes";

const AGGREGATIONS = ["sum", "mean", "median", "min", "max", "count", "nunique"];
const AGG_LABELS: Record<string, string> = {
  sum: "Suma", mean: "Media", median: "Mediana", min: "Mínimo",
  max: "Máximo", count: "Recuento", nunique: "Únicos",
};

type Role = "row" | "column" | "value" | "filter";
type ValueSpec = { col: string; agg: string; pivot: boolean };

/**
 * Modelado section: assigns roles to the dataset variables and builds a table.
 *
 * It receives the active dataset and its columns from the parent so the fused
 * screen shares a single `columns` query between the preview and the builder.
 */
export function TableBuilder({ source, columns }: {
  source: DatasetMetadata;
  columns: ColumnInfo[];
}) {
  const client = useQueryClient();
  const { setActiveDataset, setStatus, columnTypes } = useUiStore();

  const [rows, setRows] = useState<string[]>([]);
  const [columnsRole, setColumnsRole] = useState<string[]>([]);
  const [values, setValues] = useState<ValueSpec[]>([]);
  const [filters, setFilters] = useState<Record<string, string[]>>({});
  const [search, setSearch] = useState("");
  const [tableId, setTableId] = useState<string | null>(null);

  const allColumns = useMemo(() => columns, [columns]);
  const numericColumns = useMemo(() => allColumns.filter((column) =>
    /int|float|double|decimal|numeric|number/i.test(column.type)).map((column) => column.name),
    [allColumns]);

  const recipe: TableRecipe = useMemo(() => ({
    rows,
    columns: columnsRole,
    values: values.map((value) => ({ col: value.col, agg: value.agg, pivot: value.pivot })),
    filters: Object.fromEntries(Object.entries(filters).filter(([, list]) => list.length)),
    pivot: values.some((value) => value.pivot),
    column_types: columnTypes,
  }), [rows, columnsRole, values, filters, columnTypes]);

  const roleOf = (column: string): Role | null =>
    rows.includes(column) ? "row" : columnsRole.includes(column) ? "column"
      : values.some((value) => value.col === column) ? "value"
      : filters[column]?.length ? "filter" : null;

  const setRole = (column: string, role: Role | null) => {
    setRows((current) => current.filter((item) => item !== column));
    setColumnsRole((current) => current.filter((item) => item !== column));
    setValues((current) => current.filter((item) => item.col !== column));
    setFilters((current) => { const next = { ...current }; delete next[column]; return next; });
    if (role === "row") setRows((current) => [...current, column]);
    if (role === "column") setColumnsRole((current) => [...current, column]);
    if (role === "value") setValues((current) => [...current, { col: column, agg: "sum", pivot: columnsRole.length > 0 }]);
    if (role === "filter") setFilters((current) => ({ ...current, [column]: [] }));
  };

  const clearAll = () => {
    setRows([]); setColumnsRole([]); setValues([]); setFilters({}); setTableId(null);
  };

  const preview = useQuery({
    queryKey: ["table-preview", source.dataset_id, JSON.stringify(recipe)],
    queryFn: () => api.previewTable(source.dataset_id, recipe, 20),
    enabled: rows.length > 0 || columnsRole.length > 0
      || values.length > 0 || Object.keys(recipe.filters ?? {}).length > 0,
    retry: false,
  });

  const build = useMutation({
    mutationFn: () => api.buildTable(source.dataset_id, recipe),
    onMutate: () => setStatus({ message: "Construyendo tabla…", kind: "info" }),
    onSuccess: async (id) => {
      setTableId(id);
      setActiveDataset(id);
      setStatus({ message: "Tabla construida", kind: "success" });
      await client.invalidateQueries({ queryKey: ["datasets"] });
    },
    onError: (error: Error) => setStatus({ message: `No se pudo construir: ${error.message}`, kind: "error" }),
  });

  useEffect(() => { setTableId(null); }, [source.dataset_id]);

  const result = useQuery({
    queryKey: ["datasets"],
    queryFn: api.listDatasets,
    enabled: Boolean(tableId),
  }).data?.find((item) => item.dataset_id === tableId);

  const visible = allColumns.filter((column) =>
    column.name.toLocaleLowerCase("es").includes(search.toLocaleLowerCase("es")));

  return <div className="table-builder-grid">
    <article className="panel">
      <div className="panel-head"><div><h2>Variables</h2>
        <p>{allColumns.length} columnas · {rows.length} fila(s) · {columnsRole.length} columna(s) · {values.length} valor(es)</p></div>
        <div className="panel-actions">
          <button className="quiet-button" onClick={() => preview.refetch()} disabled={preview.isFetching}>Refrescar</button>
          <button className="quiet-button" onClick={clearAll}>Reset</button>
          <button className="primary" onClick={() => build.mutate()} disabled={build.isPending || !values.length}>
            {build.isPending ? "Construyendo…" : "Construir tabla"}</button>
        </div></div>
      <div className="config-fields">
        <input className="checkbox-search" placeholder="Buscar variable…" value={search} onChange={(event) => setSearch(event.target.value)} />
      </div>
      <div className="variable-list">
        {visible.length === 0 && <div className="empty-inline">Sin variables que coincidan.</div>}
        {visible.map((column) => {
          const role = roleOf(column.name);
          const value = values.find((item) => item.col === column.name);
          return <div key={column.name} className={`variable-card ${role ? `role-${role}` : ""}`}>
            <div className="variable-head">
              <div className="variable-name">
                <span aria-hidden="true">{role === "row" ? "▤" : role === "column" ? "▥" : role === "value" ? "Σ" : role === "filter" ? "⛃" : "•"}</span>
                <span title={column.name}>{column.name}</span>
              </div>
              <div className="variable-type">{TYPE_LABELS[columnTypes[column.name] ?? ""] ?? column.type}</div>
            </div>
            <div className="role-buttons">
              {(["row", "column", "value", "filter"] as Role[]).map((item) => <button key={item}
                className={`role-${item} ${role === item ? "active" : ""}`}
                onClick={() => setRole(column.name, role === item ? null : item)}>
                {item === "row" ? "Fila" : item === "column" ? "Columna" : item === "value" ? "Valor" : "Filtro"}</button>)}
              {role && <button className="trash-button" title="Quitar rol" aria-label="Quitar rol" onClick={() => setRole(column.name, null)}>×</button>}
            </div>
            {value && <div className="variable-extras">
              <select value={value.agg} onChange={(event) => setValues((current) =>
                current.map((item) => item.col === column.name ? { ...item, agg: event.target.value } : item))}>
                {AGGREGATIONS.map((agg) => <option key={agg} value={agg}>{AGG_LABELS[agg]}</option>)}</select>
              <label><input type="checkbox" checked={value.pivot} onChange={(event) => setValues((current) =>
                current.map((item) => item.col === column.name ? { ...item, pivot: event.target.checked } : item))} />Pivotar</label>
            </div>}
            {role === "filter" && <FilterValues
              datasetId={source.dataset_id}
              column={column.name}
              selected={filters[column.name] ?? []}
              onChange={(next) => setFilters((current) => ({ ...current, [column.name]: next }))} />}
            {role === "value" && !numericColumns.includes(column.name) &&
              <div className="variable-type">Sugerencia: usa «Recuento» para variables no numéricas.</div>}
          </div>;
        })}
      </div>
    </article>

    <div className="table-recipe-summary">
      <article className="panel">
        <div className="panel-head">
          <div><h2>Vista previa de la tabla</h2>
            <p>{preview.data ? `${preview.data.columns.length} columnas · ${preview.data.rows.length} filas` : "Se actualiza al asignar roles."}</p></div>
          <div className="preview-meta">
            {preview.isFetching && <Loading label="Actualizando…" inline />}
            {preview.data?.approximate && <span>Muestra aproximada</span>}
          </div>
        </div>
        {values.length === 0 && rows.length === 0 && columnsRole.length === 0 &&
          <div className="preview-hint">Asigna una variable como <strong>Fila</strong>, <strong>Columna</strong> o <strong>Valor</strong> para ver la tabla.</div>}
        {values.length === 0 && (rows.length > 0 || columnsRole.length > 0) &&
          <div className="preview-hint">Sin métrica seleccionada: se muestran las dimensiones sin agregación.</div>}
        {values.length > 0 && rows.length === 0 && columnsRole.length === 0 &&
          <div className="preview-hint">Sin variable de fila o columna no se puede representar la tabla.</div>}
        {preview.error && <div className="error" role="alert">{preview.error.message}</div>}
        {preview.data && preview.data.columns.length > 0 && <div className="table-wrap">
          <table><thead><tr>{preview.data.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
            <tbody>{preview.data.rows.map((row, index) => <tr key={index}>
              {preview.data!.columns.map((column) => <td key={column}>{formatCell(row[column])}</td>)}</tr>)}</tbody></table>
        </div>}
      </article>
      {result && <DataPreview key={`${result.dataset_id}-${result.rows}`} datasetId={result.dataset_id} totalRows={result.rows} />}
    </div>
  </div>;
}

function formatCell(value: unknown) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return Number.isInteger(value) ? value.toLocaleString("es-ES") : value.toFixed(3);
  return String(value);
}

function FilterValues({ datasetId, column, selected, onChange }: {
  datasetId: string; column: string; selected: string[];
  onChange: (values: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const values = useQuery({
    queryKey: ["column-values", datasetId, column],
    queryFn: () => api.columnValues(datasetId, column),
    enabled: open,
    staleTime: 5 * 60 * 1000,
  });
  const options = values.data?.values ?? [];
  const visible = options.filter((value) =>
    value.toLocaleLowerCase("es").includes(search.toLocaleLowerCase("es")));
  const toggle = (value: string) => onChange(
    selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value]);

  return <div className="variable-extras filter-values">
    <button type="button" className="filter-toggle" onClick={() => setOpen((current) => !current)}>
      {selected.length ? `${selected.length} valor(es) seleccionado(s)` : "Seleccionar valores…"}
      <span aria-hidden="true">{open ? "▴" : "▾"}</span>
    </button>
    {selected.length > 0 && <div className="filter-chips">
      {selected.map((value) => <button key={value} type="button" className="filter-chip"
        onClick={() => toggle(value)} title="Quitar">{value} ×</button>)}
      <button type="button" className="quiet-button" onClick={() => onChange([])}>Limpiar</button>
    </div>}
    {open && <div className="filter-dropdown">
      {values.isLoading && <Loading label="Leyendo valores…" inline />}
      {values.data?.truncated && <div className="empty-inline">
        Demasiados valores distintos; usa el buscador del dataset.</div>}
      {!values.isLoading && !values.data?.truncated && <>
        <input className="checkbox-search" placeholder="Buscar valor…" value={search}
          onChange={(event) => setSearch(event.target.value)} />
        <div className="filter-options">
          {visible.length === 0 && <div className="empty-inline">Sin coincidencias.</div>}
          {visible.map((value) => <label key={value} className="filter-option">
            <input type="checkbox" checked={selected.includes(value)} onChange={() => toggle(value)} />
            <span title={value}>{value}</span></label>)}
        </div>
      </>}
    </div>}
  </div>;
}