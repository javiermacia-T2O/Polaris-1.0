import { create } from "zustand";

export type Screen = "datos" | "tabla" | "analisis" | "resultados" | "graficos" | "diagnostico";

export type StatusKind = "info" | "success" | "warning" | "error";
export type Status = { message: string; kind: StatusKind } | null;

type UiState = {
  screen: Screen;
  activeDatasetId: string | null;
  activeResultId: string | null;
  selectedAnalysisId: string | null;
  status: Status;
  columnTypes: Record<string, string>;
  setScreen: (screen: Screen) => void;
  setActiveDataset: (datasetId: string | null) => void;
  setActiveResult: (resultId: string | null) => void;
  setSelectedAnalysis: (analysisId: string | null) => void;
  setStatus: (status: Status) => void;
  setColumnTypes: (types: Record<string, string>) => void;
  setColumnType: (column: string, type: string) => void;
};

export const useUiStore = create<UiState>((set) => ({
  screen: "datos",
  activeDatasetId: null,
  activeResultId: null,
  selectedAnalysisId: null,
  status: null,
  columnTypes: {},
  setScreen: (screen) => set({ screen }),
  setActiveDataset: (activeDatasetId) => set({ activeDatasetId }),
  setActiveResult: (activeResultId) => set({ activeResultId }),
  setSelectedAnalysis: (selectedAnalysisId) => set({ selectedAnalysisId }),
  setStatus: (status) => set({ status }),
  setColumnTypes: (columnTypes) => set({ columnTypes }),
  setColumnType: (column, type) => set((state) => ({
    columnTypes: { ...state.columnTypes, [column]: type },
  })),
}));
