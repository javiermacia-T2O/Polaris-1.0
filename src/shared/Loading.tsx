export function Loading({ label = "Cargando…", inline = false }: {
  label?: string; inline?: boolean;
}) {
  return <div className={inline ? "loading-inline" : "loading"} role="status" aria-live="polite">
    <span className="spinner" aria-hidden="true" />
    <span>{label}</span>
  </div>;
}