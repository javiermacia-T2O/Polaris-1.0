import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../shared/api";
import { useUiStore } from "../../app/store";
import type { ColumnInfo, DatasetMetadata, TableRecipe } from "../../shared/types";
import { DataPreview } from "../datasets/DataPreview";
import { Loading } from "../../shared/Loading";
import { TYPE_LABELS, TYPE_OPTIONS, guessType } from "../../shared/columnTypes";

const AGGREGATIONS = ["sum", "mean", "median", "min", "max", "count", "nunique"];
const AGG_LABELS: Record<string, string> = {
  sum: "Suma", mean: "Media", median: "Mediana", min: "Mínimo",
  max: "Máximo", count: "Recuento", nunique: "Únicos",
};

type Role = "row" | "column" | "value";
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
  const { setActiveDataset, setStatus, columnTypes, setColumnType } = useUiStore();

  const [rows, setRows] = useState<string[]>([]);
  const [columnsRole, setColumnsRole] = useState<string[]>([]);
  const [values, setValues] = useState<ValueSpec[]>([]);
  const [filters, setFilters] = useState<Record<string, Array<string | null> | null>>({});
  const [search, setSearch] = useState("");
  const [previewMessage, setPreviewMessage] = useState("");
  const [buildMessage, setBuildMessage] = useState("");
  const [waitingForAppliedPreview, setWaitingForAppliedPreview] = useState(false);
  const [tableId, setTableId] = useState<string | null>(null);
  const [rawRefresh, setRawRefresh] = useState(0);
  const originalDatasetId = useRef(source.dataset_id);
  const appliedDatasetIds = useRef(new Set<string>());

  const allColumns = useMemo(() => columns, [columns]);
  const baseDatasetId = appliedDatasetIds.current.has(source.dataset_id)
    ? originalDatasetId.current : source.dataset_id;
  const numericColumns = useMemo(() => allColumns.filter((column) =>
    /int|float|double|decimal|numeric|number/i.test(column.type)).map((column) => column.name),
    [allColumns]);

  const recipe: TableRecipe = useMemo(() => ({
    rows,
    columns: columnsRole,
    values: values.map((value) => ({ col: value.col, agg: value.agg, pivot: value.pivot })),
    filters: Object.entries(filters).reduce<Record<string, unknown[]>>((result, [column, selected]) => {
      if (selected !== null) result[column] = selected;
      return result;
    }, {}),
    pivot: values.some((value) => value.pivot),
    column_types: columnTypes,
  }), [rows, columnsRole, values, filters, columnTypes]);
  const recipeKey = JSON.stringify(recipe);
  const [previewRecipe, setPreviewRecipe] = useState(recipe);
  const previewRecipeKey = JSON.stringify(previewRecipe);

  // Role changes often arrive in quick succession. Wait briefly for the
  // user's selection to settle so we do not queue several large-source scans.
  useEffect(() => {
    const timer = window.setTimeout(() => setPreviewRecipe(recipe), 180);
    return () => window.clearTimeout(timer);
  }, [recipeKey]);

  const roleOf = (column: string): Role | null =>
    rows.includes(column) ? "row" : columnsRole.includes(column) ? "column"
      : values.some((value) => value.col === column) ? "value" : null;

  const setRole = (column: string, role: Role | null) => {
    setTableId(null);
    const nextColumns = role === "column"
      ? [...columnsRole.filter((item) => item !== column), column]
      : columnsRole.filter((item) => item !== column);
    const addingFirstColumn = role === "column" && nextColumns.length === 1;
    setRows((current) => current.filter((item) => item !== column));
    setColumnsRole(nextColumns);
    setValues((current) => {
      let next = current.filter((item) => item.col !== column);
      if (addingFirstColumn) next = next.map((item) => ({ ...item, pivot: true }));
      if (!nextColumns.length) next = next.map((item) => ({ ...item, pivot: false }));
      if (role === "value") next = [...next, { col: column, agg: "sum", pivot: nextColumns.length > 0 }];
      return next;
    });
    if (role === "row") setRows((current) => [...current, column]);
  };

  const toggleFilter = (column: string) => {
    setTableId(null);
    setFilters((current) => {
      const next = { ...current };
      if (Object.prototype.hasOwnProperty.call(next, column)) delete next[column];
      else next[column] = null;
      return next;
    });
    if (!Object.prototype.hasOwnProperty.call(filters, column)) {
      void client.prefetchQuery({
        queryKey: ["column-values", baseDatasetId, column],
        queryFn: () => api.columnValues(baseDatasetId, column, 500, {
          onProgress: () => undefined,
        }),
        staleTime: 15 * 60 * 1000,
        gcTime: 60 * 60 * 1000,
      });
    }
  };

  const clearAll = () => {
    setRows([]); setColumnsRole([]); setValues([]); setFilters({}); setTableId(null);
  };

  const resetBuilder = () => {
    clearAll();
    columns.forEach((column) => setColumnType(column.name, guessType(column.type)));
    setActiveDataset(originalDatasetId.current);
  };

  const hasConfiguration = rows.length > 0 || columnsRole.length > 0
    || values.length > 0 || Object.keys(filters).length > 0;
  const canApply = rows.length > 0 || columnsRole.length > 0 || values.length > 0
    || Object.values(filters).some((selected) => selected !== null);

  const preview = useQuery({
    queryKey: ["table-preview", baseDatasetId, previewRecipeKey],
    queryFn: () => api.previewTable(baseDatasetId, previewRecipe, 100, {
      onProgress: (event) => setPreviewMessage(event.message),
    }),
    enabled: hasConfiguration && !tableId && previewRecipeKey === recipeKey,
    retry: false,
  });

  const build = useMutation({
    mutationFn: async () => {
      const id = await api.buildTable(baseDatasetId, recipe, {
        onProgress: (event) => setBuildMessage(event.message),
      });
      const resultColumns = await api.columns(id).catch(() => []);
      return { id, resultColumns };
    },
    onMutate: () => setBuildMessage("Preparando cambios sobre todas las filas..."),
    onSuccess: ({ id, resultColumns }) => {
      const fallbackColumns = preview.data?.columns
        ?? [...new Set([...rows, ...columnsRole, ...values.map((value) => value.col)])];
      const resultNames = resultColumns.length
        ? resultColumns.map((column) => column.name) : fallbackColumns;
      const resultTypes = new Map(resultColumns.map((column) => [column.name, column.type]));
      const sourceTypes = new Map(source.columns.map((name, index) => [name, source.types[index] ?? ""]));
      const metadata: DatasetMetadata = {
        dataset_id: id,
        name: `${source.name} · tabla`,
        backend: source.backend,
        rows: 0,
        columns: resultNames,
        types: resultNames.map((name) => resultTypes.get(name) ?? sourceTypes.get(name) ?? ""),
        uses_disk: source.uses_disk,
        source_path: null,
        rows_approximate: true,
      };
      client.setQueryData<DatasetMetadata[]>(["datasets"], (current) => [
        metadata,
        ...(current ?? [source]).filter((item) => item.dataset_id !== id),
      ]);
      appliedDatasetIds.current.add(id);
      setTableId(id);
      setWaitingForAppliedPreview(true);
      setBuildMessage("Preparando la vista previa de la nueva tabla…");
      setActiveDataset(id);
      setStatus({ message: "Cambios aplicados", kind: "success" });
      void client.invalidateQueries({ queryKey: ["datasets"] });
    },
    onError: (error: Error) => {
      setWaitingForAppliedPreview(false);
      setBuildMessage("");
      setStatus({ message: `No se pudo construir: ${error.message}`, kind: "error" });
    },
  });

  useEffect(() => {
    if (appliedDatasetIds.current.has(source.dataset_id)) {
      setTableId(source.dataset_id);
      return;
    }
    originalDatasetId.current = source.dataset_id;
    clearAll();
  }, [source.dataset_id]);

  const handleAppliedPreviewLoading = useCallback((loading: boolean) => {
    if (tableId && source.dataset_id === tableId) {
      setWaitingForAppliedPreview(loading);
      if (!loading) setBuildMessage("");
    }
  }, [source.dataset_id, tableId]);

  const visible = allColumns.filter((column) =>
    column.name.toLocaleLowerCase("es").includes(search.toLocaleLowerCase("es")));

  return <div className="table-builder-grid">
    <div className="table-preview-column">
      {tableId || !hasConfiguration
        ? <DataPreview key={`${source.dataset_id}-${rawRefresh}`} datasetId={source.dataset_id}
          totalRows={source.rows} totalRowsApproximate={source.rows_approximate}
          onLoadingChange={tableId === source.dataset_id ? handleAppliedPreviewLoading : undefined}
          columnTypes={columnTypes} onColumnTypeChange={(column, type) => {
            setTableId(null);
            setColumnType(column, type);
          }} />
        : <article className="panel preview-panel">
          <div className="panel-head"><div><h2>Vista previa</h2>
            <p>{preview.data ? `${preview.data.columns.length} columnas · ${preview.data.rows.length} filas` : "Preparando vista…"}</p></div>
            <div className="preview-meta">
              {preview.isFetching && <Loading
                label={previewMessage || "Consultando las primeras 100 filas…"} inline />}
              {preview.data?.approximate && <span>Muestra aproximada</span>}
            </div>
          </div>
          {preview.error && <div className="error" role="alert">{preview.error.message}</div>}
          {preview.data && preview.data.columns.length > 0 && <div className="table-wrap">
            <table><thead><tr>{preview.data.columns.map((name) => {
              const sourceColumn = allColumns.find((column) => column.name === name);
              return <th key={name}><div className="preview-column-head"><span title={name}>{name}</span>
                {sourceColumn && <select aria-label={`Tipo de ${name}`}
                  value={columnTypes[name] ?? guessType(sourceColumn.type)} onChange={(event) => {
                    setTableId(null);
                    setColumnType(name, event.target.value);
                  }}>
                  {TYPE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>}
              </div></th>;
            })}</tr></thead>
              <tbody>{preview.data.rows.map((row, index) => <tr key={index}>
                {preview.data!.columns.map((name) => <td key={name}>{formatCell(row[name])}</td>)}</tr>)}</tbody>
            </table>
          </div>}
          {preview.data?.columns.length === 0 && <div className="preview-hint">No hay columnas que mostrar con esta configuración.</div>}
        </article>}
    </div>

    <article className="panel variables-panel">
      <div className="panel-head"><div><h2>Variables</h2>
        <p>{allColumns.length} columnas · {rows.length} filas · {columnsRole.length} columnas · {values.length} valores</p></div>
        <div className="panel-actions">
          {build.isPending && <Loading
            label={buildMessage || "Aplicando sobre el dataset completo…"} inline />}
          <button className="quiet-button" onClick={() => hasConfiguration && !tableId ? void preview.refetch() : setRawRefresh((key) => key + 1)}
            disabled={preview.isFetching}>Refrescar</button>
          <button className="quiet-button" onClick={resetBuilder} disabled={build.isPending}>Reset</button>
          <button className="primary" onClick={() => build.mutate()} disabled={build.isPending || !canApply}>
            {build.isPending ? "Aplicando…" : "Aplicar cambios"}</button>
      </div></div>
      {(build.isPending || (waitingForAppliedPreview && tableId === source.dataset_id)) && <div className="table-apply-backdrop">
        <section className="table-apply-dialog" role="dialog" aria-modal="true"
          aria-labelledby="table-apply-title" aria-live="polite">
          <Loading label={buildMessage || (waitingForAppliedPreview
            ? "Cargando las primeras filas de la tabla completa…"
            : "Aplicando los cambios al dataset completo…")} />
          <h2 id="table-apply-title">Aplicando cambios</h2>
          <p>Se está preparando la tabla con los datos completos. Puedes seguir viendo el progreso aquí.</p>
        </section>
      </div>}
      <div className="config-fields">
        <input className="checkbox-search" placeholder="Buscar variable…" value={search} onChange={(event) => setSearch(event.target.value)} />
      </div>
      <div className="variable-grid-heading" aria-hidden="true">
        <span>Variable</span><span>Fila</span><span>Columna</span><span>Valor</span><span>Filtro</span>
      </div>
      <div className="variable-list">
        {visible.length === 0 && <div className="empty-inline">Sin variables que coincidan.</div>}
        {visible.map((column) => {
          const role = roleOf(column.name);
          const filterActive = Object.prototype.hasOwnProperty.call(filters, column.name);
          const value = values.find((item) => item.col === column.name);
          return <div key={column.name} className={`variable-card ${role ? `role-${role}` : filterActive ? "role-filter" : ""}`}>
            <div className="variable-name" title={column.name}>
              <span>{column.name}</span>
              <small>{TYPE_LABELS[columnTypes[column.name] ?? ""] ?? column.type}</small>
            </div>
            {(["row", "column", "value"] as Role[]).map((item) => <button key={item} type="button"
              aria-pressed={role === item} className={`role-button role-${item} ${role === item ? "active" : ""}`}
              onClick={() => setRole(column.name, role === item ? null : item)}>
              {item === "row" ? "Fila" : item === "column" ? "Columna" : "Valor"}
            </button>)}
            <button type="button" aria-pressed={filterActive}
              className={`role-button role-filter ${filterActive ? "active" : ""}`}
              onClick={() => toggleFilter(column.name)}>Filtro</button>
            {value && <div className="variable-extras">
              <select aria-label={`Agregación de ${column.name}`} value={value.agg} onChange={(event) => {
                setTableId(null);
                setValues((current) => current.map((item) => item.col === column.name ? { ...item, agg: event.target.value } : item));
              }}>
                {AGGREGATIONS.map((agg) => <option key={agg} value={agg}>{AGG_LABELS[agg]}</option>)}</select>
              <label><input type="checkbox" checked={value.pivot} disabled={!columnsRole.length}
                onChange={(event) => {
                  setTableId(null);
                  setValues((current) => current.map((item) => item.col === column.name ? { ...item, pivot: event.target.checked } : item));
                }} />Pivotar por columna</label>
              {!numericColumns.includes(column.name) && <small>Sugerencia: usa Recuento para texto.</small>}
              {!columnsRole.length && <small>Asigna primero una variable a Columna para pivotar.</small>}
            </div>}
            {filterActive && <FilterValues datasetId={baseDatasetId} column={column.name}
              selected={filters[column.name] ?? null} onChange={(next) => {
                setTableId(null);
                setFilters((current) => ({ ...current, [column.name]: next }));
              }} />}
          </div>;
        })}
      </div>
    </article>
  </div>;
}

function formatCell(value: unknown) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return Number.isInteger(value) ? value.toLocaleString("es-ES") : value.toFixed(3);
  return String(value);
}

function FilterValues({ datasetId, column, selected, onChange }: {
  datasetId: string; column: string; selected: Array<string | null> | null;
  onChange: (values: Array<string | null> | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [progressMessage, setProgressMessage] = useState("");
  const values = useQuery({
    queryKey: ["column-values", datasetId, column],
    queryFn: () => api.columnValues(datasetId, column, 500, {
      onProgress: (event) => setProgressMessage(event.message),
    }),
    enabled: true,
    staleTime: 15 * 60 * 1000,
    gcTime: 60 * 60 * 1000,
  });
  const options = values.data?.values ?? [];
  const labelFor = (value: string | null) => value === null ? "(nulo)" : value === "" ? "(vacío)" : value;
  const visible = options.filter((value) =>
    labelFor(value).toLocaleLowerCase("es").includes(search.toLocaleLowerCase("es")));
  const toggle = (value: string | null) => onChange(selected === null
    ? options.filter((item) => item !== value)
    : selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value]);

  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [open]);

  return <>
    <div className="filter-values">
      <button type="button" className="filter-toggle" onClick={() => setOpen(true)}>
        {selected === null ? "Todos los valores seleccionados"
          : selected.length ? `${selected.length} valor(es) seleccionado(s)` : "Ningún valor seleccionado"}
        <span aria-hidden="true">⌄</span>
      </button>
    </div>
    {open && <div className="filter-modal-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget) setOpen(false);
    }}>
      <section className="filter-modal" role="dialog" aria-modal="true" aria-labelledby="filter-modal-title">
        <header className="filter-modal-head">
          <div><h2 id="filter-modal-title">Filtrar {column}</h2>
            <p>{selected === null ? "Todos los valores seleccionados" : `${selected.length} valor(es) seleccionado(s)`}</p></div>
          <button type="button" className="quiet-button" aria-label="Cerrar filtro" onClick={() => setOpen(false)}>×</button>
        </header>
        <div className="filter-modal-body">
          <input className="checkbox-search" autoFocus placeholder="Buscar valor…" value={search}
            onChange={(event) => setSearch(event.target.value)} />
          {values.isLoading && <Loading
            label={progressMessage || "Buscando valores en todo el dataset…"} inline />}
          {values.data?.truncated && <div className="empty-inline">
            Demasiados valores distintos; reduce la búsqueda desde el dataset.</div>}
          {!values.isLoading && !values.data?.truncated && <div className="filter-options">
            {visible.length === 0 && <div className="empty-inline">Sin coincidencias.</div>}
            {visible.map((value) => <label key={value === null ? "__null__" : `value:${value}`} className="filter-option">
              <input type="checkbox" checked={selected === null || selected.includes(value)} onChange={() => toggle(value)} />
              <span title={labelFor(value)}>{labelFor(value)}</span></label>)}
          </div>}
        </div>
        <footer className="filter-modal-foot">
          <button type="button" className="quiet-button" onClick={() => onChange(null)}>Marcar todo</button>
          <button type="button" className="quiet-button" onClick={() => onChange([])}>Desmarcar todo</button>
          <button type="button" className="primary" onClick={() => setOpen(false)}>Hecho</button>
        </footer>
      </section>
    </div>}
  </>;
}
