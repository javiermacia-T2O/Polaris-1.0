import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { RegressionWorkbench } from "./RegressionWorkbench";

const apiMock = vi.hoisted(() => ({ regressionPreview: vi.fn(), columns: vi.fn(), dateColumns: vi.fn() }));
vi.mock("../../shared/api", () => ({ api: apiMock }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

test("muestra correlación/VIF y conserva pares de interacción en la configuración", async () => {
  apiMock.columns.mockResolvedValue([
    { name: "date", type: "DATE" }, { name: "sales", type: "DOUBLE" },
    { name: "x", type: "DOUBLE" }, { name: "z", type: "DOUBLE" },
  ]);
  apiMock.dateColumns.mockResolvedValue([{ column: "date", granularity: "Diario" }]);
  apiMock.regressionPreview.mockResolvedValue({
    dataset_id: "dataset-regression", offset: 0, limit: 4, total_rows: 4,
    approximate: true, total_rows_approximate: true,
    columns: ["date", "sales", "x", "z"],
    rows: [1, 2, 3, 4].map((value) => ({
      date: `2025-01-0${value}`, sales: value * 2, x: value, z: value,
    })),
  });
  const fields = [
    { key: "regression_type", type: "select", options: ["OLS"], default: "OLS" },
    { key: "date_col", type: "select", options: ["date"], default: "date" },
    { key: "target_col", type: "select", options: ["sales"], default: "sales" },
    { key: "input_cols", type: "multi", options: ["sales", "x", "z", "missing"], default: [] },
  ];
  function Harness() {
    const [parameters, setParameters] = useState<Record<string, unknown>>({
      regression_type: "OLS", date_col: "date", target_col: "sales", input_cols: [],
    });
    return <RegressionWorkbench datasetId="dataset-regression" fields={fields}
      parameters={parameters} setParameter={(key, value) => setParameters((current) => ({ ...current, [key]: value }))}
      run={() => undefined} runPending={false} runDisabled={false} />;
  }
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>);

  await screen.findByText("4 fechas");
  expect(screen.queryByRole("checkbox", { name: "missing" })).not.toBeInTheDocument();
  expect(apiMock.regressionPreview).toHaveBeenCalledWith("dataset-regression", "date", ["sales", "x", "z"], 1200);
  fireEvent.click(screen.getAllByRole("checkbox", { name: "x" })[0]!);
  fireEvent.click(screen.getAllByRole("checkbox", { name: "z" })[0]!);
  await waitFor(() => expect(screen.getByText("Variables seleccionadas (2)")).toBeInTheDocument());
  await waitFor(() => expect(screen.getAllByText("∞")).toHaveLength(2));
  expect(screen.getAllByText("+1.00").length).toBeGreaterThan(0);

  fireEvent.click(screen.getByText("Interacciones (hasta 3)"));
  fireEvent.change(screen.getByRole("combobox", { name: "Primera variable de interacción" }), { target: { value: "x" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Segunda variable de interacción" }), { target: { value: "z" } });
  fireEvent.click(screen.getByRole("button", { name: "Añadir" }));
  expect(screen.getByText("x × z")).toBeInTheDocument();
});

test("marca dos eventos consecutivos en las fechas arrastradas", async () => {
  apiMock.columns.mockResolvedValue([
    { name: "date", type: "DATE" }, { name: "sales", type: "DOUBLE" },
    { name: "spend", type: "DOUBLE" },
  ]);
  apiMock.dateColumns.mockResolvedValue([{ column: "date", granularity: "Diario" }]);
  apiMock.regressionPreview.mockResolvedValue({
    dataset_id: "fixture", offset: 0, limit: 4, total_rows: 4,
    columns: ["date", "sales", "spend"],
    rows: [1, 2, 3, 4].map((day) => ({
      date: `2025-01-0${day}`, sales: day * 10, spend: day * 2,
    })),
  });
  const fields = [
    { key: "regression_type", type: "select", options: ["OLS"] },
    { key: "date_col", type: "select", options: ["date"] },
    { key: "target_col", type: "select", options: ["sales"] },
    { key: "input_cols", type: "multi", options: ["sales", "spend"] },
  ];
  function Harness() {
    const [parameters, setParameters] = useState<Record<string, unknown>>({
      regression_type: "OLS", date_col: "date", target_col: "sales", input_cols: [], events: [],
    });
    return <RegressionWorkbench datasetId="fixture" fields={fields}
      parameters={parameters} setParameter={(key, value) => setParameters((current) => ({ ...current, [key]: value }))}
      run={() => undefined} runPending={false} runDisabled={false} />;
  }
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>);
  await screen.findByText("4 fechas");
  const svg = screen.getByRole("img", { name: /Serie temporal de sales/ }) as unknown as SVGSVGElement;
  svg.setPointerCapture = vi.fn();
  vi.spyOn(svg, "getBoundingClientRect").mockReturnValue({
    x: 0, y: 0, left: 0, top: 0, right: 900, bottom: 480,
    width: 900, height: 480, toJSON: () => ({}),
  });
  const mark = async (from: number, to: number) => {
    fireEvent.click(screen.getByRole("button", { name: /Marcar eventos/ }));
    fireEvent.pointerDown(svg, { pointerId: 1, clientX: from });
    fireEvent.pointerMove(svg, { pointerId: 1, clientX: to });
    fireEvent.pointerUp(svg, { pointerId: 1, clientX: to });
  };
  await mark(80, 320);
  await waitFor(() => expect(screen.getByText("Eventos registrados (1)")).toBeInTheDocument());
  await mark(580, 820);
  await waitFor(() => expect(screen.getByText("Eventos registrados (2)")).toBeInTheDocument());
  const rows = document.querySelectorAll(".regression-event-table-row");
  expect(rows[0]?.textContent).toContain("2025-01-01");
  expect(rows[0]?.textContent).toContain("2025-01-02");
  expect(rows[1]?.textContent).toContain("2025-01-03");
  expect(rows[1]?.textContent).toContain("2025-01-04");
});
