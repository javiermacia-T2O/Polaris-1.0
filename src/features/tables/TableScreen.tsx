import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, cancelRequest } from "../../shared/api";
import { useUiStore } from "../../app/store";
import type { TablePage, TableRecipe } from "../../shared/types";
import { DataPreview } from "../datasets/DataPreview";
import { Loading } from "../../shared/Loading";
import { TYPE_LABELS, TYPE_OPTIONS } from "../../shared/columnTypes";

const AGGREGATIONS = ["sum", "mean", "median", "min", "max", "count", "nunique"];
const AGG_LABELS: Record<string, string> = {
  sum: "Suma", mean: "Media", median: "Mediana", min: "Mínimo",
  max: "Máximo", count: "Recuento", nunique: "Únicos",
};

type Role = "row" | "column" | "value" | "filter";
type ValueSpec = { col: string; agg: string; pivot: boolean };

export function TableScreen({ embedded = false }: { embedded?: boolean }) {
  const client = useQueryClient();
  const { activeDatasetId, setActiveDataset, setStatus, columnTypes, setColumnType } = useUiStore();
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: api.listDatasets });
  const source = datasets.data?.find((item) => item.dataset_id === activeDatasetId) ?? datasets.data?.[0];

  const [rows, setRows] = useState<string[]>([]);
  const [columns, setColumns] = useState<string[]>([]);
  const [values, setValues] = useState<ValueSpec[]>([]);
  const [filters, setFilters] = useState<Record<string, unknown[]>>({});
  const [search, setSearch] = useState("");
  const [filterColumn, setFilterColumn] = useState<string | null>(null);
  const [delayedRecipe, setDelayedRecipe] = useState<TableRecipe>({});
  const buildRequestId = useRef<string | null>(null);
  const originalDatasetId = useRef<string | null>(null);
  const appliedDatasetId = useRef<string | null>(null);

  const columnsQuery = useQuery({
    queryKey: ["columns", source?.dataset_id],
    queryFn: () => api.columns(source!.dataset_id),
    enabled: Boolean(source?.dataset_id),
  });
  const allColumns = useMemo(() => columnsQuery.data ?? [], [columnsQuery.data]);
  const visibleColumns = useMemo(() => allColumns.filter((column) =>
    column.name.toLocaleLowerCase("es").includes(search.toLocaleLowerCase("es"))),
  [allColumns, search]);

  const recipe: TableRecipe = useMemo(() => ({
    rows,
    columns,
    values: values.map((value) => ({ col: value.col, agg: value.agg, pivot: value.pivot })),
    filters: Object.fromEntries(Object.entries(filters).filter(([, list]) => list.length)),
    pivot: values.some((value) => value.pivot),
    column_types: columnTypes,
  }), [rows, columns, values, filters, columnTypes]);
  const configured = rows.length > 0 || columns.length > 0 || values.length > 0
    || Object.values(filters).some((selected) => selected.length > 0);
  const delayedConfigured = Boolean(delayedRecipe.rows?.length || delayedRecipe.columns?.length
    || delayedRecipe.values?.length || Object.values(delayedRecipe.filters ?? {}).some((selected) => selected.length > 0));

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
    if (role === "value") {
      const sourceType = allColumns.find((item) => item.name === column)?.type ?? "";
      const numeric = columnTypes[column] === "numero"
        || /int|float|double|decimal|numeric|number/i.test(sourceType);
      setValues((current) => [...current, { col: column, agg: numeric ? "sum" : "count", pivot: false }]);
    }
    if (role === "filter") setFilters((current) => ({ ...current, [column]: current[column] ?? [] }));
  };

  const clearRecipe = () => {
    setRows([]);
    setColumns([]);
    setValues([]);
    setFilters({});
    setFilterColumn(null);
  };

  useEffect(() => {
    const timer = window.setTimeout(() => setDelayedRecipe(recipe), 180);
    return () => window.clearTimeout(timer);
  }, [recipe]);

  useEffect(() => {
    if (!source) return;
    if (!originalDatasetId.current || source.dataset_id !== appliedDatasetId.current) {
      originalDatasetId.current = source.dataset_id;
      appliedDatasetId.current = null;
    }
    clearRecipe();
  // A source switch deliberately starts a clean recipe; role state should not
  // leak between unrelated datasets.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source?.dataset_id]);

  const preview = useQuery({
    queryKey: ["table-preview", source?.dataset_id, JSON.stringify(delayedRecipe)],
    queryFn: ({ signal }) => api.previewTable(source!.dataset_id, delayedRecipe, 20, { signal }),
    enabled: Boolean(source?.dataset_id) && delayedConfigured,
    retry: false,
    placeholderData: (previous) => previous,
  });

  const build = useMutation({
    mutationFn: async () => {
      if (!source) throw new Error("Selecciona un dataset.");
      if (!configured) throw new Error("Asigna al menos un rol o filtro.");
      const requestId = `build-${Date.now().toString(36)}`;
      buildRequestId.current = requestId;
      return api.buildTable(source.dataset_id, recipe, {
        requestId,
        timeoutMs: 30 * 60 * 1000,
        onProgress: ({ message }) => setStatus({ message, kind: "info", sticky: true }),
      });
    },
    onMutate: () => setStatus({ message: "Aplicando cambios…", kind: "info", sticky: true }),
    onSuccess: async (id) => {
      appliedDatasetId.current = id;
      setActiveDataset(id);
      await client.invalidateQueries({ queryKey: ["datasets"] });
      await client.invalidateQueries({ queryKey: ["columns", id] });
      await client.fetchQuery({
        queryKey: ["preview", id, 0, 100, null],
        queryFn: () => api.tablePage(id, 0, 100),
      });
      setStatus({ message: "Cambios aplicados; tabla y vista previa actualizadas", kind: "success" });
    },
    onError: (error: Error) => setStatus({ message: `No se pudieron aplicar los cambios: ${error.message}`, kind: "error" }),
    onSettled: () => { buildRequestId.current = null; },
  });

  const resetWorkspace = async () => {
    clearRecipe();
    const original = originalDatasetId.current;
    if (!source || !original || original === source.dataset_id) {
      await client.invalidateQueries({ queryKey: ["preview", source?.dataset_id] });
      setStatus({ message: "Modelado restablecido", kind: "success" });
      return;
    }
    setStatus({ message: "Restaurando el dataset original…", kind: "info", sticky: true });
    setActiveDataset(original);
    await client.invalidateQueries({ queryKey: ["datasets"] });
    await client.fetchQuery({
      queryKey: ["preview", original, 0, 100, null],
      queryFn: () => api.tablePage(original, 0, 100),
    });
    appliedDatasetId.current = null;
    originalDatasetId.current = original;
    setStatus({ message: "Dataset original restaurado", kind: "success" });
  };

  if (!source && !datasets.isLoading) return <div className="empty">
    <h2>Selecciona o carga un dataset</h2><p>La tabla se construye en el motor local.</p>
  </div>;

  return <section className={embedded ? "table-builder-embedded" : undefined}>
    {datasets.error && <div className="error" role="alert">No se pudieron cargar los datasets: {datasets.error.message}</div>}
    {build.error && <div className="error" role="alert">No se pudo construir la tabla: {build.error.message}</div>}

    <div className="data-model-grid">
      <div className="model-preview-area">
        {!configured && source && <DataPreview datasetId={source.dataset_id}
          totalRows={source.rows} totalRowsApproximate={source.rows_approximate}
          columnTypes={columnTypes} onColumnTypeChange={setColumnType} />}
        {configured && <RecipePreview preview={preview.data} error={preview.error}
          fetching={preview.isFetching} columnTypes={columnTypes}
          onColumnTypeChange={setColumnType} onRefresh={() => preview.refetch()} />}
      </div>

      <article className="panel variables-panel">
        <div className="panel-head variables-head">
          <div><div className="heading-with-live"><h2>Variables</h2><span className="live-badge"><i />En tiempo real</span></div>
            <p>{allColumns.length} variables · {rows.length} fila(s) · {columns.length} columna(s) · {values.length} valor(es)</p></div>
          <div className="panel-actions">
            <button className="quiet-button" onClick={() => void resetWorkspace()} disabled={build.isPending}>Reset</button>
            <button className="primary" onClick={() => build.mutate()} disabled={build.isPending || !configured}>
              {build.isPending ? "Aplicando…" : "Aplicar cambios"}</button>
            {build.isPending && <button className="quiet-button" onClick={() => {
              if (buildRequestId.current) void cancelRequest(buildRequestId.current);
            }}>Cancelar</button>}
          </div>
        </div>
        <div className="variables-search">
          <input className="checkbox-search" placeholder="Buscar variable…" value={search}
            onChange={(event) => setSearch(event.target.value)} />
        </div>
        <div className="role-matrix" role="table" aria-label="Asignación de variables">
          <div className="role-matrix-header" role="row">
            <span>Variable</span><span>Fila</span><span>Columna</span><span>Valor</span><span>Piv.</span><span>Filtro</span><span />
          </div>
          <div className="role-matrix-body">
            {columnsQuery.isLoading && <Loading label="Leyendo variables…" inline />}
            {!columnsQuery.isLoading && visibleColumns.length === 0 && <div className="empty-inline">Sin variables que coincidan.</div>}
            {visibleColumns.map((column) => {
              const role = roleOf(column.name);
              const value = values.find((item) => item.col === column.name);
              return <div key={column.name} className={`role-matrix-row ${role ? `role-${role}` : ""}`} role="row">
                <div className="matrix-variable" title={column.name}>
                  <strong>{column.name}</strong>
                  <small>{TYPE_LABELS[columnTypes[column.name] ?? ""] ?? column.type}</small>
                  {value && <select aria-label={`Agregación de ${column.name}`} value={value.agg}
                    onChange={(event) => setValues((current) => current.map((item) =>
                      item.col === column.name ? { ...item, agg: event.target.value } : item))}>
                    {AGGREGATIONS.map((agg) => <option key={agg} value={agg}>{AGG_LABELS[agg]}</option>)}
                  </select>}
                </div>
                <RoleButton label="Fila" active={role === "row"} tone="row"
                  onClick={() => setRole(column.name, role === "row" ? null : "row")} />
                <RoleButton label="Columna" active={role === "column"} tone="column"
                  onClick={() => setRole(column.name, role === "column" ? null : "column")} />
                <RoleButton label="Valor" active={role === "value"} tone="value"
                  onClick={() => setRole(column.name, role === "value" ? null : "value")} />
                <label className={`pivot-check ${value?.pivot ? "active" : ""}`} title="Pivotar este valor por las columnas seleccionadas">
                  <input type="checkbox" aria-label={`Pivotar ${column.name}`} disabled={!value}
                    checked={value?.pivot ?? false} onChange={(event) => setValues((current) => current.map((item) =>
                      item.col === column.name ? { ...item, pivot: event.target.checked } : item))} />
                </label>
                <RoleButton label="Filtro" active={role === "filter"} tone="filter" onClick={() => {
                  if (role !== "filter") setRole(column.name, "filter");
                  setFilterColumn(column.name);
                }} />
                <button className="remove-role" aria-label={`Quitar rol de ${column.name}`}
                  title="Quitar rol" disabled={!role} onClick={() => setRole(column.name, null)}>×</button>
              </div>;
            })}
          </div>
        </div>
      </article>
    </div>

    {filterColumn && source && <FilterModal datasetId={source.dataset_id} column={filterColumn}
      selected={filters[filterColumn] ?? []} onClose={() => setFilterColumn(null)}
      onApply={(selected) => {
        setFilters((current) => ({ ...current, [filterColumn]: selected }));
        setFilterColumn(null);
      }} />}
  </section>;
}

function RoleButton({ label, active, tone, onClick }: {
  label: string; active: boolean; tone: Role; onClick: () => void;
}) {
  return <button className={`matrix-role role-${tone} ${active ? "active" : ""}`}
    aria-pressed={active} onClick={onClick}>{label}</button>;
}

function RecipePreview({ preview, error, fetching, columnTypes, onColumnTypeChange, onRefresh }: {
  preview: TablePage | undefined; error: Error | null; fetching: boolean;
  columnTypes: Record<string, string>;
  onColumnTypeChange: (column: string, type: string) => void;
  onRefresh: () => unknown;
}) {
  return <article className="panel model-preview">
    <div className="panel-head"><div><div className="heading-with-live"><h2>Vista previa</h2>
      <span className="live-badge"><i />Actualización en tiempo real</span></div>
      <p>{preview ? `${preview.columns.length} columnas · ${preview.rows.length} filas de muestra` : "Construyendo la vista…"}</p></div>
      <div className="panel-actions">
        {fetching && <Loading label="Actualizando…" inline />}
        {preview?.approximate && <span className="approx-badge">Muestra acotada</span>}
        <button className="quiet-button" onClick={onRefresh} disabled={fetching}>Refrescar</button>
      </div></div>
    {error && <div className="error" role="alert">{error.message}</div>}
    {!preview && !error && <Loading label="Preparando la tabla…" />}
    {preview && preview.columns.length > 0 && <div className="table-wrap">
      <table><thead><tr>{preview.columns.map((column) => <th key={column}>
        <span className="modeled-column-name">{column}</span>
        {Object.prototype.hasOwnProperty.call(columnTypes, column) && <select className="column-type-select"
          aria-label={`Tipo de ${column}`} value={columnTypes[column]}
          onChange={(event) => onColumnTypeChange(column, event.target.value)}>
          {TYPE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>}
      </th>)}</tr></thead>
      <tbody>{preview.rows.map((row, index) => <tr key={index}>
        {preview.columns.map((column) => <td key={column}>{formatCell(row[column])}</td>)}</tr>)}</tbody></table>
    </div>}
  </article>;
}

function formatCell(value: unknown) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return Number.isInteger(value) ? value.toLocaleString("es-ES") : value.toLocaleString("es-ES", { maximumFractionDigits: 3 });
  return String(value);
}

function valueKey(value: unknown) {
  return `${typeof value}:${JSON.stringify(value)}`;
}

function FilterModal({ datasetId, column, selected, onApply, onClose }: {
  datasetId: string; column: string; selected: unknown[];
  onApply: (values: unknown[]) => void; onClose: () => void;
}) {
  const [draft, setDraft] = useState<unknown[]>(selected);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search), 250);
    return () => window.clearTimeout(timer);
  }, [search]);
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  const values = useInfiniteQuery({
    queryKey: ["column-values", datasetId, column, debouncedSearch],
    queryFn: ({ pageParam, signal }) => api.columnValues(datasetId, column,
      debouncedSearch, 100, pageParam, { signal }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    staleTime: 5 * 60 * 1000,
  });
  const options = values.data?.pages.flatMap((page) => page.values) ?? [];
  const toggle = (value: unknown) => setDraft((current) => current.some((item) => valueKey(item) === valueKey(value))
    ? current.filter((item) => valueKey(item) !== valueKey(value)) : [...current, value]);

  return createPortal(<div className="modal-backdrop" role="presentation" onMouseDown={(event) => {
    if (event.currentTarget === event.target) onClose();
  }}>
    <section className="filter-modal" role="dialog" aria-modal="true" aria-labelledby="filter-modal-title">
      <header><div><p className="eyebrow">FILTRO</p><h2 id="filter-modal-title">{column}</h2>
        <p>Selecciona los valores que formarán parte de la vista previa y de la tabla final.</p></div>
        <button className="modal-close" aria-label="Cerrar filtro" onClick={onClose}>×</button></header>
      <div className="filter-modal-search">
        <input autoFocus placeholder="Buscar valores…" value={search} onChange={(event) => setSearch(event.target.value)} />
        <span>{draft.length} seleccionado(s)</span>
      </div>
      <div className="filter-modal-values">
        {values.isLoading && <Loading label="Leyendo valores…" />}
        {values.error && <div className="error" role="alert">{values.error.message}</div>}
        {!values.isLoading && !values.error && options.length === 0 && <div className="empty-inline">Sin coincidencias.</div>}
        {options.map((value) => <label key={valueKey(value)} className="filter-option">
          <input type="checkbox" checked={draft.some((item) => valueKey(item) === valueKey(value))}
            onChange={() => toggle(value)} /><span title={formatCell(value)}>{formatCell(value)}</span>
        </label>)}
        {values.hasNextPage && <button className="quiet-button load-more" disabled={values.isFetchingNextPage}
          onClick={() => values.fetchNextPage()}>{values.isFetchingNextPage ? "Cargando…" : "Cargar más valores"}</button>}
      </div>
      <footer><button className="quiet-button" onClick={() => setDraft([])}>Limpiar selección</button>
        <div><button className="quiet-button" onClick={onClose}>Cancelar</button>
          <button className="primary" onClick={() => onApply(draft)}>Aplicar filtro</button></div></footer>
    </section>
  </div>, document.body);
}
