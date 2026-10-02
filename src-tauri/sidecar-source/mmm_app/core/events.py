"""Gestión de eventos (anomalías) por dataset."""

import json
import uuid
from pathlib import Path

import pandas as pd
from core.atomic import write_json_atomic

EVENTS_DIR = Path(__file__).resolve().parent.parent / "output" / "events"


def events_path(dataset_name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_"
                   for c in str(dataset_name))
    return EVENTS_DIR / f"{safe}.json"


def _empty() -> dict:
    return {"groups": {}, "sub_events": []}


# ------------------------------------------------------------------
# Persistencia
# ------------------------------------------------------------------
def load_events(dataset_name: str) -> dict:
    path = events_path(dataset_name)
    if not path.exists():
        return _empty()
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return _empty()

    # Migración desde formato antiguo anidado
    if "sub_events" not in data:
        data["sub_events"] = []
        old_groups = data.get("groups", {})
        for g_name, g in old_groups.items():
            for se in g.get("sub_events", []):
                se["group"] = g_name
                data["sub_events"].append(se)
        data["groups"] = {
            g: {"color": v.get("color", "#58A6FF")}
            for g, v in old_groups.items()
        }

    if "groups" not in data:
        data["groups"] = {}
    return data


def save_events(dataset_name: str, events: dict):
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = events_path(dataset_name)
    if path.exists():
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"El archivo de eventos existente está dañado: {path}") from exc
    write_json_atomic(path, events)


# ------------------------------------------------------------------
# Grupos
# ------------------------------------------------------------------
def create_group(events: dict, name: str, color: str = "#58A6FF") -> str:
    if name in events["groups"]:
        return name
    events["groups"][name] = {"color": color}
    return name


def rename_group(events: dict, old: str, new: str) -> bool:
    if old not in events["groups"] or new in events["groups"]:
        return False
    events["groups"][new] = events["groups"].pop(old)
    for se in events["sub_events"]:
        if se.get("group") == old:
            se["group"] = new
    return True


def delete_group(events: dict, name: str):
    events["groups"].pop(name, None)
    for se in events["sub_events"]:
        if se.get("group") == name:
            se["group"] = ""


# ------------------------------------------------------------------
# Sub-eventos (compatibles con varios nombres de parámetro)
# ------------------------------------------------------------------
def add_sub_event(events: dict,
                  group_name: str = None,
                  name: str = "Evento",
                  start=None,
                  end=None,
                  intensity: float = 1.0,
                  own: bool = False,
                  group: str = None) -> str:
    """
    Añade un sub-evento. Acepta 'group' o 'group_name' como kwarg.
    """
    if group is not None and group_name is None:
        group_name = group
    group_name = (group_name or "").strip()

    if group_name and group_name not in events["groups"]:
        create_group(events, group_name)

    if "sub_events" not in events:
        events["sub_events"] = []

    sub_id = str(uuid.uuid4())[:8]
    events["sub_events"].append({
        "id": sub_id,
        "name": name,
        "start": str(pd.Timestamp(start).date()) if start is not None else "",
        "end":   str(pd.Timestamp(end).date())   if end   is not None else "",
        "intensity": float(intensity),
        "group": group_name,
        "own": bool(own),
    })
    return sub_id


def remove_sub_event(events: dict, sub_id: str) -> bool:
    for i, se in enumerate(events.get("sub_events", [])):
        if se["id"] == sub_id:
            events["sub_events"].pop(i)
            return True
    return False


def toggle_own(events: dict, sub_id: str) -> bool:
    for se in events.get("sub_events", []):
        if se["id"] == sub_id:
            se["own"] = not se.get("own", False)
            return se["own"]
    return False


def update_sub_event(events: dict, sub_id: str, **fields) -> bool:
    for se in events.get("sub_events", []):
        if se["id"] == sub_id:
            for k, v in fields.items():
                if k in se:
                    se[k] = v
            return True
    return False


# ------------------------------------------------------------------
# Generación de columnas para el modelo
# ------------------------------------------------------------------
def generate_anomaly_columns(events: dict, df: pd.DataFrame,
                              date_col: str) -> dict:
    """
    Devuelve {nombre_columna: pd.Series} alineadas con df.

    Reglas:
      - Sin grupo              → columna "Evento · {nombre}"
      - Con grupo y own=False  → suma a "Evento · {grupo}"
      - Con grupo y own=True   → columna propia "Evento · {nombre}"
    """
    if date_col not in df.columns:
        return {}

    idx = pd.to_datetime(df[date_col], errors="coerce")
    buckets = {}   # key → {"label":..., "series":[], "color":...}

    for se in events.get("sub_events", []):
        name = se.get("name") or "Evento"
        group = (se.get("group") or "").strip()
        own = se.get("own", False)

        if own or not group:
            key = f"single:{se['id']}"
            label = f"Evento · {name}"
            color = events["groups"].get(group, {}).get("color", "#F85149")
        else:
            key = f"group:{group}"
            label = f"Evento · {group}"
            color = events["groups"].get(group, {}).get("color", "#F85149")

        if key not in buckets:
            buckets[key] = {"label": label, "series": [], "color": color}

        s = _series_from_event(idx, se)
        if s is not None:
            buckets[key]["series"].append(s)

    cols = {}
    for _, info in buckets.items():
        if not info["series"]:
            continue
        total = pd.Series(0.0, index=range(len(df)))
        for s in info["series"]:
            total = total + s.reset_index(drop=True)
        cols[info["label"]] = total
        try:
            cols[info["label"]]._mmm_color = info["color"]
        except Exception:
            pass

    return cols


def _series_from_event(idx: pd.Series, sub: dict):
    try:
        start = pd.Timestamp(sub["start"])
        end = pd.Timestamp(sub["end"])
    except Exception:
        return None
    mask = (idx >= start) & (idx <= end)
    if not mask.any():
        return None
    intensity = float(sub.get("intensity", 1.0))
    return pd.Series(mask.astype(float).values * intensity)


# ------------------------------------------------------------------
# Persistencia de regresión
# ------------------------------------------------------------------
def _regression_dir() -> Path:
    d = EVENTS_DIR / "regression"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _dataset_slug(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_"
                   for c in str(name))


def load_last_regression_config(dataset_name: str) -> dict:
    p = _regression_dir() / f"{_dataset_slug(dataset_name)}__last.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_last_regression_config(dataset_name: str, config: dict):
    try:
        write_json_atomic(
            _regression_dir() / f"{_dataset_slug(dataset_name)}__last.json",
            config)
    except Exception:
        pass


def load_regression_history(dataset_name: str) -> list:
    p = _regression_dir() / f"{_dataset_slug(dataset_name)}__history.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def append_regression_history(dataset_name: str, entry: dict,
                               max_items: int = 50):
    history = load_regression_history(dataset_name)
    history.insert(0, entry)
    try:
        write_json_atomic(
            _regression_dir() / f"{_dataset_slug(dataset_name)}__history.json",
            history[:max_items])
    except Exception:
        pass


def clear_regression_history(dataset_name: str):
    p = _regression_dir() / f"{_dataset_slug(dataset_name)}__history.json"
    try:
        if p.exists():
            p.unlink()
    except Exception:
        pass
