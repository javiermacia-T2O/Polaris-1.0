import { invoke } from "@tauri-apps/api/core";
import { analysisManifestSchema, datasetMetadataSchema, tablePageSchema } from "./types";
import type { AnalysisManifest, DatasetMetadata, TablePage } from "./types";

type Envelope<T> = { id: string; ok: true; result: T } |
  { id: string; ok: false; error: { code: string; message: string; details: Record<string, unknown> } };

async function request<T>(operation: string, params: Record<string, unknown> = {}): Promise<T> {
  const envelope = await invoke<Envelope<T>>("sidecar_request", { operation, params });
  if (!envelope.ok) throw new Error(`${envelope.error.code}: ${envelope.error.message}`);
  return envelope.result;
}

export const api = {
  health: () => request<{ status: string; protocol: number }>("health"),
  diagnostics: () => request<{
    app_version: string; sidecar_protocol: number; python: string;
    platform: string; packages: Record<string, string | null>;
    dependency_imports: Record<string, string>;
    memory: { total_bytes: number; available_bytes: number };
    dataset_backends: string[]; jobs: unknown[]; log_path: string;
  }>("get_diagnostics"),
  selectDataset: () => invoke<string | null>("select_dataset"),
  async loadDataset(path: string): Promise<DatasetMetadata> {
    return datasetMetadataSchema.parse(await request("load_dataset", { path }));
  },
  async listDatasets(): Promise<DatasetMetadata[]> {
    return datasetMetadataSchema.array().parse(await request("list_datasets"));
  },
  async preview(datasetId: string, offset: number, limit = 100): Promise<TablePage> {
    return tablePageSchema.parse(await request("get_table_preview", {
      dataset_id: datasetId, offset, limit,
    }));
  },
  async listAnalyses(): Promise<AnalysisManifest[]> {
    return analysisManifestSchema.array().parse(await request("list_analyses"));
  },
};
