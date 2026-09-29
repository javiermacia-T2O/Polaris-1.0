import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useUiStore } from "../../app/store";
import { api } from "../../shared/api";
import { Loading } from "../../shared/Loading";

const PAGE_SIZE = 100;

export function ResultsScreen() {
  const resultId = useUiStore((state) => state.activeResultId);
  const summary = useQuery({ queryKey: ["analysis-result", resultId],
    queryFn: () => api.analysisResult(resultId!), enabled: Boolean(resultId) });
  const [tableName, setTableName] = useState("");
  const [offset, setOffset] = useState(0);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    setTableName(summary.data?.tables[0] ?? "");
    setOffset(0);
  }, [summary.data]);

  const table = useQuery({ queryKey: ["result-table", resultId, tableName, offset],
    queryFn: () => api.resultTable(resultId!, tableName, offset, PAGE_SIZE),
    enabled: Boolean(resultId && tableName) });

  const exportTable = useMutation({
    mutationFn: async (format: "csv" | "parquet") => {
      const path = await api.selectExportPath(`${tableName || "resultado"}.${format}`, format);
      if (!path) return null;
      return api.exportResult(resultId!, tableName, path, format);
    },
    onSuccess: (path) => setNotice(path ? `Tabla exportada en ${path}` : ""),
  });

  if (!resultId) return <section>
    <div className="empty"><h2>Aún no hay resultados</h2><p>Ejecuta un análisis para explorar métricas y tablas.</p></div>
  </section>;

  return <section>
    {summary.isLoading && <Loading label="Cargando resultados…" />}
    {summary.error && <div className="error" role="alert">No se pudo abrir el resultado: {summary.error.message}</div>}
    {summary.data && <>
      <div className="result-metrics">{Object.entries(summary.data.scalars).map(([label, value]) =>
        <article key={label}><span>{label}</span><strong>{String(value ?? "—")}</strong></article>)}</div>
      {notice && <div className="notice-bar" role="status">{notice}</div>}
      {exportTable.error && <div className="error" role="alert">
        No se pudo exportar: {exportTable.error.message}</div>}
      {summary.data.tables.length === 0 && <div className="empty">
        <h2>Este resultado no incluye tablas</h2><p>Consulta la pestaña Gráficos.</p></div>}
      {summary.data.tables.length > 0 && <article className="panel result-panel">
        <div className="panel-head"><div><h2>Tabla de resultado</h2>
          <p>Acceso paginado, sin transferir el DataFrame completo.</p></div>
          <div className="panel-actions">
            <select value={tableName} onChange={(event) => { setTableName(event.target.value); setOffset(0); }}>
              {summary.data.tables.map((item) => <option key={item}>{item}</option>)}</select>
            <button className="quiet-button" onClick={() => exportTable.mutate("csv")}
              disabled={exportTable.isPending || !tableName}>Exportar CSV</button>
            <button className="quiet-button" onClick={() => exportTable.mutate("parquet")}
              disabled={exportTable.isPending || !tableName}>Parquet</button>
          </div>
        </div>
        {table.isLoading && <Loading label="Cargando tabla…" />}
        {table.error && <div className="error" role="alert">No se pudo cargar la tabla: {table.error.message}</div>}
        {table.data && <><div className="table-wrap"><table><thead><tr>{table.data.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
          <tbody>{table.data.rows.map((row, index) => <tr key={offset + index}>{table.data!.columns.map((column) =>
            <td key={column}>{String(row[column] ?? "—")}</td>)}</tr>)}</tbody></table></div>
          <div className="result-pager"><span>{offset + 1}–{Math.min(table.data.total_rows, offset + PAGE_SIZE)} / {table.data.total_rows.toLocaleString("es-ES")}</span>
            <button onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))} disabled={offset === 0}>Anterior</button>
            <button onClick={() => setOffset(offset + PAGE_SIZE)} disabled={offset + PAGE_SIZE >= table.data.total_rows}>Siguiente</button></div></>}
      </article>}
    </>}
  </section>;
}