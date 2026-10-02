"""Estado de los datos asociado a una sesión de MMMApp."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SessionState:
    """Referencias a los DataFrames y selección activa de una sesión.

    Los DataFrames se almacenan por referencia para no duplicar datos durante
    los cambios de pantalla o de conjunto activo.
    """

    df_raw: Any = None
    df_view: Any = None
    file_path: Path | None = None
    df_original: Any = None
    df_table: Any = None
    loaded_datasets: dict[str, Any] = field(default_factory=dict)
    active_dataset_name: str | None = None
    column_types: dict[str, str] = field(default_factory=dict)
    value_filters: dict[str, set] = field(default_factory=dict)
    view_is_built: bool = False
    active_dataset: Any = None
    active_stats: dict[str, Any] | None = None
    base_view: Any = None
