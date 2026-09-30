import { useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, cancelRequest } from "../../shared/api";
import { useUiStore } from "../../app/store";
import type { TableRecipe } from "../../shared/types";
import { DataPreview } from "../datasets/DataPreview";
import { Loading } from "../../shared/Loading";

const AGGREGATIONS = ["sum", "mean", "median", "min", "max", "count", "nunique"];
const AGG_LABELS: Record<string, string> = {
  sum: "Suma", mean: "Media", median: "Mediana", min: "Mínimo",
  max: "Máximo", count: "Recuento", nunique: "Únicos",
};
const TYPE_LABELS: Record<string, string> = {
  numero: "Número", texto: "Texto", categorica: "Categoría",
  fecha: "Fecha", ignorar: "Ignorada",
};

type Role = "row" | "column" | "value" | "filter";
type ValueSpec = { col: string; agg: string; pivot: boolean };

export function TableScreen({ embedded = false }: { embedded?: boolean }) {
  const client = useQueryClient();
  const { activeDatasetId, setActiveDataset, setStatus, columnTypes } = useUiStore();
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: api.listDatasets });
  const source = datasets.data?.find((item) => item.dataset_id === activeDatasetId) ?? datasets.data?.[0];

  const [rows, setRows] = useState<string[]>([]);
  const [columns, setColumns] = useState<string[]>([]);
  const [values, setValues] = useState<ValueSpec[]>([]);
  const [filters, setFilters] = useState<Record<string, unknown[]>>({});
  const [search, setSearch] = useState("");
  const [tableId, setTableId] = useState<string | null>(null);
  const [delayedRecipe, setDelayedRecipe] = useState<TableRecipe>({});
  const buildRequestId = useRef<string | null>(null);

  const columnsQuery = useQuery({
    queryKey: ["columns", source?.dataset_id],
    queryFn: () => api.columns(source!.dataset_id),
    enabled: Boolean(source?.dataset_id),
  });

  const allColumns = useMemo(() => columnsQuery.data ?? [], [columnsQuery.data]);
  const numericColumns = useMemo(() => allColumns.filter((column) =>
    /int|float|double|decimal|numeric|number/i.test(column.type)).map((column) => column.name),
    [allColumns]);

  const recipe: TableRecipe = useMemo(() => ({
    rows,
    columns,
    values: values.map((value) => ({ col: value.col, agg: value.agg, pivot: value.pivot })),
    filters: Object.fromEntries(Object.entries(filters).filter(([, list]) => list.length)),
    pivot: values.some((value) => value.pivot),
    column_types: columnTypes,
  }), [rows, columns, values, filters, columnTypes]);

  const roleOf = (column: string): Role | null =>
    rows.includes(column) ? "row" : columns.includes(column) ? "column"
      : values.some((value) => value.col === column) ? "value"
      : Object.prototype.hasOwnProperty.call(filters, column) ? "filter" : null;

  const setRole = (column: string, role: Role | null) => {
    setRows((current) => current.filter((item) => item !== column));
    setColumns((current) => current.filter((item) => item !== column));
    setValues((current) => current.filter((item) => item.col !== column));
    setFilters((current) => { const next = { ...current }; delete next[column]; return next; });
    if (role === "row") setRows((current) => [...current, column]);
    if (role === "column") setColumns((current) => [...current, column]);
    if (role === "value") setValues((current) => [...current, { col: column, agg: "sum", pivot: columns.length > 0 }]);
    if (role === "filter") setFilters((current) => ({ ...current, [column]: [] }));
  };

  const clearAll = () => {
    setRows([]); setColumns([]); setValues([]); setFilters({}); setTableId(null);
  };

  useEffect(() => {
    const timer = window.setTimeout(() => setDelayedRecipe(recipe), 200);
    return () => window.clearTimeout(timer);
  }, [recipe]);

  const preview = useQuery({
    queryKey: ["table-preview", source?.dataset_id, JSON.stringify(delayedRecipe)],
    queryFn: ({ signal }) => api.previewTable(source!.dataset_id, delayedRecipe, 20, { signal }),
    enabled: Boolean(source?.dataset_id) && (rows.length > 0 || columns.length > 0
      || values.length > 0 || Object.keys(delayedRecipe.filters ?? {}).length > 0),
    retry: false,
  });

  const build = useMutation({
    mutationFn: () => {
      if (!source) throw new Error("Selecciona un dataset.");
      const requestId = `build-${Date.now().toString(36)}`;
      buildRequestId.current = requestId;
      return api.buildTable(source.dataset_id, recipe, {
        requestId, timeoutMs: 30 * 60 * 1000,
        onProgress: ({ message }) => setStatus({ message, kind: "info", sticky: true }),
      });
    },
    onMutate: () => setStatus({ message: "Construyendo tabla…", kind: "info", sticky: true }),
    onSuccess: async (id) => {
      setTableId(id);
      setActiveDataset(id);
      await client.invalidateQueries({ queryKey: ["datasets"] });
      await client.fetchQuery({ queryKey: ["preview", id, 0, 100, null],
        queryFn: () => api.tablePage(id, 0, 100) });
      setStatus({ message: "Tabla construida y vista previa actualizada", kind: "success" });
    },
    onError: (error: Error) => setStatus({ message: `No se pudo construir: ${error.message}`, kind: "error" }),
  });

  useEffect(() => {
    setRows([]); setColumns([]); setValues([]); setFilters({}); setTableId(null);
  }, [source?.dataset_id]);

  const result = datasets.data?.find((item) => item.dataset_id === tableId);
  const visible = allColumns.filter((column) =>
    column.name.toLocaleLowerCase("es").includes(search.toLocaleLowerCase("es")));

  if (!source && !datasets.isLoading) return <section>
    <header className="page-header"><div><p className="eyebrow">PREPARACIÓN</p><h1>Constructor de tablas</h1></div></header>
    <div className="empty"><h2>Selecciona o carga un dataset</h2><p>La tabla se construye en el motor local.</p></div>
  </section>;

  return <section className={embedded ? "table-builder-embedded" : undefined}>
    {!embedded && <header className="page-header">
      <div><p className="eyebrow">PREPARACIÓN · {source?.name ?? ""}</p><h1>Constructor de tablas</h1>
        <p>Asigna roles a las variables y observa la tabla construirse paso a paso.</p></div>
      <div className="toolbar-actions">
        <span className="active-dataset-chip" title="Dataset activo">
          <span className="connection-dot connected" />
          {source ? source.name : "Sin dataset"}
        </span>
        <button className="quiet-button" onClick={() => preview.refetch()} disabled={preview.isFetching}>Refrescar vista</button>
        <button className="quiet-button" onClick={clearAll}>Reset</button>
        <button className="primary" onClick={() => build.mutate()} disabled={build.isPending || !source || !values.length}>
          {build.isPending ? "Construyendo…" : "Construir tabla"}</button>
        {build.isPending && <button className="quiet-button" onClick={() => {
          if (buildRequestId.current) void cancelRequest(buildRequestId.current);
        }}>Cancelar</button>}
      </div>
    </header>}
    {embedded && <div className="panel-head"><div><h2>Constructor de tablas</h2>
      <p>Asigna roles; el preview se recalcula de forma cancelable.</p></div>
      <div className="toolbar-actions"><button className="quiet-button" onClick={clearAll}>Reset</button>
      <button className="primary" onClick={() => build.mutate()} disabled={build.isPending || !source || !values.length}>
        {build.isPending ? "Aplicando cambios…" : "Aplicar cambios"}</button>
      {build.isPending && <button className="quiet-button" onClick={() => buildRequestId.current && cancelRequest(buildRequestId.current)}>Cancelar</button>}</div></div>}
    {datasets.error && <div className="error" role="alert">No se pudieron cargar los datasets: {datasets.error.message}</div>}
    {build.error && <div className="error" role="alert">No se pudo construir la tabla: {build.error.message}</div>}

    <div className="table-builder-grid">
      <article className="panel">
        <div className="panel-head"><div><h2>Variables</h2><p>{allColumns.length} columnas · {rows.length} fila(s) · {columns.length} columna(s) · {values.length} valor(es)</p></div></div>
        <div className="config-fields">
          <input className="checkbox-search" placeholder="Buscar variable…" value={search} onChange={(event) => setSearch(event.target.value)} />
        </div>
        <div className="variable-list">
          {columnsQuery.isLoading && <Loading label="Leyendo columnas…" inline />}
          {!columnsQuery.isLoading && visible.length === 0 && <div className="empty-inline">Sin variables que coincidan.</div>}
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
                datasetId={source!.dataset_id}
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
            <div><h2>Vista previa</h2>
              <p>{preview.data ? `${preview.data.columns.length} columnas · ${preview.data.rows.length} filas` : "Se actualiza al asignar roles."}</p></div>
            <div className="preview-meta">
              {preview.isFetching && <Loading label="Actualizando…" inline />}
              {preview.data?.approximate && <span>Muestra aproximada</span>}
            </div>
          </div>
          {values.length === 0 && rows.length === 0 && columns.length === 0 &&
            <div className="preview-hint">Asigna una variable como <strong>Fila</strong>, <strong>Columna</strong> o <strong>Valor</strong> para ver la tabla.</div>}
          {values.length === 0 && (rows.length > 0 || columns.length > 0) &&
            <div className="preview-hint">Sin métrica seleccionada: se muestran las dimensiones sin agregación.</div>}
          {values.length > 0 && rows.length === 0 && columns.length === 0 &&
            <div className="preview-hint">Sin variable de fila o columna no se puede representar la tabla.</div>}
          {preview.error && <div className="error" role="alert">{preview.error.message}</div>}
          {preview.data && preview.data.columns.length > 0 && <div className="table-wrap">
            <table><thead><tr>{preview.data.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
              <tbody>{preview.data.rows.map((row, index) => <tr key={index}>
                {preview.data!.columns.map((column) => <td key={column}>{formatCell(row[column])}</td>)}</tr>)}</tbody></table>
          </div>}
        </article>
        {!embedded && result && <DataPreview key={`${result.dataset_id}-${result.rows}`} datasetId={result.dataset_id} totalRows={result.rows} />}
      </div>
    </div>
  </section>;
}

function formatCell(value: unknown) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return Number.isInteger(value) ? value.toLocaleString("es-ES") : value.toFixed(3);
  return String(value);
}

function FilterValues({ datasetId, column, selected, onChange }: {
  datasetId: string; column: string; selected: unknown[];
  onChange: (values: unknown[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search), 250);
    return () => window.clearTimeout(timer);
  }, [search]);
  const values = useInfiniteQuery({
    queryKey: ["column-values", datasetId, column, debouncedSearch],
    queryFn: ({ pageParam, signal }) => api.columnValues(
      datasetId, column, debouncedSearch, 100, pageParam, { signal }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: open,
    staleTime: 5 * 60 * 1000,
  });
  const options = values.data?.pages.flatMap((page) => page.values) ?? [];
  const key = (value: unknown) => `${typeof value}:${JSON.stringify(value)}`;
  const toggle = (value: unknown) => onChange(
    selected.some((item) => key(item) === key(value))
      ? selected.filter((item) => key(item) !== key(value)) : [...selected, value]);

  return <div className="variable-extras filter-values">
    <button type="button" className="filter-toggle" onClick={() => setOpen((current) => !current)}>
      {selected.length ? `${selected.length} valor(es) seleccionado(s)` : "Seleccionar valores…"}
      <span aria-hidden="true">{open ? "▴" : "▾"}</span>
    </button>
    {selected.length > 0 && <div className="filter-chips">
      {selected.map((value) => <button key={key(value)} type="button" className="filter-chip"
        onClick={() => toggle(value)} title="Quitar">{formatCell(value)} ×</button>)}
      <button type="button" className="quiet-button" onClick={() => onChange([])}>Limpiar</button>
    </div>}
    {open && <div className="filter-dropdown">
      {values.isLoading && <Loading label="Leyendo valores…" inline />}
      {!values.isLoading && <>
        <input className="checkbox-search" placeholder="Buscar valor…" value={search}
          onChange={(event) => setSearch(event.target.value)} />
        <div className="filter-options">
          {options.length === 0 && <div className="empty-inline">Sin coincidencias.</div>}
          {options.map((value) => <label key={key(value)} className="filter-option">
            <input type="checkbox" checked={selected.some((item) => key(item) === key(value))} onChange={() => toggle(value)} />
            <span title={String(value)}>{formatCell(value)}</span></label>)}
        </div>
        {values.hasNextPage && <button className="quiet-button" disabled={values.isFetchingNextPage}
          onClick={() => values.fetchNextPage()}>{values.isFetchingNextPage ? "Cargando…" : "Cargar más"}</button>}
      </>}
    </div>}
  </div>;
}
