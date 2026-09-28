"""Immutable instructions for one table-builder result; never stores rows."""

from dataclasses import dataclass
import hashlib


@dataclass(frozen=True)
class TableRecipe:
    selected: tuple[str, ...] = ()
    filters: tuple[tuple[str, tuple], ...] = ()
    rows: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    values: tuple[tuple[str, str], ...] = ()
    # ``values`` deliberately remains the historical (column, aggregation)
    # contract.  Pivot is a property of each metric, so keeping it in a
    # parallel immutable tuple avoids breaking callers that unpack values.
    value_pivots: tuple[bool, ...] = ()
    order: tuple[tuple[str, bool], ...] = ()
    column_types: tuple[tuple[str, str], ...] = ()
    pivot: bool = True

    @classmethod
    def from_parts(cls, rows=(), cols=(), val_specs=(), filters=None,
                   pivot=True, selected=(), order=(), col_types=None):
        return cls(
            selected=tuple(selected),
            filters=tuple((str(col), tuple(sorted(values, key=repr)))
                          for col, values in sorted((filters or {}).items())),
            rows=tuple(rows), columns=tuple(cols),
            values=tuple((str(v["col"]), str(v.get("agg") or "count").lower())
                         for v in val_specs),
            value_pivots=tuple(bool(v.get("pivot", pivot))
                               for v in val_specs),
            order=tuple((str(col), bool(ascending))
                        for col, ascending in order),
            column_types=tuple(sorted((str(col), str(kind))
                                      for col, kind in (col_types or {}).items())),
            pivot=bool(pivot),
        )

    def pivots_for_values(self) -> tuple[bool, ...]:
        """Return one pivot flag per metric, including legacy recipes.

        Recipes persisted before per-metric pivot support have no
        ``value_pivots`` entries.  Their recipe-wide flag remains authoritative
        so cache keys and historical callers retain their previous meaning.
        """
        if len(self.value_pivots) == len(self.values):
            return self.value_pivots
        return (bool(self.pivot),) * len(self.values)

    def fingerprint(self, dataset, namespace: str) -> str:
        payload = (namespace, dataset.version_token(), self)
        return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()
