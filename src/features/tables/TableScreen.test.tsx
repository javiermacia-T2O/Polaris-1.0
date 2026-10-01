import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { TableScreen } from "./TableScreen";
import { useUiStore } from "../../app/store";

const mocks = vi.hoisted(() => ({
  listDatasets: vi.fn(), columns: vi.fn(), columnValues: vi.fn(),
  previewTable: vi.fn(), buildTable: vi.fn(), tablePage: vi.fn(),
}));
vi.mock("../../shared/api", () => ({ api: mocks, cancelRequest: vi.fn() }));

const rawPage = { dataset_id: "d1", offset: 0, limit: 100,
  total_rows: 2, columns: ["region", "sales"],
  rows: [{ region: "Norte", sales: 10 }, { region: "Sur", sales: 20 }],
  approximate: false, total_rows_approximate: false, has_more: false,
  dataset_version: "v1" };

beforeEach(() => {
  vi.clearAllMocks();
  useUiStore.setState({ activeDatasetId: "d1", columnTypes: {
    region: "categorica", sales: "numero",
  }, status: null });
  mocks.listDatasets.mockResolvedValue([{ dataset_id: "d1", name: "ventas",
    backend: "pandas", rows: 2, columns: ["region", "sales"],
    types: ["string", "int64"], uses_disk: false, source_path: null,
    rows_approximate: false }]);
  mocks.columns.mockResolvedValue([{ name: "region", type: "string" },
    { name: "sales", type: "int64" }]);
  mocks.columnValues.mockResolvedValue({ column: "region", values: ["Norte", "Sur"],
    has_more: false, next_cursor: null, version: "v1" });
  mocks.tablePage.mockResolvedValue(rawPage);
  mocks.previewTable.mockResolvedValue({ ...rawPage, limit: 20,
    columns: ["region"], rows: [{ region: "Norte" }, { region: "Sur" }] });
});

afterEach(() => cleanup());

function renderBuilder() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><TableScreen embedded /></QueryClientProvider>);
}

test("Filtro sustituye el rol previo, permanece seleccionado y abre el modal", async () => {
  renderBuilder();
  const row = (await screen.findAllByRole("button", { name: "Fila" }))[0]!;
  fireEvent.click(row);
  const filter = screen.getAllByRole("button", { name: "Filtro" })[0]!;
  fireEvent.click(filter);

  expect(filter).toHaveClass("active");
  expect(await screen.findByRole("dialog", { name: "region" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Aplicar filtro" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Quitar rol de region" })).toBeEnabled();
});

test("asignar Fila actualiza la misma vista previa con una receta acotada", async () => {
  renderBuilder();
  fireEvent.click((await screen.findAllByRole("button", { name: "Fila" }))[0]!);

  await waitFor(() => expect(mocks.previewTable).toHaveBeenCalled());
  const [, recipe, limit, options] = mocks.previewTable.mock.calls.at(-1)!;
  expect(recipe.rows).toEqual(["region"]);
  expect(recipe.columns).toEqual([]);
  expect(limit).toBe(20);
  expect(options.signal).toBeInstanceOf(AbortSignal);
  expect(await screen.findByText(/2 filas de muestra/)).toBeInTheDocument();
});

test("Aplicar no confirma éxito hasta que la nueva vista previa está disponible", async () => {
  renderBuilder();
  await screen.findByText("Norte");

  const derived = { ...rawPage, dataset_id: "d2", dataset_version: "v2" };
  let resolvePreview!: (page: typeof derived) => void;
  const pendingPreview = new Promise<typeof derived>((resolve) => { resolvePreview = resolve; });
  mocks.tablePage.mockImplementationOnce(() => pendingPreview);
  mocks.buildTable.mockResolvedValue("d2");
  mocks.listDatasets.mockResolvedValue([
    { dataset_id: "d1", name: "ventas", backend: "pandas", rows: 2,
      columns: ["region", "sales"], types: ["string", "int64"], uses_disk: false,
      source_path: null, rows_approximate: false },
    { dataset_id: "d2", name: "ventas modelada", backend: "duckdb", rows: 2,
      columns: ["region"], types: ["string"], uses_disk: true,
      source_path: null, rows_approximate: false },
  ]);

  fireEvent.click((await screen.findAllByRole("button", { name: "Fila" }))[0]!);
  fireEvent.click(screen.getByRole("button", { name: "Aplicar cambios" }));
  await waitFor(() => expect(mocks.buildTable).toHaveBeenCalled());
  expect(useUiStore.getState().status).toMatchObject({ kind: "info" });

  resolvePreview(derived);
  await waitFor(() => expect(useUiStore.getState().status).toMatchObject({
    kind: "success", message: expect.stringContaining("vista previa actualizadas"),
  }));
});
