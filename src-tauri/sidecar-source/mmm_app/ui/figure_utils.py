"""Figure extraction and conversion helpers."""

from matplotlib.figure import Figure


def _is_matplotlib(obj) -> bool:
    return ("matplotlib" in type(obj).__module__
            and type(obj).__name__ == "Figure")


def _is_plotly(obj) -> bool:
    mod = type(obj).__module__
    return mod.startswith("plotly") and type(obj).__name__ == "Figure"


def extract_figures(result) -> list:
    figs = []

    def walk(value):
        if value is None:
            return
        if _is_matplotlib(value):
            figs.append(value)
            return
        if _is_plotly(value):
            figs.append(_plotly_to_matplotlib(value))
            return
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v)

    walk(result)
    return figs



def _plotly_to_matplotlib(plotly_fig) -> Figure:
    fig = Figure(figsize=(7, 4.5), dpi=100)
    ax = fig.add_subplot(111)
    for trace in plotly_fig.data:
        ttype = getattr(trace, "type", None)
        name = getattr(trace, "name", None) or ""
        if ttype == "scatter":
            x, y = getattr(trace, "x", None), getattr(trace, "y", None)
            if x is not None and y is not None:
                ax.plot(x, y, label=name)
        elif ttype == "bar":
            x, y = getattr(trace, "x", None), getattr(trace, "y", None)
            if x is not None and y is not None:
                ax.bar(range(len(x)), y, label=name)
                ax.set_xticks(range(len(x)))
                ax.set_xticklabels([str(v) for v in x],
                                   rotation=30, ha="right")
        elif ttype == "pie":
            labels = list(getattr(trace, "labels", []) or [])
            values = list(getattr(trace, "values", []) or [])
            if labels and values:
                ax.pie(values, labels=labels, autopct="%1.1f%%")
                ax.axis("equal")
        elif ttype == "heatmap":
            z = getattr(trace, "z", None)
            if z is not None:
                im = ax.imshow(z, aspect="auto", cmap="RdBu_r",
                               vmin=-1, vmax=1)
                fig.colorbar(im, ax=ax)
    try:
        layout = plotly_fig.layout
        if layout.title and layout.title.text:
            ax.set_title(layout.title.text, fontsize=11)
    except Exception:
        pass
    if ax.get_legend_handles_labels()[0]:
        ax.legend(fontsize=8, loc="best")
    fig.tight_layout()
    return fig
