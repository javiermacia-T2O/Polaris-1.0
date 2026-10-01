import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../shared/api";
import { TYPE_OPTIONS } from "../../shared/columnTypes";

export function DataPreview({ datasetId, totalRows, totalRowsApproximate = false,
  columnTypes = {}, onColumnTypeChange }: {
  datasetId: string; totalRows: number | null; totalRowsApproximate?: boolean;
  columnTypes?: Record<string, string>;
  onColumnTypeChange?: (column: string, type: string) => void;
}) {
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(100);
  const [sort, setSort] = useState<{ column: string; direction: "asc" | "desc" } | null>(null);
  useEffect(() => { setOffset(0); setSort(null); }, [datasetId]);
  const preview = useQuery({
    queryKey: ["preview", datasetId, offset, limit, sort],
    queryFn: ({ signal }) => api.tablePage(datasetId, offset, limit,
      sort ? [sort] : [], { signal }),
  });
  const total = preview.data?.total_rows ?? totalRows;
  const approximate = preview.data?.total_rows_approximate ?? totalRowsApproximate;
  const hasMore = preview.data?.has_more ?? (total !== null && offset + limit < total);
  const totalLabel = approximate || total === null ? "contando…" : `${total.toLocaleString("es-ES")} filas`;
  const shown = preview.data?.rows.length ?? 0;
  const end = Math.min(total ?? offset + shown, offset + shown);
  return <article className="panel raw-preview"><div className="panel-head"><div>
    <div className="heading-with-live"><h2>Vista previa</h2><span className="live-badge"><i />Dataset original</span></div>
    <p>{preview.data?.columns.length ?? 0} columnas · {totalLabel}</p></div>
    <div className="pager"><select aria-label="Filas por página" value={limit} onChange={(event) => { setLimit(Number(event.target.value)); setOffset(0); }}>
      {[50, 100, 250, 500, 1000].map((size) => <option key={size} value={size}>{size} / página</option>)}</select>
      <button onClick={() => setOffset(Math.max(0, offset - limit))} disabled={!offset}>Anterior</button>
      <span>{shown ? offset + 1 : 0}–{end} de {approximate || total === null ? "…" : total.toLocaleString("es-ES")}</span>
      <button onClick={() => setOffset(offset + limit)} disabled={!hasMore}>Siguiente</button></div></div>
    {preview.isLoading && <div className="loading">Cargando vista previa…</div>}
    {preview.error && <div className="error">{preview.error.message}</div>}
    {preview.data && <div className="table-wrap"><table><thead><tr>{preview.data.columns.map((column) => <th key={column}>
      <button className="sort-button" onClick={() => { setSort((current) => ({ column, direction: current?.column === column && current.direction === "asc" ? "desc" : "asc" })); setOffset(0); }}>
        {column}{sort?.column === column ? (sort.direction === "asc" ? " ↑" : " ↓") : ""}</button>
      {onColumnTypeChange && <select className="column-type-select" aria-label={`Tipo de ${column}`}
        value={columnTypes[column] ?? "texto"} onChange={(event) => onColumnTypeChange(column, event.target.value)}>
        {TYPE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select>}
    </th>)}</tr></thead>
      <tbody>{preview.data.rows.map((row, index) => <tr key={offset + index}>{preview.data!.columns.map((column) =>
        <td key={column}>{String(row[column] ?? "")}</td>)}</tr>)}</tbody></table></div>}
  </article>;
}
