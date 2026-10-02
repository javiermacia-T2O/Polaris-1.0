import { useState } from "react";
import { useUiStore } from "../../app/store";
import { api } from "../../shared/api";

export type ExportKind = "metrics" | "table" | "all_results" | "chart" | "all_charts";

export function ExportAnalysisDialog({ resultId, kind, name, onClose, onSaved }: {
  resultId: string;
  kind: ExportKind;
  name?: string;
  onClose: () => void;
  onSaved: (message: string) => void;
}) {
  const session = useUiStore((state) => state.exportSessions[resultId]);
  const setSession = useUiStore((state) => state.setExportSession);
  const [destination, setDestination] = useState(session?.destination ?? "");
  const [runName, setRunName] = useState(session?.runName ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const subject = kind === "all_results" ? "todos los resultados" :
    kind === "all_charts" ? "todos los gráficos" :
    kind === "metrics" ? "el resumen de métricas" : name ?? "el resultado";

  async function chooseDirectory() {
    try {
      const path = await api.selectOutputDirectory();
      if (path) setDestination(path);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function save() {
    if (!destination || !runName.trim()) {
      setError("Elige una carpeta y escribe un nombre para este análisis o cliente.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const existingRoot = session?.destination === destination &&
        session.runName === runName.trim() ? session.rootDir : "";
      const result = await api.exportAnalysisBundle(resultId, destination,
        runName.trim(), kind, name ?? "", existingRoot);
      setSession(resultId, { destination, runName: runName.trim(), rootDir: result.root_dir });
      onSaved(`${result.saved.length} archivo(s) guardado(s) en ${result.root_dir}`);
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return <div className="modal-backdrop" onMouseDown={(event) => {
    if (event.target === event.currentTarget && !busy) onClose();
  }}>
    <section className="export-analysis-dialog" role="dialog" aria-modal="true" aria-label="Guardar análisis">
      <h2>Guardar {subject}</h2>
      <p>Los archivos se organizarán por fecha, nombre del análisis y tipo de resultado.</p>
      <label>Nombre del análisis o cliente
        <input autoFocus value={runName} onChange={(event) => setRunName(event.target.value)}
          placeholder="Ej. Análisis septiembre · Cliente A" /></label>
      <div className="export-directory-field"><span>Carpeta de destino</span>
        <div><output title={destination}>{destination || "Ninguna carpeta seleccionada"}</output>
          <button type="button" className="quiet-button" onClick={chooseDirectory}
            disabled={busy}>Elegir…</button></div></div>
      {error && <div className="error" role="alert">{error}</div>}
      <div className="export-dialog-actions">
        <button type="button" className="quiet-button" onClick={onClose} disabled={busy}>Cancelar</button>
        <button type="button" className="primary" onClick={save} disabled={busy}>
          {busy ? "Guardando…" : "Guardar"}</button>
      </div>
    </section>
  </div>;
}
