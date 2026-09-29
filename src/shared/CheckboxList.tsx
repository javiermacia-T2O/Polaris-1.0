import { useMemo, useState } from "react";

export type CheckboxOption = string | { value: string; label: string; hint?: string };

function normalize(option: CheckboxOption) {
  return typeof option === "string" ? { value: option, label: option, hint: undefined }
    : option;
}

export function CheckboxList({ options, selected, onChange, searchable = true,
  emptyLabel = "Sin opciones", maxHeight }: {
  options: CheckboxOption[];
  selected: string[];
  onChange: (values: string[]) => void;
  searchable?: boolean;
  emptyLabel?: string;
  maxHeight?: number;
}) {
  const [search, setSearch] = useState("");
  const items = useMemo(() => options.map(normalize), [options]);
  const filtered = useMemo(() => {
    const term = search.trim().toLocaleLowerCase("es");
    if (!term) return items;
    return items.filter((item) => item.label.toLocaleLowerCase("es").includes(term));
  }, [items, search]);

  const toggle = (value: string) => onChange(
    selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value]);

  if (!items.length) return <p className="checkbox-empty">{emptyLabel}</p>;

  return <div className="checkbox-list">
    {searchable && items.length > 6 && <input className="checkbox-search" value={search}
      placeholder="Buscar…" aria-label="Buscar opciones"
      onChange={(event) => setSearch(event.target.value)} />}
    <div className="checkbox-scroll" style={maxHeight ? { maxHeight } : undefined}>
      {filtered.map((item) => <label key={item.value} className="check-field">
        <input type="checkbox" checked={selected.includes(item.value)} onChange={() => toggle(item.value)} />
        <span>{item.label}{item.hint ? <small>{item.hint}</small> : null}</span>
      </label>)}
      {!filtered.length && <p className="checkbox-empty">Sin coincidencias</p>}
    </div>
    {selected.length > 0 && <div className="checkbox-foot">
      <span>{selected.length} seleccionada(s)</span>
      <button type="button" className="link-button" onClick={() => onChange([])}>Limpiar</button>
    </div>}
  </div>;
}