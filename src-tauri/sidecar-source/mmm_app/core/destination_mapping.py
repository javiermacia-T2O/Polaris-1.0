"""
Mapeo de Hotel_short_name → destination_area_mapped.

Replica las reglas de negocio de Iberostar del script R original.
"""

import pandas as pd


# ==================================================================
# REGLAS DE MAPEO (Hotel_short_name → destino)
# ==================================================================
_DESTINATION_MAP = {
    # Andalucía
    "andalusia": [
        "XAN", "ANP", "ICH", "MAB", "MAP",
    ],
    # Aruba
    "aruba": ["ARB"],
    # Islas Baleares
    "balearic islands": [
        "BLA", "ALC", "MUR", "PMV", "ABA", "ABH", "PIN", "BAR",
        "CRI", "JAR", "CDM", "CAM", "BHP", "EST", "LLT", "PDP", "SEU",
    ],
    # Brasil
    "brazil": ["AMA", "BAH", "PRA"],
    # Islas Canarias
    "canary islands": [
        "LAN", "XFU", "GAV", "ANT", "SAB", "BOU", "DAL", "FUA",
        "MIR", "OTT", "SLM",
    ],
    # Cabo Verde
    "cape verde": ["BOA"],
    # Cuba
    "cuba": [
        "BEL", "DAI", "TAI", "VAR", "GHT", "PAV", "LAG", "PCE",
        "ENS", "HOL", "HCL", "ECL", "ESM", "PCK", "PCT", "BEV",
    ],
    # República Dominicana
    "dominican republic": ["BAV", "DOR", "DOM", "PCA", "HAC", "GHB", "BSP"],
    # Grecia
    "greece": ["CME", "CMA", "PAN"],
    # Jamaica
    "jamaica": ["ROH", "ROA", "GHR"],
    # México
    "mexico": [
        "COZ", "QTZ", "TUC", "PMA", "PBE", "PMY", "PLI",
        "GHP", "MIT", "RIC", "CUN", "TCU",
    ],
    # Montenegro
    "montenegro": ["BVE", "HEN", "SLA", "MON"],
    # Marruecos
    "morocco": ["FOB", "SAI", "PMK"],
    # Perú
    "peru": ["LMA"],
    # Portugal
    "portugal": ["LIS", "ALG"],
    # Túnez
    "tunisia": ["AVE", "MEH", "REM", "DEA", "KAN", "KUR", "EOL", "MRG"],
    # Estados Unidos
    "united states": ["NYP", "BRK", "MIA"],
    # City hotels
    "city hotels": ["MCY", "BCN", "LET"],
}

# Diccionario invertido: código → destino (para búsquedas rápidas)
_CODE_TO_DEST = {}
for dest, codes in _DESTINATION_MAP.items():
    for code in codes:
        _CODE_TO_DEST[code.upper()] = dest


# ==================================================================
# FUNCIONES PÚBLICAS
# ==================================================================
def clean_hotel_name(s: pd.Series) -> pd.Series:
    """Normaliza: uppercase + trim."""
    return s.astype(str).str.strip().str.upper()


def map_hotel_to_destination(s: pd.Series,
                               fallback: str = "otros") -> pd.Series:
    """
    Convierte una serie de Hotel_short_name en su destino.

    Uso:
        df["destination_area_mapped"] = map_hotel_to_destination(
            df["Hotel_short_name"])
    """
    cleaned = clean_hotel_name(s)
    result = cleaned.map(_CODE_TO_DEST).fillna(fallback)
    return result


def add_destination_column(df: pd.DataFrame,
                             hotel_col: str = "Hotel_short_name",
                             dest_col: str = "destination_area_mapped",
                             fallback: str = "otros",
                             inplace: bool = False) -> pd.DataFrame:
    """
    Añade la columna 'destination_area_mapped' al DataFrame
    a partir de 'Hotel_short_name'.

    Args:
        df: DataFrame de entrada
        hotel_col: nombre de la columna con los códigos de hotel
        dest_col: nombre de la columna de destino a crear
        fallback: valor por defecto cuando el hotel no está mapeado
        inplace: si True, modifica df directamente. Si False, devuelve copia.

    Returns:
        DataFrame con la nueva columna
    """
    if hotel_col not in df.columns:
        raise ValueError(
            f"Columna '{hotel_col}' no existe en el DataFrame.")

    out = df if inplace else df.copy()
    out[dest_col] = map_hotel_to_destination(out[hotel_col],
                                                fallback=fallback)
    return out


def list_destinations() -> list[str]:
    """Lista de destinos disponibles."""
    return sorted(_DESTINATION_MAP.keys()) + ["otros"]


def list_hotels_by_destination(dest: str) -> list[str]:
    """Hoteles de un destino concreto."""
    return list(_DESTINATION_MAP.get(dest, []))


def preview_mapping(hotel_series: pd.Series) -> pd.DataFrame:
    """
    Muestra un resumen del mapeo: cuántos hoteles hay por destino,
    cuántos se mapean, cuántos caen a 'otros'.
    """
    cleaned = clean_hotel_name(hotel_series)
    mapped = cleaned.map(_CODE_TO_DEST)

    df = pd.DataFrame({
        "Hotel": cleaned,
        "Destino": mapped.fillna("otros"),
    })
    counts = (df.groupby("Destino").size()
                .reset_index(name="Count")
                .sort_values("Count", ascending=False))
    return counts