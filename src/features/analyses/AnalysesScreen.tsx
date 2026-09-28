import { useQuery } from "@tanstack/react-query";
import { api } from "../../shared/api";

export function AnalysesScreen() {
  const analyses = useQuery({ queryKey: ["analyses"], queryFn: api.listAnalyses });
  return <section><header className="page-header"><div><p className="eyebrow">Catálogo</p><h1>Análisis</h1>
    <p>Los cálculos se ejecutan en el motor científico Python.</p></div></header>
    {analyses.isLoading && <div className="loading">Leyendo catálogo…</div>}
    {analyses.error && <div className="error">{analyses.error.message}</div>}
    <div className="analysis-grid">{analyses.data?.map((analysis) => <article key={analysis.id} className="analysis-card">
      <span>{analysis.category}</span><h2>{analysis.name}</h2><p>{analysis.description}</p>
      {analysis.caution && <small>{analysis.caution}</small>}<button disabled>Configurar próximamente</button>
    </article>)}</div>
  </section>;
}
