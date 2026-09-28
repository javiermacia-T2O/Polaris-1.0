import { useUiStore } from "./store";
import { DatasetsScreen } from "../features/datasets/DatasetsScreen";
import { AnalysesScreen } from "../features/analyses/AnalysesScreen";
import { DiagnosticsScreen } from "../features/diagnostics/DiagnosticsScreen";

const labels = { datasets: "Datos", analyses: "Análisis", diagnostics: "Diagnóstico" } as const;

export function App() {
  const { screen, setScreen } = useUiStore();
  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><span>MA</span><div>Medición Ágil<small>Escritorio local</small></div></div>
      <nav>{(Object.keys(labels) as Array<keyof typeof labels>).map((item) =>
        <button key={item} className={screen === item ? "active" : ""} onClick={() => setScreen(item)}>
          {labels[item]}
        </button>)}</nav>
      <p className="privacy">Los datos permanecen en este equipo.</p>
    </aside>
    <main>
      {screen === "datasets" && <DatasetsScreen />}
      {screen === "analyses" && <AnalysesScreen />}
      {screen === "diagnostics" && <DiagnosticsScreen />}
    </main>
  </div>;
}
