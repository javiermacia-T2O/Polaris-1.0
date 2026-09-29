import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../shared/api";

export function DiagnosticsScreen() {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 10_000 });
  const diagnostics = useQuery({ queryKey: ["diagnostics"], queryFn: api.diagnostics });
  const cache = useQuery({ queryKey: ["cache-info"], queryFn: api.cacheInfo });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["diagnostics"] });
    queryClient.invalidateQueries({ queryKey: ["cache-info"] });
  };

  const clearCache = useMutation({
    mutationFn: api.clearCache,
    onSuccess: (data) => {
      setNotice(`Caché limpiada · ${data.removed} archivos borrados`);
      refresh();
    },
    onError: (error: Error) => setNotice(error.message),
  });

  const freeMemory = useMutation({
    mutationFn: (hard: boolean) => api.freeMemory(hard),
    onSuccess: (data, hard) => {
      setNotice(`Liberación ${hard ? "completa" : "ligera"} · ` +
        `${(data.available_bytes / 2 ** 30).toFixed(1)} GB disponibles`);
      refresh();
    },
    onError: (error: Error) => setNotice(error.message),
  });

  const copy = () => diagnostics.data && navigator.clipboard.writeText(
    JSON.stringify(diagnostics.data, null, 2));

  return <section><header className="page-header"><div><p className="eyebrow">Sistema</p><h1>Diagnóstico</h1>
    <p>Estado del motor local y del canal seguro entre la interfaz y Python.</p></div></header>
    <article className="panel diagnostic"><div className={`status ${health.data ? "ok" : ""}`} />
      <div><h2>Motor científico</h2><p>{health.isLoading ? "Comprobando…" : health.data ?
        `Operativo · protocolo ${health.data.protocol}` : "No disponible"}</p>
        {health.error && <small>{health.error.message}</small>}</div></article>
    {diagnostics.data && <article className="panel diagnostics-data">
      <div className="panel-head"><div><h2>Entorno</h2><p>Información lista para soporte.</p></div>
        <button onClick={copy}>Copiar diagnóstico</button></div>
      <dl><div><dt>Aplicación</dt><dd>{diagnostics.data.app_version}</dd></div>
        <div><dt>Python</dt><dd>{diagnostics.data.python}</dd></div>
        <div><dt>Memoria disponible</dt><dd>{(diagnostics.data.memory.available_bytes / 2 ** 30).toFixed(1)} GB</dd></div>
        <div><dt>Logs</dt><dd>{diagnostics.data.log_path}</dd></div></dl>
    </article>}
    <article className="panel diagnostics-data">
      <div className="panel-head"><div><h2>Mantenimiento</h2>
        <p>Libera recursos sin tocar tus archivos originales.</p></div></div>
      <dl><div><dt>Caché Parquet</dt>
        <dd>{cache.isLoading ? "Calculando…" : `${(cache.data?.size_mb ?? 0).toFixed(1)} MB`}</dd></div></dl>
      <div className="toolbar-actions">
        <button onClick={() => clearCache.mutate()} disabled={clearCache.isPending}>
          {clearCache.isPending ? "Limpiando…" : "Limpiar caché Parquet"}</button>
        <button onClick={() => freeMemory.mutate(false)} disabled={freeMemory.isPending}>
          Liberar memoria</button>
        <button onClick={() => freeMemory.mutate(true)} disabled={freeMemory.isPending}>
          Liberación completa</button>
      </div>
      {notice && <div className="notice-bar" role="status">{notice}</div>}
    </article>
  </section>;
}
