/**
 * Shared column-type vocabulary used by the dataset preview and the table
 * builder. Keeping it in one place means the inline type dropdown and the
 * automatic detection never drift apart.
 */
export const TYPE_OPTIONS = [
  { value: "numero", label: "Número" },
  { value: "texto", label: "Texto" },
  { value: "categorica", label: "Categoría" },
  { value: "fecha", label: "Fecha" },
  { value: "ignorar", label: "Ignorar" },
];

export const TYPE_LABELS: Record<string, string> = {
  numero: "Número",
  texto: "Texto",
  categorica: "Categoría",
  fecha: "Fecha",
  ignorar: "Ignorada",
};

/** Best-effort mapping from a DuckDB/Pandas type name to our vocabulary. */
export function guessType(type: string): string {
  if (/int|float|double|decimal|numeric|number/i.test(type)) return "numero";
  if (/date|time|timestamp/i.test(type)) return "fecha";
  if (/bool|category|object|string/i.test(type)) return "categorica";
  return "texto";
}