import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, test, vi } from "vitest";
import { useUiStore } from "../../app/store";
import { api } from "../../shared/api";
import { ResultsScreen } from "./ResultsScreen";
import { ChartsScreen } from "./ChartsScreen";

vi.mock("../../shared/api", () => ({ api: {
  analysisResult: vi.fn(), resultTable: vi.fn(), resultChart: vi.fn(),
  selectExportPath: vi.fn(), exportResult: vi.fn(), saveChart: vi.fn(),
  selectOutputDirectory: vi.fn(), exportAnalysisBundle: vi.fn(),
} }));

function wrapper(children: React.ReactNode) {
  return <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    {children}</QueryClientProvider>;
}

afterEach(() => { cleanup(); useUiStore.getState().setActiveResult(null); vi.clearAllMocks(); });

test("los resultados separan indicadores y tablas navegables", async () => {
  useUiStore.getState().setActiveResult("result-1");
  vi.mocked(api.analysisResult).mockResolvedValue({
    result_id: "result-1", analysis_id: "regression",
    tables: ["Métricas in-sample (diagnóstico)", "Coeficientes (con IC 95/90/85%)"],
    charts: ["Ajuste · Real vs Predicho"],
    scalars: { RMSE: 1.23456, "Diagnóstico de ejecución": "registro largo" },
  });
  vi.mocked(api.resultTable).mockResolvedValue({ result_id: "result-1",
    table: "Métricas in-sample (diagnóstico)", offset: 0, limit: 100,
    total_rows: 1, columns: ["Métrica", "Valor"], rows: [{ "Métrica": "R²", "Valor": .85 }],
  });
  render(wrapper(<ResultsScreen />));
  expect(await screen.findByRole("table")).toHaveTextContent("1,2346");
  expect(screen.queryByText("registro largo")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Ajuste" }));
  await waitFor(() => expect(api.resultTable).toHaveBeenCalledWith(
    "result-1", "Métricas in-sample (diagnóstico)", 0, 100));
  expect(await screen.findByRole("table")).toHaveTextContent("R²");
});

test("los gráficos avanzan y el ajuste muestra sus métricas", async () => {
  useUiStore.getState().setActiveResult("result-2");
  vi.mocked(api.analysisResult).mockResolvedValue({
    result_id: "result-2", analysis_id: "regression",
    tables: ["Métricas in-sample (diagnóstico)"],
    charts: ["Ajuste · Real vs Predicho", "Residuos y diagnóstico"], scalars: {},
  });
  vi.mocked(api.resultChart).mockImplementation(async (_id, chart) => ({
    result_id: "result-2", chart, mime_type: "image/png", data_base64: "AAAA",
  }));
  vi.mocked(api.resultTable).mockResolvedValue({ result_id: "result-2",
    table: "Métricas in-sample (diagnóstico)", offset: 0, limit: 30,
    total_rows: 1, columns: ["Métrica", "Valor"], rows: [{ "Métrica": "RMSE", "Valor": 1.2 }],
  });
  render(wrapper(<ChartsScreen />));
  expect(await screen.findByRole("img", { name: "Ajuste · Real vs Predicho" })).toBeInTheDocument();
  expect(await screen.findByText("Métricas de ajuste")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Gráfico siguiente" }));
  expect(await screen.findByRole("img", { name: "Residuos y diagnóstico" })).toBeInTheDocument();
});

test("el guardado conjunto pide carpeta y nombre y conserva la estructura", async () => {
  useUiStore.getState().setActiveResult("result-export");
  vi.mocked(api.analysisResult).mockResolvedValue({
    result_id: "result-export", analysis_id: "regression",
    tables: [], charts: [], scalars: { RMSE: 1.2 },
  });
  vi.mocked(api.selectOutputDirectory).mockResolvedValue("C:\\Informes");
  vi.mocked(api.exportAnalysisBundle).mockResolvedValue({
    root_dir: "C:\\Informes\\2026-10-02\\Cliente A\\Regresión",
    saved: ["resumen.csv"],
  });
  render(wrapper(<ResultsScreen />));
  fireEvent.click(await screen.findByRole("button", { name: "Guardar todos los resultados" }));
  fireEvent.change(screen.getByPlaceholderText("Ej. Análisis septiembre · Cliente A"),
    { target: { value: "Cliente A" } });
  fireEvent.click(screen.getByRole("button", { name: "Elegir…" }));
  await waitFor(() => expect(screen.getByText("C:\\Informes")).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: /^Guardar$/ }));
  await waitFor(() => expect(api.exportAnalysisBundle).toHaveBeenCalledWith(
    "result-export", "C:\\Informes", "Cliente A", "all_results", "", ""));
});

test("cada gráfico ofrece su propio guardado y el guardado conjunto", async () => {
  useUiStore.getState().setActiveResult("result-chart-save");
  vi.mocked(api.analysisResult).mockResolvedValue({
    result_id: "result-chart-save", analysis_id: "regression", tables: [],
    charts: ["Ajuste · Real vs Predicho", "Residuos y diagnóstico"], scalars: {},
  });
  vi.mocked(api.resultChart).mockImplementation(async (_id, chart) => ({
    result_id: "result-chart-save", chart, mime_type: "image/png", data_base64: "AAAA",
  }));
  render(wrapper(<ChartsScreen />));
  expect(await screen.findByRole("button", { name: "Guardar todos los gráficos" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Guardar PNG" }));
  expect(screen.getByRole("dialog", { name: "Guardar análisis" })).toHaveTextContent(
    "Ajuste · Real vs Predicho");
});
