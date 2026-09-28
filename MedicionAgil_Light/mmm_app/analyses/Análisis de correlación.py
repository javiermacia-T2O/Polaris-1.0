"""Corrplot con gradiente de color para DataFrames pivotados."""

import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.patches import Circle
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable

from core.loader import detect_date_column

NAME = "Corrplot (gradiente)"
DESCRIPTION = "Corrplot con círculos proporcionales a |r| y color según signo"
CATEGORY = "Gráficos"


def run(df: pd.DataFrame, method: str = "pearson", **kwargs) -> dict:
    print(f"Calculando correlaciones ({method})...")

    # Solo columnas numéricas
    num = df.select_dtypes("number").copy()

    # Quita columnas sin varianza (constantes rompen la correlación)
    num = num.loc[:, num.std(numeric_only=True) > 1e-12]

    if num.shape[1] < 2:
        return {"Error": "Se necesitan al menos 2 columnas numéricas con varianza."}

    corr = num.corr(method=method).round(3)
    n = corr.shape[0]

    print(f"Variables analizadas: {list(corr.columns)}")

    # --- Figura 1: corrplot circular ---
    fig1 = _corrplot_circles(corr)

    # --- Figura 2: heatmap ---
    fig2 = _corrplot_heatmap(corr)

    # --- Tabla de correlaciones ordenada por |r| ---
    pairs = []
    cols = list(corr.columns)
    for i in range(n):
        for j in range(i + 1, n):
            r = corr.iloc[i, j]
            if pd.isna(r):
                continue
            pairs.append({
                "Var 1": cols[i],
                "Var 2": cols[j],
                "r": float(r),
                "|r|": abs(float(r)),
                "Intensidad": _strength(abs(float(r))),
            })
    pairs_df = pd.DataFrame(pairs).sort_values("|r|", ascending=False).reset_index(drop=True)

    # Pares fuertes
    strong = pairs_df[pairs_df["|r|"] >= 0.5]
    print(f"Pares con |r| ≥ 0.5: {len(strong)}")

    return {
        "Corrplot (círculos)": {"plot": fig1},
        "Heatmap": {"plot": fig2},
        "Matriz de correlación": corr,
        "Pares ordenados por |r|": pairs_df.round(3),
        "Pares con |r| ≥ 0.5": (
            strong.round(3) if not strong.empty else "Ninguno"
        ),
        "Interpretación": (
            "Tamaño del círculo proporcional a |r|. "
            "Rojo = correlación positiva, Azul = negativa. "
            "Solo se muestran correlaciones significativas en la figura."
        ),
    }


# ------------------------------------------------------------------
# DIBUJO
# ------------------------------------------------------------------
def _corrplot_circles(corr: pd.DataFrame) -> Figure:
    """Círculos con tamaño proporcional a |r| y color según signo."""
    n = corr.shape[0]
    labels = [str(c) for c in corr.columns]

    fig = Figure(figsize=(max(6, n * 0.7), max(6, n * 0.7)), dpi=100)
    ax = fig.add_subplot(111)

    # Rango del plot: n x n
    ax.set_xlim(-0.5, n - 0.5)
    ax.set_ylim(-0.5, n - 0.5)
    ax.invert_yaxis()
    ax.set_aspect("equal")
    ax.set_xticks(range(n))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.set_yticks(range(n))
    ax.set_yticklabels(labels, fontsize=9)
    ax.grid(False)

    norm = Normalize(vmin=-1, vmax=1)
    cmap = _diverging_cmap()

    # Radio máximo = 0.45 (para que quepan en la celda)
    r_max = 0.45

    for i in range(n):
        for j in range(n):
            r = corr.iloc[i, j]
            if pd.isna(r):
                continue

            if i == j:
                # Diagonal: círculo grande gris claro
                circle = Circle((j, i), radius=r_max,
                                facecolor="#DDDDDD",
                                edgecolor="#666666", linewidth=0.6)
                ax.add_patch(circle)
                continue

            # Radio proporcional a |r|
            radius = r_max * abs(r)
            if radius < 0.01:
                continue  # nada que dibujar

            color = cmap(norm(r))
            circle = Circle((j, i), radius=radius,
                            facecolor=color,
                            edgecolor="#333333", linewidth=0.4)
            ax.add_patch(circle)

    # Barra de color
    sm = ScalarMappable(norm=norm, cmap=cmap)
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("Correlación (r)", fontsize=9)

    ax.set_title("Corrplot — tamaño ∝ |r|, color según signo",
                 fontsize=11, pad=12)
    fig.tight_layout()
    return fig


def _corrplot_heatmap(corr: pd.DataFrame) -> Figure:
    """Heatmap con anotaciones de los coeficientes."""
    n = corr.shape[0]
    labels = [str(c) for c in corr.columns]

    fig = Figure(figsize=(max(6, n * 0.7), max(6, n * 0.7)), dpi=100)
    ax = fig.add_subplot(111)

    data = corr.values
    im = ax.imshow(data, cmap=_diverging_cmap(), vmin=-1, vmax=1, aspect="auto")

    ax.set_xticks(range(n))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.set_yticks(range(n))
    ax.set_yticklabels(labels, fontsize=9)

    # Anota cada celda
    for i in range(n):
        for j in range(n):
            r = data[i, j]
            if pd.isna(r):
                continue
            color = "white" if abs(r) > 0.6 else "black"
            ax.text(j, i, f"{r:.2f}", ha="center", va="center",
                    fontsize=8, color=color)

    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("Correlación (r)", fontsize=9)

    ax.set_title("Matriz de correlación", fontsize=11, pad=12)
    fig.tight_layout()
    return fig


def _diverging_cmap():
    """Colormap divergente azul → blanco → rojo."""
    try:
        import matplotlib.pyplot as plt
        return plt.get_cmap("RdBu_r")
    except Exception:
        from matplotlib.colors import LinearSegmentedColormap
        return LinearSegmentedColormap.from_list(
            "diverging", ["#2166AC", "#F7F7F7", "#B2182B"]
        )


def _strength(abs_r: float) -> str:
    if abs_r >= 0.9:
        return "Muy fuerte"
    if abs_r >= 0.7:
        return "Fuerte"
    if abs_r >= 0.5:
        return "Moderada"
    if abs_r >= 0.3:
        return "Débil"
    return "Muy débil"