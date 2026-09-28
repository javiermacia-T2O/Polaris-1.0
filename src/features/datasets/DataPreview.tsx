import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../shared/api";

const PAGE_SIZE = 100;

export function DataPreview({ datasetId, totalRows }: { datasetId: string; totalRows: number }) {
  const [offset, setOffset] = useState(0);
  const preview = useQuery({
    queryKey: ["preview", datasetId, offset],
    queryFn: () => api.preview(datasetId, offset, PAGE_SIZE),
  });
  return <article className="panel"><div className="panel-head"><div><h2>Vista previa</h2>
    <p>Se muestran como máximo {PAGE_SIZE} filas por página.</p></div>
    <div className="pager"><button onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))} disabled={!offset}>Anterior</button>
      <span>{offset + 1}–{Math.min(totalRows, offset + PAGE_SIZE)} de {totalRows.toLocaleString("es-ES")}</span>
      <button onClick={() => setOffset(offset + PAGE_SIZE)} disabled={offset + PAGE_SIZE >= totalRows}>Siguiente</button></div></div>
    {preview.isLoading && <div className="loading">Cargando vista previa…</div>}
    {preview.error && <div className="error">{preview.error.message}</div>}
    {preview.data && <div className="table-wrap"><table><thead><tr>{preview.data.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
      <tbody>{preview.data.rows.map((row, index) => <tr key={offset + index}>{preview.data!.columns.map((column) =>
        <td key={column}>{String(row[column] ?? "")}</td>)}</tr>)}</tbody></table></div>}
  </article>;
}
