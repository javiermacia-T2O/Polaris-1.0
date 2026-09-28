"""Preflight review dialog."""

import tkinter as tk
from tkinter import ttk

from theme import COLORS, FONTS
from ui.window_position import center_popup


class PreflightDialog(tk.Toplevel):
    """
    Muestra un resumen de la configuración antes de ejecutar.
    Avisa sobre problemas (VIF, NaN, anomalías excesivas) sin bloquear.
    Devuelve True en self.proceed si el usuario acepta.
    """

    def __init__(self, parent, summary: dict, warnings: list, errors: list):
        super().__init__(parent)
        self.title("Revisión previa")
        self.configure(bg=COLORS["bg"])
        self.transient(parent)
        self.grab_set()

        self.proceed = False

        w, h = 560, 460
        center_popup(self, parent, w, h)
        self.minsize(520, 380)

        # Header
        header = tk.Frame(self, bg=COLORS["bg_card"])
        header.pack(fill="x")
        tk.Frame(header, bg=COLORS["primary"], height=2).pack(fill="x")
        tk.Label(header, text="Revisión previa al análisis",
                 bg=COLORS["bg_card"], fg=COLORS["text"],
                 font=FONTS["h2"]).pack(anchor="w", padx=20, pady=12)

        # Cuerpo scrollable
        body = tk.Frame(self, bg=COLORS["bg"])
        body.pack(fill="both", expand=True, padx=20, pady=(4, 0))

        # --- Resumen ---
        tk.Label(body, text="RESUMEN", bg=COLORS["bg"],
                 fg=COLORS["primary"], font=FONTS["kpi_lbl"]).pack(
            anchor="w", pady=(6, 4))

        resumen = tk.Frame(body, bg=COLORS["bg_card"],
                           highlightbackground=COLORS["border"],
                           highlightthickness=1)
        resumen.pack(fill="x")

        for k, v in summary.items():
            row = tk.Frame(resumen, bg=COLORS["bg_card"])
            row.pack(fill="x", padx=12, pady=2)
            tk.Label(row, text=k, bg=COLORS["bg_card"],
                     fg=COLORS["text_muted"], font=FONTS["small"],
                     width=26, anchor="w").pack(side="left")
            tk.Label(row, text=str(v), bg=COLORS["bg_card"],
                     fg=COLORS["text"], font=FONTS["body"],
                     anchor="w").pack(side="left")

        # --- Errores ---
        if errors:
            tk.Label(body, text="ERRORES BLOQUEANTES",
                     bg=COLORS["bg"], fg=COLORS["danger"],
                     font=FONTS["kpi_lbl"]).pack(anchor="w", pady=(12, 4))
            for e in errors:
                card = tk.Frame(body, bg=COLORS["bg_card"],
                                highlightbackground=COLORS["danger"],
                                highlightthickness=1)
                card.pack(fill="x", pady=2)
                tk.Label(card, text=f"✕  {e}", bg=COLORS["bg_card"],
                         fg=COLORS["danger"], font=FONTS["small"],
                         anchor="w", justify="left",
                         wraplength=480).pack(fill="x", padx=12, pady=6)

        # --- Avisos ---
        if warnings:
            tk.Label(body, text="AVISOS", bg=COLORS["bg"],
                     fg=COLORS["warning"],
                     font=FONTS["kpi_lbl"]).pack(anchor="w", pady=(12, 4))
            for wmsg in warnings:
                card = tk.Frame(body, bg=COLORS["bg_card"],
                                highlightbackground=COLORS["warning"],
                                highlightthickness=1)
                card.pack(fill="x", pady=2)
                tk.Label(card, text=f"⚠  {wmsg}", bg=COLORS["bg_card"],
                         fg=COLORS["warning"], font=FONTS["small"],
                         anchor="w", justify="left",
                         wraplength=480).pack(fill="x", padx=12, pady=6)

        if not errors and not warnings:
            tk.Label(body, text="✓  Sin avisos. Todo listo para ejecutar.",
                     bg=COLORS["bg"], fg=COLORS["success"],
                     font=FONTS["body"]).pack(anchor="w", pady=12)

        # --- Footer ---
        footer = tk.Frame(self, bg=COLORS["bg_card"])
        footer.pack(fill="x", side="bottom")
        tk.Frame(footer, bg=COLORS["border"], height=1).pack(fill="x")

        fr = tk.Frame(footer, bg=COLORS["bg_card"])
        fr.pack(fill="x", padx=20, pady=12)

        ttk.Button(fr, text="Revisar",
                   command=self._cancel).pack(side="right", padx=(6, 0))

        if not errors:
            ttk.Button(fr, text="✓  Ejecutar igual",
                       style="Primary.TButton",
                       command=self._accept).pack(side="right")

    def _accept(self):
        self.proceed = True
        self.destroy()

    def _cancel(self):
        self.proceed = False
        self.destroy()
