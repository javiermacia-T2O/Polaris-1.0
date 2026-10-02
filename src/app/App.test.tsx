import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, test, vi } from "vitest";
import { App } from "./App";
import { useUiStore } from "./store";
import { api } from "../shared/api";
import type { DatasetMetadata } from "../shared/types";

vi.mock("../shared/api", () => ({ api: {
  listDatasets: vi.fn().mockResolvedValue([]),
  listAnalyses: vi.fn().mockResolvedValue([]),
  health: vi.fn().mockResolvedValue({ status: "ok", protocol: 1 }),
  selectDataset: vi.fn(), loadDataset: vi.fn(), closeDataset: vi.fn(), preview: vi.fn(),
  columns: vi.fn().mockResolvedValue([]),
} }));

test("muestra el estado vacío de datos", async () => {
  render(<QueryClientProvider client={new QueryClient()}><App /></QueryClientProvider>);
  expect(await screen.findByText("Aún no hay datasets")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: /Abrir archivo/ })[0]).toBeEnabled();
});

test("Análisis espera la selección desde la barra lateral", async () => {
  const dataset: DatasetMetadata = { dataset_id: "analysis-empty", name: "datos.csv",
    backend: "pandas", rows: 2, columns: ["Fecha", "Objetivo"],
    types: ["datetime64[ns]", "float64"], uses_disk: false,
    source_path: "datos.csv", rows_approximate: false };
  const client = new QueryClient();
  client.setQueryData(["datasets"], [dataset]);
  useUiStore.getState().setActiveDataset(dataset.dataset_id);
  useUiStore.getState().setSelectedAnalysis(null);
  useUiStore.getState().setScreen("analisis");
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
  expect(await screen.findByText("Selecciona un análisis")).toBeInTheDocument();
  expect(document.querySelector(".analysis-nav")).toBeNull();
  useUiStore.getState().setActiveDataset(null);
  useUiStore.getState().setScreen("datos");
});

test("muestra que el KPI de filas sigue contando cuando la metadata es provisional", () => {
  const dataset: DatasetMetadata = {
    dataset_id: "dataset-counting", name: "large.csv", backend: "duckdb",
    rows: 0, columns: ["Canal", "Venta"], types: ["VARCHAR", "DOUBLE"],
    uses_disk: true, source_path: "large.csv", rows_approximate: true,
  };
  const client = new QueryClient();
  client.setQueryData(["datasets"], [dataset]);
  useUiStore.getState().setActiveDataset(dataset.dataset_id);

  render(<QueryClientProvider client={client}><App /></QueryClientProvider>);

  const rowsCard = screen.getByText("FILAS").closest(".kpi-card");
  expect(rowsCard).toHaveTextContent("Contando…");
  useUiStore.getState().setActiveDataset(null);
});

test("muestra el conteo aproximado mientras el total exacto sigue en curso", () => {
  const dataset: DatasetMetadata = {
    dataset_id: "dataset-estimating", name: "large.csv", backend: "duckdb_csv",
    rows: 1_234_567, columns: ["Canal"], types: ["VARCHAR"],
    uses_disk: true, source_path: "large.csv", rows_approximate: true,
  };
  const client = new QueryClient();
  client.setQueryData(["datasets"], [dataset]);
  useUiStore.getState().setActiveDataset(dataset.dataset_id);

  render(<QueryClientProvider client={client}><App /></QueryClientProvider>);

  expect(screen.getByText("~1.234.567")).toBeInTheDocument();
  useUiStore.getState().setActiveDataset(null);
});

test("muestra el número de columnas desde metadata sin una segunda petición", async () => {
  const dataset: DatasetMetadata = {
    dataset_id: "dataset-columns", name: "large.csv", backend: "duckdb_csv",
    rows: 0, columns: ["Canal", "Venta"], types: ["VARCHAR", "DOUBLE"],
    uses_disk: true, source_path: "large.csv", rows_approximate: true,
  };
  const client = new QueryClient();
  client.setQueryData(["datasets"], [dataset]);
  vi.mocked(api.listDatasets).mockResolvedValue([dataset]);
  vi.mocked(api.columns).mockClear();
  useUiStore.getState().setActiveDataset(dataset.dataset_id);

  render(<QueryClientProvider client={client}><App /></QueryClientProvider>);

  expect(screen.getByText("2 columnas · 0 filas · 0 columnas · 0 valores"))
    .toBeInTheDocument();
  expect(api.columns).not.toHaveBeenCalled();
  useUiStore.getState().setActiveDataset(null);
});

test("Abrir archivo reemplaza el dataset activo después de cargar el nuevo", async () => {
  const previous: DatasetMetadata = {
    dataset_id: "dataset-old", name: "masivo.csv", backend: "duckdb_csv",
    rows: 100_000, columns: ["Canal"], types: ["VARCHAR"],
    uses_disk: true, source_path: "masivo.csv", rows_approximate: false,
  };
  const replacement: DatasetMetadata = {
    ...previous, dataset_id: "dataset-new", name: "normal.csv",
    backend: "pandas", rows: 250_000, uses_disk: false,
    source_path: "normal.csv",
  };
  const client = new QueryClient();
  client.setQueryData(["datasets"], [previous]);
  vi.mocked(api.listDatasets).mockImplementation(async () =>
    vi.mocked(api.closeDataset).mock.calls.length ? [replacement] : [previous]);
  vi.mocked(api.selectDataset).mockResolvedValue("normal.csv");
  const loadDataset = vi.spyOn(api, "loadDataset").mockResolvedValue(replacement);
  const closeDataset = vi.spyOn(api, "closeDataset").mockResolvedValue(undefined);
  useUiStore.getState().setActiveDataset(previous.dataset_id);

  render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
  fireEvent.click(screen.getAllByRole("button", { name: "Abrir archivo" })[0]!);

  await waitFor(() => expect(closeDataset).toHaveBeenCalledWith(previous.dataset_id));
  expect(loadDataset.mock.invocationCallOrder[0]!)
    .toBeLessThan(closeDataset.mock.invocationCallOrder[0]!);
  await waitFor(() => {
    expect(client.getQueryData<DatasetMetadata[]>(["datasets"]))
      .toEqual([replacement]);
  });
  expect(useUiStore.getState().activeDatasetId).toBe(replacement.dataset_id);
});

afterEach(cleanup);
