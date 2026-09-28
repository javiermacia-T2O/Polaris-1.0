"""Paleta, estilos, animaciones y componentes visuales."""

import tkinter as tk
from tkinter import ttk


# ------------------------------------------------------------------
# PALETA — profundidad por capas
# ------------------------------------------------------------------
COLORS = {
    "bg_deep":      "#05070A",
    "bg":           "#0A0D12",
    "bg_sidebar":   "#070A0E",
    "bg_card":      "#12171E",
    "bg_card_hi":   "#1A2029",
    "bg_card_alt":  "#161B23",
    "bg_card_top":  "#2A323D",
    "bg_input":     "#1A2029",
    "bg_hover":     "#232A33",
    "bg_overlay":   "#0F141B",

    "shadow":       "#030509",

    "border":       "#21272F",
    "border_hi":    "#333B45",
    "border_glow":  "#58A6FF",

    "text":         "#E6EDF3",
    "text_muted":   "#8B949E",
    "text_dim":     "#5D6670",

    "primary":      "#58A6FF",
    "primary_hi":   "#79B8FF",
    "primary_dark": "#1F6FEB",
    "success":      "#3FB950",
    "warning":      "#D29922",
    "danger":       "#F85149",
    "purple":       "#BC8CFF",

    "chart_bg":     "#0A0D12",
    "chart_grid":   "#21272F",
    "chart_text":   "#E6EDF3",
}

FONTS = {
    "h1":      ("Segoe UI", 16, "bold"),
    "h2":      ("Segoe UI", 12, "bold"),
    "h3":      ("Segoe UI", 10, "bold"),
    "body":    ("Segoe UI", 10),
    "small":   ("Segoe UI", 9),
    "tiny":    ("Segoe UI", 8),
    "mono":    ("Consolas", 9),
    "kpi_val": ("Segoe UI", 20, "bold"),
    "kpi_lbl": ("Segoe UI", 8),
}


def interpolate_color(c1: str, c2: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f"#{r:02X}{g:02X}{b:02X}"


# ------------------------------------------------------------------
# ESTILO TTK
# ------------------------------------------------------------------
def apply_theme(root: tk.Tk):
    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(bg=COLORS["bg"])

    style.configure("TFrame", background=COLORS["bg"])
    style.configure("Card.TFrame", background=COLORS["bg_card"])
    style.configure("Sidebar.TFrame", background=COLORS["bg_sidebar"])
    style.configure("Header.TFrame", background=COLORS["bg_card"])

    style.configure("TLabel",
                    background=COLORS["bg"],
                    foreground=COLORS["text"],
                    font=FONTS["body"])
    style.configure("H1.TLabel",
                    background=COLORS["bg_card"],
                    foreground=COLORS["text"],
                    font=FONTS["h1"])
    style.configure("H2.TLabel",
                    background=COLORS["bg"],
                    foreground=COLORS["text"],
                    font=FONTS["h2"])
    style.configure("Muted.TLabel",
                    background=COLORS["bg_card"],
                    foreground=COLORS["text_muted"],
                    font=FONTS["small"])

    # Buttons
    style.configure("TButton",
                    background=COLORS["bg_input"],
                    foreground=COLORS["text"],
                    bordercolor=COLORS["border"],
                    focuscolor=COLORS["border"],
                    font=FONTS["body"],
                    padding=(10, 6))
    style.map("TButton",
              background=[("active", COLORS["bg_hover"]),
                          ("pressed", COLORS["bg_hover"])])

    style.configure("Primary.TButton",
                    background=COLORS["primary_dark"],
                    foreground="#FFFFFF",
                    bordercolor=COLORS["primary_dark"],
                    font=FONTS["h3"],
                    padding=(10, 8))
    style.map("Primary.TButton",
              background=[("active", COLORS["primary"]),
                          ("pressed", COLORS["primary"])])

    style.configure("Nav.TButton",
                    background=COLORS["bg_input"],
                    foreground=COLORS["text"],
                    font=("Segoe UI", 12, "bold"),
                    padding=(8, 4))
    style.map("Nav.TButton",
              background=[("active", COLORS["primary_dark"])])

    # Entries / Combos
    style.configure("TEntry",
                    fieldbackground=COLORS["bg_input"],
                    foreground=COLORS["text"],
                    bordercolor=COLORS["border"],
                    insertcolor=COLORS["text"],
                    padding=6)
    style.configure("TCombobox",
                    fieldbackground=COLORS["bg_input"],
                    background=COLORS["bg_input"],
                    foreground=COLORS["text"],
                    arrowcolor=COLORS["text"],
                    bordercolor=COLORS["border"],
                    padding=4)
    style.map("TCombobox",
              fieldbackground=[("readonly", COLORS["bg_input"])],
              foreground=[("readonly", COLORS["text"])],
              selectbackground=[("readonly", COLORS["bg_input"])],
              selectforeground=[("readonly", COLORS["text"])])

    # Notebook
    style.configure("TNotebook",
                    background=COLORS["bg"],
                    borderwidth=0,
                    tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab",
                    background=COLORS["bg"],
                    foreground=COLORS["text_muted"],
                    padding=(20, 10),
                    font=FONTS["body"],
                    borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", COLORS["bg_card"])],
              foreground=[("selected", COLORS["primary"])])

    # Treeview
    style.configure("Treeview",
                    background=COLORS["bg_card"],
                    fieldbackground=COLORS["bg_card"],
                    foreground=COLORS["text"],
                    bordercolor=COLORS["border"],
                    borderwidth=0,
                    rowheight=26,
                    font=FONTS["small"])
    style.configure("Treeview.Heading",
                    background=COLORS["bg_card_top"],
                    foreground=COLORS["text_muted"],
                    font=FONTS["h3"],
                    padding=(8, 6),
                    borderwidth=0)
    style.map("Treeview.Heading",
              background=[("active", COLORS["bg_hover"])])
    style.map("Treeview",
              background=[("selected", COLORS["primary_dark"])],
              foreground=[("selected", "#FFFFFF")])

    # Scrollbars
    for orient in ("Vertical", "Horizontal"):
        style.configure(f"{orient}.TScrollbar",
                        background=COLORS["bg_card"],
                        troughcolor=COLORS["bg"],
                        bordercolor=COLORS["bg"],
                        arrowcolor=COLORS["text_muted"])

    style.configure("TSeparator", background=COLORS["border"])
    style.configure("TPanedwindow", background=COLORS["bg"])

    # Checkbuttons
    style.configure("TCheckbutton",
                    background=COLORS["bg"],
                    foreground=COLORS["text"],
                    focuscolor=COLORS["bg"],
                    font=FONTS["small"])
    style.map("TCheckbutton",
              background=[("active", COLORS["bg"])],
              foreground=[("active", COLORS["text"])])

    style.configure("Card.TCheckbutton",
                    background=COLORS["bg_card"],
                    foreground=COLORS["text"],
                    focuscolor=COLORS["bg_card"],
                    font=FONTS["small"])
    style.map("Card.TCheckbutton",
              background=[("active", COLORS["bg_card"])])

    # Radio
    style.configure("TRadiobutton",
                    background=COLORS["bg_card"],
                    foreground=COLORS["text"],
                    focuscolor=COLORS["bg_card"],
                    font=FONTS["small"])

    # Progressbar
    style.configure("Thin.Horizontal.TProgressbar",
                    background=COLORS["primary"],
                    troughcolor=COLORS["bg_card"],
                    bordercolor=COLORS["bg_card"],
                    lightcolor=COLORS["primary"],
                    darkcolor=COLORS["primary"],
                    thickness=3)

    # Progressbar gruesa para diálogos de exportación
    style.configure("Export.Horizontal.TProgressbar",
                    background=COLORS["primary"],
                    troughcolor=COLORS["bg_input"],
                    bordercolor=COLORS["border"],
                    lightcolor=COLORS["primary_hi"],
                    darkcolor=COLORS["primary_dark"],
                    thickness=24)

    return style


# ------------------------------------------------------------------
# TOAST
# ------------------------------------------------------------------
class Toast:
    _active: list = []

    def __init__(self, parent, message: str, kind: str = "info",
                 duration: int = 3200):
        self.parent = parent
        self._after_id = None

        color_map = {
            "info": COLORS["primary"],
            "success": COLORS["success"],
            "warning": COLORS["warning"],
            "error": COLORS["danger"],
        }
        accent = color_map.get(kind, COLORS["primary"])

        self.frame = tk.Frame(
            parent, bg=COLORS["bg_overlay"],
            highlightbackground=accent, highlightthickness=1,
        )
        tk.Frame(self.frame, bg=accent, width=3).pack(side="left", fill="y")

        inner = tk.Frame(self.frame, bg=COLORS["bg_overlay"])
        inner.pack(side="left", fill="both", expand=True, padx=12, pady=10)

        icon = {"info": "ℹ", "success": "✓",
                "warning": "⚠", "error": "✕"}.get(kind, "ℹ")
        tk.Label(inner, text=f"{icon}  {message}",
                 bg=COLORS["bg_overlay"], fg=COLORS["text"],
                 font=FONTS["small"], anchor="w",
                 wraplength=340, justify="left").pack(anchor="w")

        Toast._active.append(self)
        self._reposition()
        self._after_id = parent.after(duration, self._close)

    def _reposition(self):
        try:
            self.parent.update_idletasks()
        except Exception:
            return
        pw = self.parent.winfo_width()
        ph = self.parent.winfo_height()
        base_y = ph - 60
        for i, t in enumerate(reversed(Toast._active)):
            w = t.frame.winfo_reqwidth() or 300
            h = t.frame.winfo_reqheight() or 50
            x = pw - w - 24
            y = base_y - (i + 1) * (h + 8) - 20
            try:
                t.frame.place(x=x, y=y)
            except Exception:
                pass

    def _close(self):
        try:
            self.frame.destroy()
        except Exception:
            pass
        if self in Toast._active:
            Toast._active.remove(self)


# ------------------------------------------------------------------
# TOOLTIP
# ------------------------------------------------------------------
class Tooltip:
    def __init__(self, widget, text: str, delay: int = 500):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after_id = None
        self._tip = None

        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        if self._tip is not None:
            return
        try:
            x = self.widget.winfo_rootx() + 20
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
            self._tip = tk.Toplevel(self.widget)
            self._tip.wm_overrideredirect(True)
            self._tip.wm_geometry(f"+{x}+{y}")
            tk.Label(self._tip, text=self.text,
                     bg=COLORS["bg_overlay"], fg=COLORS["text"],
                     font=FONTS["tiny"], padx=10, pady=6,
                     highlightbackground=COLORS["border"],
                     highlightthickness=1).pack()
        except Exception:
            self._tip = None

    def _hide(self, _e=None):
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


# ------------------------------------------------------------------
# GLASS CARD
# ------------------------------------------------------------------
class GlassCard(tk.Frame):
    """Tarjeta con sombra, highlight superior y hover animado."""

    def __init__(self, parent, padding=(14, 12),
                 on_click=None, accent=None,
                 hover_color=None, **kwargs):
        super().__init__(parent, bg=parent["bg"], **kwargs)

        self.on_click = on_click
        self.accent = accent or COLORS["primary"]
        self.hover_color = hover_color or COLORS["bg_card_hi"]
        self._hovered = False
        self._selected = False

        # Sombra (frame oscuro detrás)
        self.shadow = tk.Frame(self, bg=COLORS["shadow"])
        self.shadow.place(x=2, y=2, relwidth=1, relheight=1)

        # Tarjeta
        self.card = tk.Frame(
            self, bg=COLORS["bg_card"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        self.card.pack(fill="both", expand=True)

        # Highlight superior
        self.top_hl = tk.Frame(self.card, bg=COLORS["bg_card_top"], height=1)
        self.top_hl.pack(fill="x", side="top")

        # Contenido
        self.inner = tk.Frame(self.card, bg=COLORS["bg_card"])
        self.inner.pack(fill="both", expand=True,
                        padx=padding[0], pady=padding[1])

        for w in (self.card, self.inner, self.top_hl):
            w.bind("<Enter>", self._on_enter, add="+")
            w.bind("<Leave>", self._on_leave, add="+")
        if on_click:
            for w in (self.card, self.inner):
                w.bind("<Button-1>", self._on_click_event, add="+")
                w.configure(cursor="hand2")

    def _set_bg(self, color: str):
        try:
            self.card.configure(bg=color)
            self.inner.configure(bg=color)
            for child in self.inner.winfo_children():
                try:
                    child.configure(bg=color)
                except Exception:
                    pass
        except Exception:
            pass

    def _on_enter(self, _e=None):
        if self._hovered:
            return
        self._hovered = True
        if not self._selected:
            self._set_bg(self.hover_color)
            self.card.configure(highlightbackground=COLORS["border_hi"])
            self.top_hl.configure(bg=COLORS["border_hi"])

    def _on_leave(self, _e=None):
        self._hovered = False
        if not self._selected:
            self._set_bg(COLORS["bg_card"])
            self.card.configure(highlightbackground=COLORS["border"])
            self.top_hl.configure(bg=COLORS["bg_card_top"])

    def _on_click_event(self, _e=None):
        if self.on_click:
            self.on_click()

    def set_selected(self, value: bool):
        self._selected = value
        if value:
            self._set_bg(self.hover_color)
            self.card.configure(highlightbackground=self.accent)
            self.top_hl.configure(bg=self.accent)
        else:
            self._on_leave()


# ------------------------------------------------------------------
# MATPLOTLIB
# ------------------------------------------------------------------
def style_matplotlib(fig):
    """
    Aplica el tema oscuro de la app a una figura matplotlib.

    Si la figura trae la marca `_mmm_keep_style = True` (puesta por
    plugins que tienen su propio estilo, como Causal Impact), se
    respeta tal cual y no se toca nada.
    """
    # ▼▼▼ NUEVO: respetar figuras con estilo propio ▼▼▼
    if getattr(fig, "_mmm_keep_style", False):
        return
    # ▲▲▲ NUEVO ▲▲▲

    fig.patch.set_facecolor(COLORS["chart_bg"])
    for ax in fig.get_axes():
        ax.set_facecolor(COLORS["chart_bg"])
        ax.tick_params(colors=COLORS["chart_text"], labelsize=9)
        ax.xaxis.label.set_color(COLORS["chart_text"])
        ax.yaxis.label.set_color(COLORS["chart_text"])
        ax.title.set_color(COLORS["chart_text"])
        for spine in ax.spines.values():
            spine.set_color(COLORS["chart_grid"])
        ax.grid(True, color=COLORS["chart_grid"], alpha=0.35, linewidth=0.6)
        legend = ax.get_legend()
        if legend is not None:
            legend.get_frame().set_facecolor(COLORS["bg_card"])
            legend.get_frame().set_edgecolor(COLORS["border"])
            for text in legend.get_texts():
                text.set_color(COLORS["chart_text"])
    fig.patch.set_facecolor(COLORS["chart_bg"])
    for ax in fig.get_axes():
        ax.set_facecolor(COLORS["chart_bg"])
        ax.tick_params(colors=COLORS["chart_text"], labelsize=9)
        ax.xaxis.label.set_color(COLORS["chart_text"])
        ax.yaxis.label.set_color(COLORS["chart_text"])
        ax.title.set_color(COLORS["chart_text"])
        for spine in ax.spines.values():
            spine.set_color(COLORS["chart_grid"])
        ax.grid(True, color=COLORS["chart_grid"], alpha=0.35, linewidth=0.6)
        legend = ax.get_legend()
        if legend is not None:
            legend.get_frame().set_facecolor(COLORS["bg_card"])
            legend.get_frame().set_edgecolor(COLORS["border"])
            for text in legend.get_texts():
                text.set_color(COLORS["chart_text"])