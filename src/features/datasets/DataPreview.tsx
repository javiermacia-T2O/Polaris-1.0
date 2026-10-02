import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../shared/api";
import { TYPE_OPTIONS, guessType } from "../../shared/columnTypes";

const PAGE_SIZE = 100;

export function DataPreview({ datasetId, totalRows, totalRowsApproximate = false, columnTypes, onColumnTypeChange, onLoadingChange }: {
  datasetId: string; totalRows: number; totalRowsApproximate?: boolean;
  columnTypes?: Record<string, string>;
  onColumnTypeChange?: (column: string, type: string) => void;
  onLoadingChange?: (loading: boolean) => void;
}) {
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(100);
  const [sort, setSort] = useState<{ column: string; direction: "asc" | "desc" } | null>(null);
  const preview = useQuery({
    queryKey: ["preview", datasetId, offset, limit, sort],
    queryFn: () => api.tablePage(datasetId, offset, limit, sort ? [sort] : []),
    // While the exact total is being counted in the background, poll so the
    // pager bounds and the "de N filas" label update on their own.
    refetchInterval: (query) => query.state.data?.total_rows_approximate ? 1000 : false,
  });
  useEffect(() => onLoadingChange?.(preview.isFetching),
    [onLoadingChange, preview.isFetching]);
  const total = preview.data?.total_rows ?? totalRows;
  const approximate = preview.data?.total_rows_approximate ?? totalRowsApproximate;
  const totalLabel = approximate
    ? total > 0 ? `~${total.toLocaleString("es-ES")} filas · exacto en curso` : "contando…"
    : `${total.toLocaleString("es-ES")} filas`;
  return <article className="panel preview-panel"><div className="panel-head"><div><h2>Vista previa</h2>
    <p>{preview.data?.columns.length ?? 0} columnas · {totalLabel}</p></div>
    <div className="pager"><select aria-label="Filas por página" value={limit} onChange={(event) => { setLimit(Number(event.target.value)); setOffset(0); }}>
      {[50, 100, 250, 500, 1000].map((size) => <option key={size} value={size}>{size} / página</option>)}</select>
      <button onClick={() => setOffset(Math.max(0, offset - limit))} disabled={!offset}>Anterior</button>
      <span>{total ? offset + 1 : 0}–{Math.min(total, offset + limit)} de {approximate ? "…" : total.toLocaleString("es-ES")}</span>
      <button onClick={() => setOffset(offset + limit)} disabled={approximate || offset + limit >= total}>Siguiente</button></div></div>
    {preview.isLoading && <div className="loading">Cargando vista previa…</div>}
    {preview.error && <div className="error">{preview.error.message}</div>}
    {preview.data && <div className="table-wrap"><table><thead><tr>{preview.data.columns.map((column) => <th key={column}>
      <div className="preview-column-head"><button className="sort-button" onClick={() => { setSort((current) => ({ column, direction: current?.column === column && current.direction === "asc" ? "desc" : "asc" })); setOffset(0); }}>
        {column}{sort?.column === column ? (sort.direction === "asc" ? " ↑" : " ↓") : ""}</button>
        {onColumnTypeChange && <select aria-label={`Tipo de ${column}`}
          value={columnTypes?.[column] ?? guessType("")} onChange={(event) => onColumnTypeChange(column, event.target.value)}>
          {TYPE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>}</div></th>)}</tr></thead>
      <tbody>{preview.data.rows.map((row, index) => <tr key={offset + index}>{preview.data!.columns.map((column) =>
        <td key={column}>{String(row[column] ?? "")}</td>)}</tr>)}</tbody></table></div>}
  </article>;
}
