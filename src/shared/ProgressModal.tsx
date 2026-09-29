import { useEffect, useRef, useState } from "react";
import type { JobStatus } from "./types";

const HEAVY_ANALYSES = ["geox", "geo-test", "causal"];

export function isHeavyAnalysis(analysisId: string) {
  const slug = analysisId.toLocaleLowerCase("es");
  return HEAVY_ANALYSES.some((token) => slug.includes(token));
}

function formatDuration(seconds: number) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes} min ${rest} s`;
}

export function ProgressModal({ analysisName, job, onCancel, onClose }: {
  analysisName: string;
  job: JobStatus | undefined;
  onCancel: () => void;
  onClose: () => void;
}) {
  const startedAt = useRef<number>(Date.now());
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => setElapsed((Date.now() - startedAt.current) / 1000), 500);
    return () => window.clearInterval(timer);
  }, []);

  const progress = job?.progress ?? 0;
  const state = job?.state ?? "QUEUED";
  const running = state === "QUEUED" || state === "RUNNING";
  const estimate = progress > 3 ? (elapsed / progress) * (100 - progress) : null;
  const total = progress > 3 ? (elapsed / progress) * 100 : null;

  return <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="Progreso del análisis">
    <div className="modal-card">
      <header className="modal-head">
        <div>
          <p className="eyebrow">ANÁLISIS PESADO</p>
          <h2>{analysisName}</h2>
        </div>
        {!running && <button className="quiet-button" onClick={onClose}>Cerrar</button>}
      </header>
      <div className="modal-body">
        <div className="progress-track"><div className="progress-fill" style={{ width: `${progress}%` }} /></div>
        <div className="progress-meta">
          <strong>{progress}%</strong>
          <span>{job?.message || statusLabel(state)}</span>
        </div>
        <div className="progress-stats">
          <article><span>Tiempo transcurrido</span><strong>{formatDuration(elapsed)}</strong></article>
          <article><span>Tiempo restante estimado</span><strong>{running ? formatDuration(estimate ?? 0) : "—"}</strong></article>
          <article><span>Duración total estimada</span><strong>{formatDuration(total ?? 0)}</strong></article>
        </div>
        {job?.diagnostics.length ? <div className="progress-log">
          {job.diagnostics.slice(-6).map((item, index) => <p key={`${index}-${item}`}>{item}</p>)}
        </div> : null}
        {job?.error && <div className="error" role="alert">{job.error.message}</div>}
        {state === "COMPLETED" && <div className="notice-bar" role="status">Análisis completado correctamente.</div>}
      </div>
      <footer className="modal-foot">
        {running && <button className="quiet-button" onClick={onCancel}>Cancelar análisis</button>}
        {!running && <button className="primary" onClick={onClose}>Ver resultados</button>}
      </footer>
    </div>
  </div>;
}

function statusLabel(state: string) {
  return ({ QUEUED: "En cola", RUNNING: "Ejecutando", COMPLETED: "Completado",
    FAILED: "Error", CANCELLED: "Cancelado" } as Record<string, string>)[state] ?? state;
}