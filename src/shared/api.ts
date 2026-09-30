import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { analysisManifestSchema, chartArtifactSchema, columnInfoSchema, columnProfileSchema,
  datasetMetadataSchema, jobStatusSchema, resultSummarySchema,
  resultTablePageSchema, tablePageSchema } from "./types";
import type { AnalysisManifest, ColumnInfo, ColumnProfile, DatasetMetadata,
  ChartArtifact, JobStatus, ResultSummary, ResultTablePage, TablePage, TableRecipe } from "./types";

type Envelope<T> = { id: string; type?: string; ok: true; result: T } |
  { id: string; type?: string; ok: false; error: { code: string; message: string; details: Record<string, unknown> } };

export type RequestPriority = "control" | "interactive" | "background";

export type ProgressEvent = { id: string; percent: number; message: string };

export type RequestOptions = {
  /** Priority hint for the sidecar scheduler. Defaults to "interactive". */
  priority?: RequestPriority;
  /** Per-request timeout in milliseconds. */
  timeoutMs?: number;
  /** Stable id so the caller can cancel this request. */
  requestId?: string;
  /** Called for every progress message the sidecar emits for this request. */
  onProgress?: (event: ProgressEvent) => void;
};

/** Error thrown for a failed sidecar request, preserving the error code. */
export class SidecarError extends Error {
  readonly code: string;
  readonly details: Record<string, unknown>;
  constructor(code: string, message: string, details: Record<string, unknown> = {}) {
    super(`${code}: ${message}`);
    this.name = "SidecarError";
    this.code = code;
    this.details = details;
  }
}

let counter = 0;
function nextRequestId(): string {
  counter += 1;
  return `req-${Date.now().toString(36)}-${counter}`;
}

async function request<T>(operation: string, params: Record<string, unknown> = {},
  options: RequestOptions = {}): Promise<T> {
  const requestId = options.requestId ?? nextRequestId();
  let unlisten: (() => void) | undefined;
  if (options.onProgress) {
    unlisten = await listen<ProgressEvent>("sidecar://progress", (event) => {
      if (event.payload.id === requestId) options.onProgress?.(event.payload);
    });
  }
  try {
    const envelope = await invoke<Envelope<T>>("sidecar_request", {
      operation, params, requestId,
      priority: options.priority ?? "interactive",
      timeoutMs: options.timeoutMs,
    });
    if (!envelope.ok) {
      throw new SidecarError(envelope.error.code, envelope.error.message,
        envelope.error.details);
    }
    return envelope.result;
  } finally {
    unlisten?.();
  }
}

/** Ask the sidecar to cancel an in-flight request by its id. */
export function cancelRequest(requestId: string): Promise<boolean> {
  return invoke<boolean>("cancel_request", { targetId: requestId });
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
  selectExportPath: (fileName: string, format: "csv" | "parquet") =>
    invoke<string | null>("select_export_path", { fileName, format }),
  saveChart: (fileName: string, dataBase64: string) =>
    invoke<string | null>("save_chart", { fileName, dataBase64 }),
  async loadDataset(path: string): Promise<DatasetMetadata> {
    return datasetMetadataSchema.parse(await request("load_dataset", { path }));
  },
  async listDatasets(): Promise<DatasetMetadata[]> {
    return datasetMetadataSchema.array().parse(await request("list_datasets"));
  },
  async closeDataset(datasetId: string): Promise<void> {
    await request("close_dataset", { dataset_id: datasetId });
  },
  async columns(datasetId: string): Promise<ColumnInfo[]> {
    return columnInfoSchema.array().parse(await request("get_columns", { dataset_id: datasetId }));
  },
  async columnProfile(datasetId: string, column: string): Promise<ColumnProfile> {
    return columnProfileSchema.parse(await request("get_column_profile", {
      dataset_id: datasetId, column,
    }));
  },
  columnValues: (datasetId: string, column: string, limit = 500) =>
    request<{ column: string; values: string[]; truncated: boolean }>(
      "get_column_values", { dataset_id: datasetId, column, limit }),
  async preview(datasetId: string, offset: number, limit = 100,
    options: RequestOptions = {}): Promise<TablePage> {
    return tablePageSchema.parse(await request("get_table_preview", {
      dataset_id: datasetId, offset, limit,
    }, options));
  },
  async listAnalyses(): Promise<AnalysisManifest[]> {
    return analysisManifestSchema.array().parse(await request("list_analyses"));
  },
  async analysisManifest(analysisId: string, datasetId: string): Promise<AnalysisManifest> {
    return analysisManifestSchema.parse(await request("get_analysis_manifest", {
      analysis_id: analysisId, dataset_id: datasetId,
    }));
  },
  async applyFilters(datasetId: string, filters: Array<{ column: string; values: unknown[] }>) {
    return datasetMetadataSchema.parse(await request("apply_filters", {
      dataset_id: datasetId, filters,
    }));
  },
  async resetFilters(datasetId: string) {
    return datasetMetadataSchema.parse(await request("reset_filters", { dataset_id: datasetId }));
  },
  dateColumns: (datasetId: string) =>
    request<Array<{ column: string; granularity: string }>>("get_date_columns", {
      dataset_id: datasetId,
    }),
  dateRange: (datasetId: string, column: string) =>
    request<{ column: string; start: string | null; end: string | null; granularity: string }>(
      "get_date_range", { dataset_id: datasetId, column }),
  async applyDateRange(datasetId: string, column: string, start: string | null, end: string | null) {
    return datasetMetadataSchema.parse(await request("apply_date_range", {
      dataset_id: datasetId, column, start, end,
    }));
  },
  async resetDateRange(datasetId: string) {
    return datasetMetadataSchema.parse(await request("reset_date_range", { dataset_id: datasetId }));
  },
  async tablePage(datasetId: string, offset: number, limit = 100,
    sort: Array<{ column: string; direction: "asc" | "desc" }> = [],
    options: RequestOptions = {}): Promise<TablePage> {
    return tablePageSchema.parse(await request("get_table_page", {
      dataset_id: datasetId, offset, limit, sort,
    }, options));
  },
  async buildTable(datasetId: string, recipe: TableRecipe,
    options: RequestOptions = {}): Promise<string> {
    return request("build_table", { dataset_id: datasetId, recipe }, options);
  },
  async previewTable(datasetId: string, recipe: TableRecipe, limit = 20,
    options: RequestOptions = {}): Promise<TablePage> {
    return tablePageSchema.parse(await request("preview_table", {
      dataset_id: datasetId, recipe, limit,
    }, options));
  },
  runAnalysis: (analysisId: string, datasetId: string, parameters: Record<string, unknown>,
    options: RequestOptions = {}) =>
    request<string>("run_analysis", { analysis_id: analysisId,
      dataset_id: datasetId, parameters }, options),
  async jobStatus(jobId: string): Promise<JobStatus> {
    return jobStatusSchema.parse(await request("get_job_status", { job_id: jobId }));
  },
  async cancelJob(jobId: string): Promise<JobStatus> {
    return jobStatusSchema.parse(await request("cancel_job", { job_id: jobId }));
  },
  async analysisResult(resultId: string): Promise<ResultSummary> {
    return resultSummarySchema.parse(await request("get_analysis_result", { result_id: resultId }));
  },
  async resultTable(resultId: string, table: string, offset = 0, limit = 100): Promise<ResultTablePage> {
    return resultTablePageSchema.parse(await request("get_result_table", {
      result_id: resultId, table, offset, limit,
    }));
  },
  async resultChart(resultId: string, chart: string): Promise<ChartArtifact> {
    return chartArtifactSchema.parse(await request("get_result_chart", {
      result_id: resultId, chart,
    }));
  },
  exportResult: (resultId: string, table: string, path: string, format: "csv" | "parquet",
    options: RequestOptions = {}) =>
    request<string>("export_result", { result_id: resultId, table, path, fmt: format }, options),
  exportDataset: (datasetId: string, path: string, format: "csv" | "parquet",
    options: RequestOptions = {}) =>
    request<string>("export_dataset", { dataset_id: datasetId, path, fmt: format }, options),
  async mergeDatasets(datasetIds: string[], options: {
    name?: string; operation?: "concat" | "merge"; mode?: "all" | "common";
    keys?: string[]; how?: "inner" | "outer";
  } = {}, requestOptions: RequestOptions = {}): Promise<DatasetMetadata> {
    return datasetMetadataSchema.parse(await request("merge_datasets", {
      dataset_ids: datasetIds, name: options.name ?? "",
      operation: options.operation ?? "concat", mode: options.mode ?? "all",
      keys: options.keys ?? [], how: options.how ?? "inner",
    }, requestOptions));
  },
  cacheInfo: () => request<{ size_mb: number }>("get_cache_info"),
  clearCache: () => request<{ removed: number }>("clear_cache"),
  freeMemory: (hard = false) =>
    request<{ collected: number; hard: boolean; available_bytes: number }>(
      "free_memory", { hard }),
};
