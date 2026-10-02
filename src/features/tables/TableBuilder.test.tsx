import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { ColumnInfo, DatasetMetadata, TableRecipe } from "../../shared/types";
import { TableBuilder } from "./TableBuilder";
import { useUiStore } from "../../app/store";

const apiMocks = vi.hoisted(() => ({
  tablePage: vi.fn(),
  columns: vi.fn(),
  previewTable: vi.fn(),
  columnValues: vi.fn(),
  buildTable: vi.fn(),
}));

vi.mock("../../shared/api", () => ({ api: apiMocks }));

const source: DatasetMetadata = {
  dataset_id: "dataset-1", name: "ventas.csv", backend: "duckdb", rows: 2,
  columns: ["Canal", "Ingresos"], types: ["VARCHAR", "DOUBLE"],
  uses_disk: false, source_path: "ventas.csv", rows_approximate: false,
};
const columns: ColumnInfo[] = [
  { name: "Canal", type: "VARCHAR" },
  { name: "Ingresos", type: "DOUBLE" },
];

function renderBuilder() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(<QueryClientProvider client={client}>
    <TableBuilder source={source} columns={columns} />
  </QueryClientProvider>);
  return { ...view, client };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.tablePage.mockResolvedValue({
    dataset_id: source.dataset_id, offset: 0, limit: 100, total_rows: 2,
    columns: ["Canal", "Ingresos"], rows: [{ Canal: "Web", Ingresos: 25 }],
  });
  apiMocks.previewTable.mockImplementation(async (_datasetId: string, recipe: TableRecipe) => ({
    dataset_id: source.dataset_id, offset: 0, limit: 20, total_rows: 1,
    columns: recipe.columns?.length ? recipe.columns : recipe.rows ?? [],
    rows: [{ Canal: "Web" }],
  }));
  apiMocks.columnValues.mockResolvedValue({ column: "Canal", values: ["Web", "Tienda"], truncated: false });
  apiMocks.columns.mockResolvedValue([{ name: "Canal", type: "VARCHAR" }]);
  apiMocks.buildTable.mockResolvedValue("dataset-result");
  useUiStore.getState().setActiveDataset(null);
  useUiStore.getState().setColumnTypes({});
});

test("previsualiza solo la columna seleccionada antes de elegir una métrica", async () => {
  renderBuilder();

  const variable = screen.getByText("Canal").closest(".variable-card");
  expect(variable).not.toBeNull();
  fireEvent.click(within(variable as HTMLElement).getByRole("button", { name: "Columna" }));

  await waitFor(() => expect(apiMocks.previewTable).toHaveBeenLastCalledWith(
    source.dataset_id,
    expect.objectContaining({ rows: [], columns: ["Canal"], values: [], filters: {} }),
    100,
    expect.objectContaining({ onProgress: expect.any(Function) }),
  ));
});

test("el diálogo de filtro actualiza la vista previa al seleccionar un valor", async () => {
  renderBuilder();

  const variable = screen.getByText("Canal").closest(".variable-card");
  fireEvent.click(within(variable as HTMLElement).getByRole("button", { name: "Columna" }));
  fireEvent.click(within(variable as HTMLElement).getByRole("button", { name: "Filtro" }));
  fireEvent.click(await screen.findByRole("button", { name: /Todos los valores seleccionados/ }));

  const dialog = await screen.findByRole("dialog", { name: "Filtrar Canal" });
  const web = await within(dialog).findByRole("checkbox", { name: "Web" });
  expect(web).toBeChecked();
  fireEvent.click(web);

  expect(apiMocks.columnValues).toHaveBeenCalledWith(
    source.dataset_id, "Canal", 500,
    expect.objectContaining({ onProgress: expect.any(Function) }),
  );
  await waitFor(() => expect(apiMocks.previewTable).toHaveBeenLastCalledWith(
    source.dataset_id,
    expect.objectContaining({ columns: ["Canal"], filters: { Canal: ["Tienda"] } }),
    100,
    expect.objectContaining({ onProgress: expect.any(Function) }),
  ));
});

test("el filtro empieza con todo seleccionado y permite desmarcar y volver a marcar todo", async () => {
  renderBuilder();

  const variable = screen.getByText("Canal").closest(".variable-card");
  fireEvent.click(within(variable as HTMLElement).getByRole("button", { name: "Filtro" }));
  fireEvent.click(await screen.findByRole("button", { name: /Todos los valores seleccionados/ }));

  const dialog = await screen.findByRole("dialog", { name: "Filtrar Canal" });
  const web = await within(dialog).findByRole("checkbox", { name: "Web" });
  const store = within(dialog).getByRole("checkbox", { name: "Tienda" });
  expect(web).toBeChecked();
  expect(store).toBeChecked();

  fireEvent.click(within(dialog).getByRole("button", { name: "Desmarcar todo" }));
  expect(web).not.toBeChecked();
  expect(store).not.toBeChecked();
  await waitFor(() => expect(apiMocks.previewTable).toHaveBeenLastCalledWith(
    source.dataset_id,
    expect.objectContaining({ filters: { Canal: [] } }),
    100,
    expect.objectContaining({ onProgress: expect.any(Function) }),
  ));

  fireEvent.click(within(dialog).getByRole("button", { name: "Marcar todo" }));
  expect(web).toBeChecked();
  expect(store).toBeChecked();
});

test("el filtro muestra y aplica valores nulos y vacíos por separado", async () => {
  apiMocks.columnValues.mockResolvedValue({ column: "Canal", values: [null, "", "Web"], truncated: false });
  renderBuilder();
  const variable = screen.getByText("Canal").closest(".variable-card");
  fireEvent.click(within(variable as HTMLElement).getByRole("button", { name: "Filtro" }));
  fireEvent.click(await screen.findByRole("button", { name: /Todos los valores seleccionados/ }));
  const dialog = await screen.findByRole("dialog", { name: "Filtrar Canal" });
  const nullOption = await within(dialog).findByRole("checkbox", { name: "(nulo)" });
  const emptyOption = within(dialog).getByRole("checkbox", { name: "(vacío)" });
  expect(nullOption).toBeChecked();
  expect(emptyOption).toBeChecked();
  fireEvent.click(within(dialog).getByRole("button", { name: "Desmarcar todo" }));
  fireEvent.click(nullOption);
  fireEvent.click(emptyOption);
  await waitFor(() => expect(apiMocks.previewTable).toHaveBeenLastCalledWith(
    source.dataset_id,
    expect.objectContaining({ filters: { Canal: [null, ""] } }),
    100,
    expect.objectContaining({ onProgress: expect.any(Function) }),
  ));
});

test("aplicar activa y cachea el dataset construido sin depender de list_datasets", async () => {
  const { client } = renderBuilder();
  client.setQueryData(["datasets"], [source]);

  const variable = screen.getByText("Canal").closest(".variable-card");
  fireEvent.click(within(variable as HTMLElement).getByRole("button", { name: "Fila" }));
  fireEvent.click(screen.getByRole("button", { name: "Aplicar cambios" }));

  await waitFor(() => expect(apiMocks.buildTable).toHaveBeenCalledWith(
    source.dataset_id,
    expect.objectContaining({ rows: ["Canal"] }),
    expect.objectContaining({ onProgress: expect.any(Function) }),
  ));
  await waitFor(() => expect(useUiStore.getState().activeDatasetId).toBe("dataset-result"));
  expect(client.getQueryData<DatasetMetadata[]>(["datasets"])?.[0]).toMatchObject({
    dataset_id: "dataset-result",
    columns: ["Canal"],
    rows_approximate: true,
  });
});

afterEach(cleanup);
