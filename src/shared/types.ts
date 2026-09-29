import { z } from "zod";

export const datasetMetadataSchema = z.object({
  dataset_id: z.string(),
  name: z.string(),
  backend: z.string(),
  rows: z.number().int().nonnegative(),
  columns: z.array(z.string()),
  types: z.array(z.string()),
  uses_disk: z.boolean(),
  source_path: z.string().nullable(),
  rows_approximate: z.boolean().optional().default(false),
});
export type DatasetMetadata = z.infer<typeof datasetMetadataSchema>;

export const columnInfoSchema = z.object({ name: z.string(), type: z.string() });
export type ColumnInfo = z.infer<typeof columnInfoSchema>;

export const columnProfileSchema = z.object({
  column: z.string(), sample_size: z.number(), nulls: z.number(),
  unique_sample: z.number(), numeric: z.boolean(), examples: z.array(z.unknown()),
});
export type ColumnProfile = z.infer<typeof columnProfileSchema>;

export const tablePageSchema = z.object({
  dataset_id: z.string(),
  offset: z.number(),
  limit: z.number(),
  total_rows: z.number(),
  columns: z.array(z.string()),
  rows: z.array(z.record(z.string(), z.unknown())),
  approximate: z.boolean().optional().default(false),
  total_rows_approximate: z.boolean().optional().default(false),
});
export type TablePage = z.infer<typeof tablePageSchema>;

export const tableFormatSchema = z.object({
  summary: z.string(),
  temporal: z.array(z.string()).default([]),
  required_dimensions: z.array(z.string()).default([]),
  optional_dimensions: z.array(z.string()).default([]),
  metrics: z.array(z.string()).default([]),
  investment: z.array(z.string()).default([]),
  rows: z.array(z.string()).default([]),
  columns: z.array(z.string()).default([]),
  values: z.array(z.string()).default([]),
  requires_pivot: z.boolean().default(false),
  notes: z.string().nullable().default(null),
});
export type TableFormat = z.infer<typeof tableFormatSchema>;

export const analysisManifestSchema = z.object({
  id: z.string(), version: z.string(), name: z.string(),
  description: z.string(), category: z.string(),
  parameter_schema: z.record(z.string(), z.unknown()).nullable(),
  supports_cancel: z.boolean(), supports_progress: z.boolean(),
  needs_full_dataframe: z.boolean(), supports_lazy_dataset: z.boolean(),
  recommended_for: z.string().nullable(), caution: z.string().nullable(),
  table_format: tableFormatSchema.nullable().default(null),
});
export type AnalysisManifest = z.infer<typeof analysisManifestSchema>;

export const jobStatusSchema = z.object({
  job_id: z.string(), type: z.string(),
  state: z.enum(["QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"]),
  created_at: z.string(), started_at: z.string().nullable(),
  finished_at: z.string().nullable(), progress: z.number(), message: z.string(),
  result_id: z.string().nullable(),
  error: z.object({ code: z.string(), message: z.string(),
    details: z.record(z.string(), z.unknown()) }).nullable(),
  diagnostics: z.array(z.string()),
});
export type JobStatus = z.infer<typeof jobStatusSchema>;

export const resultSummarySchema = z.object({
  result_id: z.string(), analysis_id: z.string(), tables: z.array(z.string()),
  charts: z.array(z.string()), scalars: z.record(z.string(), z.unknown()),
});
export type ResultSummary = z.infer<typeof resultSummarySchema>;

export const resultTablePageSchema = z.object({
  result_id: z.string(), table: z.string(), offset: z.number(), limit: z.number(),
  total_rows: z.number(), columns: z.array(z.string()),
  rows: z.array(z.record(z.string(), z.unknown())),
});
export type ResultTablePage = z.infer<typeof resultTablePageSchema>;

export const chartArtifactSchema = z.object({
  result_id: z.string(), chart: z.string(), mime_type: z.string(), data_base64: z.string(),
});
export type ChartArtifact = z.infer<typeof chartArtifactSchema>;

export type TableRecipe = {
  rows?: string[];
  columns?: string[];
  values?: Array<{ col: string; agg: string; pivot?: boolean }>;
  filters?: Record<string, unknown[]>;
  pivot?: boolean;
  selected?: string[];
  order?: Array<{ column: string; ascending: boolean }>;
  column_types?: Record<string, string>;
};

export type SidecarError = { code: string; message: string; details: Record<string, unknown> };
