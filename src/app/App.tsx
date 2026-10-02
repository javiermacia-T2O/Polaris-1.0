import { useEffect } from "react";
import { useUiStore, type Screen } from "./store";
import { AnalysisProvider, useAnalysis } from "./AnalysisContext";
import { Sidebar } from "./Sidebar";
import { KpiCards } from "./KpiCards";
import { DatasetsScreen } from "../features/datasets/DatasetsScreen";
import { AnalysesScreen } from "../features/analyses/AnalysesScreen";
import { ResultsScreen } from "../features/results/ResultsScreen";
import { ChartsScreen } from "../features/results/ChartsScreen";
import { DiagnosticsScreen } from "../features/diagnostics/DiagnosticsScreen";
import { ProgressModal } from "../shared/ProgressModal";

const tabs: Array<{ id: Screen; label: string }> = [
  { id: "datos", label: "Datos" },
  { id: "analisis", label: "Análisis" },
  { id: "resultados", label: "Resultados" },
  { id: "graficos", label: "Gráficos" },
  { id: "diagnostico", label: "Diagnósticos" },
];

export function App() {
  return <AnalysisProvider><Shell /></AnalysisProvider>;
}

function Shell() {
  const { screen, setScreen, status, setStatus } = useUiStore();
  const { selected, job, modalOpen, setModalOpen, cancel, openResults } = useAnalysis();

  useEffect(() => {
    if (!status) return;
    const timer = window.setTimeout(() => setStatus(null), 4000);
    return () => window.clearTimeout(timer);
  }, [status, setStatus]);

  return <div className="shell">
    <Sidebar />
    <div className="workspace">
      <header className="topbar">
        <div className="breadcrumb">Polaris <span>/</span> {tabs.find((tab) => tab.id === screen)?.label}
          {screen === "analisis" && selected && <> <span>/</span> <strong>{selected.name}</strong></>}</div>
        <div className="topbar-right">
          {status && <span className={`status-pill ${status.kind}`} role="status">{status.message}</span>}
          <span className="runtime-badge">LOCAL · PYTHON</span>
        </div>
      </header>
      <KpiCards />
      <nav className="tabs" role="tablist">
        {tabs.map((tab) => <button key={tab.id} role="tab" aria-selected={screen === tab.id}
          className={screen === tab.id ? "active" : ""} onClick={() => setScreen(tab.id)}>
          {screen === tab.id && <span className="tab-arrow" aria-hidden="true">▸</span>}{tab.label}</button>)}
      </nav>
      <main className="content">
        {screen === "datos" && <DatasetsScreen />}
        {screen === "analisis" && <AnalysesScreen />}
        {screen === "resultados" && <ResultsScreen />}
        {screen === "graficos" && <ChartsScreen />}
        {screen === "diagnostico" && <DiagnosticsScreen />}
      </main>
    </div>
    {modalOpen && selected && <ProgressModal analysisName={selected.name} job={job}
      onCancel={cancel} onClose={openResults} />}
  </div>;
}
