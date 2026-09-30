import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, test, vi } from "vitest";
import { App } from "./App";

vi.mock("../shared/api", () => ({ api: {
  listDatasets: vi.fn().mockResolvedValue([]),
  listAnalyses: vi.fn().mockResolvedValue([]),
  health: vi.fn().mockResolvedValue({ status: "ok", protocol: 1 }),
  selectDataset: vi.fn(), loadDataset: vi.fn(), preview: vi.fn(),
  columns: vi.fn().mockResolvedValue([]),
} }));

test("muestra el estado vacío de datos", async () => {
  render(<QueryClientProvider client={new QueryClient()}><App /></QueryClientProvider>);
  expect(await screen.findByText("Aún no hay datasets")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: /Abrir archivo/ })[0]).toBeEnabled();
  expect(screen.queryByRole("tab", { name: "Constructor de tablas" })).not.toBeInTheDocument();
});
