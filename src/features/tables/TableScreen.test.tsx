import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, expect, test, vi } from "vitest";
import { TableScreen } from "./TableScreen";
import { useUiStore } from "../../app/store";

const mocks = vi.hoisted(() => ({
  listDatasets: vi.fn(), columns: vi.fn(), columnValues: vi.fn(),
  previewTable: vi.fn(), buildTable: vi.fn(), tablePage: vi.fn(),
}));
vi.mock("../../shared/api", () => ({ api: mocks, cancelRequest: vi.fn() }));

beforeEach(() => {
  useUiStore.setState({ activeDatasetId: "d1", columnTypes: { region: "categorica" } });
  mocks.listDatasets.mockResolvedValue([{ dataset_id: "d1", name: "ventas",
    backend: "pandas", rows: 2, columns: ["region"], types: ["string"],
    uses_disk: false, source_path: null, rows_approximate: false }]);
  mocks.columns.mockResolvedValue([{ name: "region", type: "string" }]);
  mocks.columnValues.mockResolvedValue({ column: "region", values: ["Norte"],
    has_more: false, next_cursor: null, version: "v1" });
  mocks.previewTable.mockResolvedValue({ dataset_id: "d1", offset: 0, limit: 0,
    total_rows: 0, columns: [], rows: [], approximate: false,
    total_rows_approximate: false, has_more: false, dataset_version: "v1" });
});

test("Filtro sustituye el rol previo y permanece seleccionado", async () => {
  render(<QueryClientProvider client={new QueryClient()}><TableScreen embedded /></QueryClientProvider>);
  const row = await screen.findByRole("button", { name: "Fila" });
  fireEvent.click(row);
  const filter = screen.getByRole("button", { name: "Filtro" });
  fireEvent.click(filter);
  expect(screen.getByRole("button", { name: "Filtro" })).toHaveClass("active");
  expect(screen.getByRole("button", { name: /Seleccionar valores/ })).toBeEnabled();
});
