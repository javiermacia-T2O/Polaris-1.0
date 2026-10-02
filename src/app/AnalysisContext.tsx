import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../shared/api";
import type { AnalysisManifest, JobStatus, ResultSummary } from "../shared/types";
import { isHeavyAnalysis } from "../shared/ProgressModal";
import { useUiStore } from "./store";

type AnalysisContextValue = {
  analyses: AnalysisManifest[];
  analysesLoading: boolean;
  analysesError: Error | null;
  selected: AnalysisManifest | null;
  selectAnalysis: (analysisId: string) => void;
  manifest: AnalysisManifest | undefined;
  manifestLoading: boolean;
  manifestError: Error | null;
  parameters: Record<string, unknown>;
  setParameter: (key: string, value: unknown) => void;
  run: () => void;
  runPending: boolean;
  runError: Error | null;
  job: JobStatus | undefined;
  result: ResultSummary | undefined;
  jobId: string | null;
  modalOpen: boolean;
  setModalOpen: (open: boolean) => void;
  cancel: () => void;
  reset: () => void;
  openResults: () => void;
};

const AnalysisContext = createContext<AnalysisContextValue | null>(null);

export function AnalysisProvider({ children }: { children: ReactNode }) {
  const datasetId = useUiStore((state) => state.activeDatasetId);
  const selectedAnalysisId = useUiStore((state) => state.selectedAnalysisId);
  const setSelectedAnalysis = useUiStore((state) => state.setSelectedAnalysis);
  const setActiveResult = useUiStore((state) => state.setActiveResult);
  const setScreen = useUiStore((state) => state.setScreen);
  const setStatus = useUiStore((state) => state.setStatus);

  const [parameters, setParameters] = useState<Record<string, unknown>>({});
  const initializedManifest = useRef("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  const analyses = useQuery({ queryKey: ["analyses"], queryFn: api.listAnalyses });
  const selected = useMemo(() =>
    analyses.data?.find((item) => item.id === selectedAnalysisId) ?? null,
    [analyses.data, selectedAnalysisId]);

  const manifest = useQuery({
    queryKey: ["analysis-manifest", selectedAnalysisId, datasetId],
    queryFn: () => api.analysisManifest(selectedAnalysisId!, datasetId!),
    enabled: Boolean(selectedAnalysisId && datasetId),
  });

  const fields = useMemo(() => schemaFields(manifest.data?.parameter_schema), [manifest.data]);

  useEffect(() => {
    if (!manifest.data || !selectedAnalysisId || !datasetId) return;
    const key = `${selectedAnalysisId}\u001f${datasetId}`;
    if (initializedManifest.current === key) return;
    initializedManifest.current = key;
    const defaults = Object.fromEntries(fields.filter((field) => field.key).map((field) => [
      field.key!, field.default ?? (field.type === "multi" ? [] : field.type === "checkbox" ? false : ""),
    ]));
    setParameters(defaults);
    setJobId(null);
    setModalOpen(false);
  }, [selectedAnalysisId, datasetId, manifest.data, fields]);

  const setParameter = useCallback((key: string, value: unknown) => {
    setParameters((current) => ({ ...current, [key]: value }));
  }, []);

  const runMutation = useMutation({
    mutationFn: () => api.runAnalysis(selectedAnalysisId!, datasetId!, parameters),
    onSuccess: (id) => {
      setJobId(id);
      if (selectedAnalysisId && isHeavyAnalysis(selectedAnalysisId)) {
        setModalOpen(true);
      } else {
        setStatus({ message: "Ejecutando análisis…", kind: "info" });
      }
    },
    onError: (error: Error) => setStatus({ message: error.message, kind: "error" }),
  });

  const job = useQuery({
    queryKey: ["analysis-job", jobId],
    queryFn: () => api.jobStatus(jobId!),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      return state && ["COMPLETED", "FAILED", "CANCELLED"].includes(state) ? false : 700;
    },
  });

  const result = useQuery({
    queryKey: ["analysis-result", job.data?.result_id],
    queryFn: () => api.analysisResult(job.data!.result_id!),
    enabled: Boolean(job.data?.result_id),
  });

  useEffect(() => {
    const state = job.data?.state;
    if (!state) return;
    if (state === "COMPLETED") {
      setStatus({ message: "Análisis completado. Resultados listos.", kind: "success" });
    } else if (state === "FAILED") {
      setStatus({ message: job.data?.message || "El análisis ha fallado.", kind: "error" });
    } else if (state === "CANCELLED") {
      setStatus({ message: "Análisis cancelado.", kind: "warning" });
    }
  }, [job.data?.state, job.data?.message, setStatus]);

  const selectAnalysis = useCallback((analysisId: string) => {
    setSelectedAnalysis(analysisId);
    setScreen("analisis");
  }, [setSelectedAnalysis, setScreen]);

  const run = useCallback(() => {
    if (!selectedAnalysisId || !datasetId) return;
    runMutation.mutate();
  }, [datasetId, runMutation, selectedAnalysisId]);

  const cancel = useCallback(() => { if (jobId) void api.cancelJob(jobId); }, [jobId]);
  const reset = useCallback(() => { setJobId(null); setModalOpen(false); }, []);
  const openResults = useCallback(() => {
    if (result.data) { setActiveResult(result.data.result_id); setScreen("resultados"); }
    setModalOpen(false);
  }, [result.data, setActiveResult, setScreen]);

  const value: AnalysisContextValue = {
    analyses: analyses.data ?? [],
    analysesLoading: analyses.isLoading,
    analysesError: analyses.error as Error | null,
    selected,
    selectAnalysis,
    manifest: manifest.data,
    manifestLoading: manifest.isLoading,
    manifestError: manifest.error as Error | null,
    parameters,
    setParameter,
    run,
    runPending: runMutation.isPending,
    runError: runMutation.error as Error | null,
    job: job.data,
    result: result.data,
    jobId,
    modalOpen,
    setModalOpen,
    cancel,
    reset,
    openResults,
  };

  return <AnalysisContext.Provider value={value}>{children}</AnalysisContext.Provider>;
}

export function useAnalysis() {
  const context = useContext(AnalysisContext);
  if (!context) throw new Error("useAnalysis debe usarse dentro de AnalysisProvider");
  return context;
}

export type SchemaField = {
  key?: string;
  label?: string;
  type?: string;
  options?: unknown[];
  default?: unknown;
  hint?: string;
  min?: number;
  max?: number;
};

export function schemaFields(schema: Record<string, unknown> | null | undefined): SchemaField[] {
  if (!schema || !Array.isArray(schema.fields)) return [];
  return schema.fields.filter((field): field is SchemaField => Boolean(field) && typeof field === "object");
}
