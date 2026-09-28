import { create } from "zustand";

export type Screen = "datasets" | "analyses" | "diagnostics";

type UiState = {
  screen: Screen;
  activeDatasetId: string | null;
  setScreen: (screen: Screen) => void;
  setActiveDataset: (datasetId: string | null) => void;
};

export const useUiStore = create<UiState>((set) => ({
  screen: "datasets",
  activeDatasetId: null,
  setScreen: (screen) => set({ screen }),
  setActiveDataset: (activeDatasetId) => set({ activeDatasetId }),
}));
