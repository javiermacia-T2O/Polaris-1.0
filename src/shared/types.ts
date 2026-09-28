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
});
export type DatasetMetadata = z.infer<typeof datasetMetadataSchema>;

export const tablePageSchema = z.object({
  dataset_id: z.string(),
  offset: z.number(),
  limit: z.number(),
  total_rows: z.number(),
  columns: z.array(z.string()),
  rows: z.array(z.record(z.string(), z.unknown())),
});
export type TablePage = z.infer<typeof tablePageSchema>;

export const analysisManifestSchema = z.object({
  id: z.string(), version: z.string(), name: z.string(),
  description: z.string(), category: z.string(),
  parameter_schema: z.record(z.string(), z.unknown()).nullable(),
  supports_cancel: z.boolean(), supports_progress: z.boolean(),
  needs_full_dataframe: z.boolean(), supports_lazy_dataset: z.boolean(),
  recommended_for: z.string().nullable(), caution: z.string().nullable(),
});
export type AnalysisManifest = z.infer<typeof analysisManifestSchema>;

export type SidecarError = { code: string; message: string; details: Record<string, unknown> };
