import configparser
import html
import json
import os
import queue
import re
import sqlite3
import subprocess
import sys
import threading
import traceback
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from compile_mq5 import find_metaeditor_path, load_compile_root
from mt5_env import ENV_FILE, env_value, metaeditor_path_from_env, terminal_path_from_env
import telegram_notify
from run_tests import (
    EXPERTS_ROOT_FILE,
    LOG_DIR,
    REPORT_DIR,
    TEMPLATE_FILE,
    find_matching_running_terminals,
    find_mt5_path,
    infer_tester_fields_from_set,
    load_set_files,
    load_experts_from_dir,
    load_experts_root,
)

try:
    from portfolio_manager.generator import (
        find_report_files as portfolio_find_report_files,
        generate_dd_threshold_workbook,
        generate_drawdown_workbook,
        generate_portfolio_drawdown_workbook,
        generate_portfolio_valley_drawdown_workbook,
        generate_top_portfolio_valleys_workbook,
        generate_workbook as generate_portfolio_workbook,
    )
except Exception:
    portfolio_find_report_files = None
    generate_dd_threshold_workbook = None
    generate_drawdown_workbook = None
    generate_portfolio_drawdown_workbook = None
    generate_portfolio_valley_drawdown_workbook = None
    generate_top_portfolio_valleys_workbook = None
    generate_portfolio_workbook = None


BASE_DIR = Path(__file__).resolve().parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
COMPILE_ROOT_FILE = BASE_DIR / "compile_root.txt"
UI_SETTINGS_FILE = BASE_DIR / "ui_settings.ini"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TRUE_VALUES = {"1", "true", "yes", "on", "si"}


LIGHT_COLORS = {
    "bg": "#f8f9ff",
    "sidebar_bg": "#ffffff",
    "topbar_bg": "#ffffff",
    "panel": "#ffffff",
    "panel_alt": "#eff4ff",
    "panel_high": "#dce9ff",
    "panel_highest": "#d3e4fe",
    "text": "#0b1c30",
    "muted": "#45474c",
    "border": "#c5c6cd",
    "primary": "#091426",
    "primary_text": "#ffffff",
    "primary_container": "#1e293b",
    "primary_hover_text": "#ffffff",
    "on_primary_container": "#8590a6",
    "accent": "#006c49",
    "accent_hover": "#005236",
    "accent_soft": "#6cf8bb",
    "accent_soft_text": "#00714d",
    "danger": "#ba1a1a",
    "danger_soft": "#ffdad6",
    "log_bg": "#1e293b",
    "log_text": "#cbd5e1",
    "log_info": "#4edea3",
    "log_error": "#fca5a5",
    "log_debug": "#e2e8f0",
    "log_muted": "#94a3b8",
    "nav_active_bg": "#6cf8bb",
    "nav_active_text": "#00714d",
    "nav_inactive_text": "#45474c",
    "nav_hover_bg": "#dce9ff",
    "entry_bg": "#ffffff",
    "tree_bg": "#ffffff",
    "tree_odd": "#f8fafc",
    "tree_even": "#ffffff",
}

DARK_COLORS = {
    "bg": "#111827",
    "sidebar_bg": "#0f172a",
    "topbar_bg": "#111827",
    "panel": "#1f2937",
    "panel_alt": "#273449",
    "panel_high": "#334155",
    "panel_highest": "#40506a",
    "text": "#e5e7eb",
    "muted": "#aeb7c7",
    "border": "#41516a",
    "primary": "#e5e7eb",
    "primary_text": "#0b1120",
    "primary_container": "#0b1120",
    "primary_hover_text": "#ffffff",
    "on_primary_container": "#cbd5e1",
    "accent": "#22c55e",
    "accent_hover": "#16a34a",
    "accent_soft": "#14532d",
    "accent_soft_text": "#86efac",
    "danger": "#ef4444",
    "danger_soft": "#451a1a",
    "log_bg": "#0b1120",
    "log_text": "#dbeafe",
    "log_info": "#86efac",
    "log_error": "#fca5a5",
    "log_debug": "#dbeafe",
    "log_muted": "#94a3b8",
    "nav_active_bg": "#14532d",
    "nav_active_text": "#bbf7d0",
    "nav_inactive_text": "#cbd5e1",
    "nav_hover_bg": "#1e293b",
    "entry_bg": "#111827",
    "tree_bg": "#111827",
    "tree_odd": "#162033",
    "tree_even": "#111827",
}

COLORS = LIGHT_COLORS.copy()


def _widget_bg(widget) -> str:
    try:
        return widget.cget("bg") or widget.cget("background")
    except tk.TclError:
        return COLORS["bg"]


try:
    from PIL import Image, ImageDraw, ImageTk
    _PIL_OK = True
except Exception:
    _PIL_OK = False


class _CornerImageCache:
    """Cache anti-aliased rounded corner PhotoImages keyed by visual params."""
    _store: dict = {}

    @classmethod
    def get(cls, radius: int, card_bg: str, border: str, border_w: int,
            parent_bg: str, corner: str):
        if not _PIL_OK:
            return None
        key = (radius, card_bg, border, border_w, parent_bg, corner)
        cached = cls._store.get(key)
        if cached is not None:
            return cached
        ss = 4
        s = radius * ss
        bw = max(0, border_w * ss)
        img = Image.new("RGB", (s, s), parent_bg)
        draw = ImageDraw.Draw(img)
        # bbox de la elipse (2x el lado) anclada según la esquina
        if corner == "nw":
            bbox = (0, 0, 2 * s - 1, 2 * s - 1)
            start, end = 180, 270
        elif corner == "ne":
            bbox = (-s, 0, s - 1, 2 * s - 1)
            start, end = 270, 360
        elif corner == "sw":
            bbox = (0, -s, 2 * s - 1, s - 1)
            start, end = 90, 180
        else:  # se
            bbox = (-s, -s, s - 1, s - 1)
            start, end = 0, 90
        draw.ellipse(bbox, fill=card_bg)
        if bw > 0:
            draw.arc(bbox, start, end, fill=border, width=bw)
        img = img.resize((radius, radius), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        cls._store[key] = photo
        return photo


class _ButtonImageCache:
    """Cache anti-aliased rounded button background images."""
    _store: dict = {}

    @classmethod
    def get(cls, width: int, height: int, radius: int, fill: str,
            border: str, border_w: int, parent_bg: str):
        if not _PIL_OK or width <= 0 or height <= 0:
            return None
        key = (width, height, radius, fill, border, border_w, parent_bg)
        cached = cls._store.get(key)
        if cached is not None:
            return cached
        ss = 2
        w, h = width * ss, height * ss
        r = max(1, radius * ss)
        bw = max(0, border_w * ss)
        img = Image.new("RGB", (w, h), parent_bg)
        draw = ImageDraw.Draw(img)
        # rounded_rectangle existe desde Pillow 8.2
        try:
            draw.rounded_rectangle(
                (0, 0, w - 1, h - 1),
                radius=r, fill=fill,
                outline=(border if bw > 0 else None),
                width=bw,
            )
        except AttributeError:
            # Fallback ellipse+rect para Pillow viejo
            draw.ellipse((0, 0, 2 * r, 2 * r), fill=fill)
            draw.ellipse((w - 2 * r - 1, 0, w - 1, 2 * r), fill=fill)
            draw.ellipse((0, h - 2 * r - 1, 2 * r, h - 1), fill=fill)
            draw.ellipse((w - 2 * r - 1, h - 2 * r - 1, w - 1, h - 1), fill=fill)
            draw.rectangle((r, 0, w - r - 1, h - 1), fill=fill)
            draw.rectangle((0, r, w - 1, h - r - 1), fill=fill)
        img = img.resize((width, height), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        cls._store[key] = photo
        return photo


class RoundedCard(tk.Frame):
    """Frame with simulated rounded corners and a thin border."""

    def __init__(self, parent, *, radius: int = 12, bg: str | None = None,
                 border: str | None = None, parent_bg: str | None = None, **kw):
        bg = bg or COLORS["panel"]
        border = border or COLORS["border"]
        super().__init__(parent, bg=bg, **kw)
        self._radius = radius
        self._card_bg = bg
        self._border_color = border
        self._parent_bg = parent_bg or _widget_bg(parent)
        self._overlays: list[tk.Widget] = []
        self.bind("<Configure>", self._on_resize)
        self.after_idle(self._setup_rounding)

    def _setup_rounding(self) -> None:
        for w in self._overlays:
            try:
                w.destroy()
            except tk.TclError:
                pass
        self._overlays = []
        # Bordes rectos a lo largo de toda la card: los extremos quedan
        # ocultos por las esquinas (que se elevan al frente) y así no
        # se ve ningún corte/gap entre línea recta y curva.
        top = tk.Frame(self, bg=self._border_color, height=1)
        top.place(x=0, y=0, relwidth=1.0)
        bottom = tk.Frame(self, bg=self._border_color, height=1)
        bottom.place(x=0, rely=1.0, y=-1, relwidth=1.0)
        left = tk.Frame(self, bg=self._border_color, width=1)
        left.place(x=0, y=0, relheight=1.0)
        right = tk.Frame(self, bg=self._border_color, width=1)
        right.place(relx=1.0, x=-1, y=0, relheight=1.0)
        self._overlays.extend([top, bottom, left, right])
        # Esquinas redondeadas
        for anchor in ("nw", "ne", "sw", "se"):
            self._overlays.append(self._make_corner(anchor))
        self._raise_overlays()

    def _make_corner(self, anchor: str) -> tk.Widget:
        r = self._radius
        # Intenta con Pillow (anti-aliasing)
        photo = _CornerImageCache.get(r, self._card_bg, self._border_color, 1,
                                      self._parent_bg, anchor)
        if photo is not None:
            lbl = tk.Label(self, image=photo, bg=self._parent_bg, borderwidth=0,
                           highlightthickness=0)
            lbl.image = photo  # keep ref
            self._place_corner(lbl, anchor)
            return lbl
        # Fallback: dibujo con Canvas (sin AA)
        canvas = tk.Canvas(self, width=r, height=r, bg=self._parent_bg,
                           highlightthickness=0, borderwidth=0)
        if anchor == "nw":
            bbox, start = (0, 0, 2 * r, 2 * r), 90
        elif anchor == "ne":
            bbox, start = (-r, 0, r, 2 * r), 0
        elif anchor == "sw":
            bbox, start = (0, -r, 2 * r, r), 180
        else:
            bbox, start = (-r, -r, r, r), 270
        canvas.create_arc(*bbox, start=start, extent=90,
                          fill=self._card_bg, outline="", style="pieslice")
        canvas.create_arc(*bbox, start=start, extent=90,
                          outline=self._border_color, width=1, style="arc")
        self._place_corner(canvas, anchor)
        return canvas

    def _place_corner(self, w: tk.Widget, anchor: str) -> None:
        r = self._radius
        if anchor == "nw":
            w.place(x=0, y=0)
        elif anchor == "ne":
            w.place(relx=1.0, x=-r, y=0)
        elif anchor == "sw":
            w.place(x=0, rely=1.0, y=-r)
        else:
            w.place(relx=1.0, x=-r, rely=1.0, y=-r)

    def _raise_overlays(self) -> None:
        # Usa el comando Tcl directo para evitar el alias Canvas.lift = tag_raise en Python 3.x
        for w in self._overlays:
            try:
                self.tk.call("raise", w._w)
            except tk.TclError:
                pass

    def _on_resize(self, _event=None) -> None:
        self._raise_overlays()


class RoundedButton(tk.Canvas):
    """Canvas-based button with rounded corners + hover."""

    def __init__(self, parent, *, text: str = "", command=None,
                 bg: str = "#006c49", fg: str = "#ffffff",
                 hover_bg: str | None = None, hover_fg: str | None = None,
                 disabled_bg: str = "#8ba59c", disabled_fg: str = "#ffffff",
                 border: str | None = None, parent_bg: str | None = None,
                 font=("Segoe UI", 10, "bold"), radius: int = 10,
                 padx: int = 14, pady: int = 10, width: int | None = None,
                 height: int | None = None, anchor: str = "center"):
        parent_bg = parent_bg or _widget_bg(parent)
        super().__init__(parent, bg=parent_bg, highlightthickness=0, borderwidth=0, cursor="hand2")
        self._command = command
        self._bg = bg
        self._fg = fg
        self._hover_bg = hover_bg or bg
        self._hover_fg = hover_fg or fg
        self._disabled_bg = disabled_bg
        self._disabled_fg = disabled_fg
        self._border = border
        self._font = font
        self._radius = radius
        self._padx = padx
        self._pady = pady
        self._anchor = anchor
        self._text = text
        self._disabled = False
        self._rect_id = None
        self._text_id = None
        # Tamaño inicial basado en el texto
        if width is None or height is None:
            tmp = tk.Label(self, text=text, font=font)
            w = tmp.winfo_reqwidth() + padx * 2
            h = tmp.winfo_reqheight() + pady * 2
            tmp.destroy()
            width = width or w
            height = height or h
        self.configure(width=width, height=height)
        self._draw(self._bg, self._fg)
        self.bind("<Configure>", lambda _e: self._draw(self._current_bg(), self._current_fg()))
        self.bind("<Enter>", lambda _e: self._on_enter())
        self.bind("<Leave>", lambda _e: self._on_leave())
        self.bind("<Button-1>", lambda _e: self._on_click())

    def _current_bg(self) -> str:
        return self._disabled_bg if self._disabled else self._bg

    def _current_fg(self) -> str:
        return self._disabled_fg if self._disabled else self._fg

    def _round_rect_points(self, w: int, h: int, r: int) -> list[int]:
        # Inset 1px para que el outline no se clipee en los bordes del canvas
        x0, y0, x1, y1 = 1, 1, w - 2, h - 2
        return [
            x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
            x1, y1 - r, x1, y1, x1 - r, y1,
            x0 + r, y1, x0, y1, x0, y1 - r,
            x0, y0 + r, x0, y0, x0, y0,
        ]

    def _draw(self, bg: str, fg: str) -> None:
        w = max(int(self.winfo_width()), 1)
        h = max(int(self.winfo_height()), 1)
        self.delete("all")
        outline = self._border or bg
        border_w = 1 if self._border else 0
        photo = _ButtonImageCache.get(w, h, self._radius, bg, outline, border_w,
                                      _widget_bg(self.master))
        if photo is not None:
            self._bg_photo = photo  # keep ref
            self.create_image(0, 0, anchor="nw", image=photo)
        else:
            points = self._round_rect_points(w, h, self._radius)
            self._rect_id = self.create_polygon(points, smooth=True, fill=bg,
                                                outline=outline, width=1)
        # Texto centrado o anclado
        if self._anchor == "w":
            x, anchor = self._padx, "w"
        elif self._anchor == "e":
            x, anchor = w - self._padx, "e"
        else:
            x, anchor = w // 2, "center"
        self._text_id = self.create_text(x, h // 2, text=self._text, fill=fg, font=self._font, anchor=anchor)

    def configure_text(self, text: str) -> None:
        self._text = text
        self._draw(self._current_bg(), self._current_fg())

    def set_disabled(self, disabled: bool) -> None:
        self._disabled = bool(disabled)
        self.configure(cursor="" if disabled else "hand2")
        self._draw(self._current_bg(), self._current_fg())

    def set_colors(self, *, bg: str | None = None, fg: str | None = None,
                   hover_bg: str | None = None, hover_fg: str | None = None) -> None:
        if bg is not None:
            self._bg = bg
        if fg is not None:
            self._fg = fg
        if hover_bg is not None:
            self._hover_bg = hover_bg
        if hover_fg is not None:
            self._hover_fg = hover_fg
        self._draw(self._current_bg(), self._current_fg())

    def _on_enter(self) -> None:
        if not self._disabled:
            self._draw(self._hover_bg, self._hover_fg)

    def _on_leave(self) -> None:
        self._draw(self._current_bg(), self._current_fg())

    def _on_click(self) -> None:
        if self._disabled:
            return
        if self._command is not None:
            try:
                self._command()
            except Exception:
                pass


class ToggleSwitch(tk.Canvas):
    """Modern iOS-style toggle switch bound to a tk.BooleanVar."""

    def __init__(self, parent, variable: tk.BooleanVar, command=None, width: int = 44, height: int = 24,
                 bg: str = "#ffffff", on_color: str = "#006c49", off_color: str = "#c5c6cd"):
        super().__init__(parent, width=width, height=height, bg=bg, highlightthickness=0, borderwidth=0, cursor="hand2")
        self._var = variable
        self._on_color = on_color
        self._off_color = off_color
        self._cmd = command
        self._sw = width
        self._sh = height
        self._track = None
        self._knob = None
        self._redraw()
        self.bind("<Button-1>", self._toggle)
        try:
            self._trace = variable.trace_add("write", lambda *_: self._redraw())
        except Exception:
            pass

    def _round_rect(self, x1, y1, x2, y2, r, **kwargs):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.create_polygon(points, smooth=True, **kwargs)

    def _redraw(self):
        self.delete("all")
        on = bool(self._var.get())
        color = self._on_color if on else self._off_color
        self._round_rect(1, 1, self._sw - 1, self._sh - 1, self._sh // 2, fill=color, outline=color)
        pad = 3
        kr = self._sh - pad * 2
        kx = self._sw - pad - kr if on else pad
        self.create_oval(kx, pad, kx + kr, pad + kr, fill="#ffffff", outline="#ffffff")

    def _toggle(self, _event=None):
        self._var.set(not bool(self._var.get()))
        self._redraw()
        if self._cmd is not None:
            try:
                self._cmd()
            except Exception:
                pass


class MT5AutotesterUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("MT5 Autotester")
        self.geometry("1320x800")
        self.minsize(1180, 700)
        self.configure(bg=COLORS["bg"])
        self._apply_window_icon()

        self.process: subprocess.Popen[str] | None = None
        self.reader_thread: threading.Thread | None = None
        self.output_queue: queue.Queue[str | tuple[str, int]] = queue.Queue()
        self.stop_requested = False

        ui_settings = self._read_ui_settings()
        saved_paths = ui_settings["Paths"] if ui_settings.has_section("Paths") else {}
        saved_general = ui_settings["General"] if ui_settings.has_section("General") else {}
        saved_multi = ui_settings["Multiterminal"] if ui_settings.has_section("Multiterminal") else {}
        saved_theme = saved_general.get("theme", "light").strip().lower()
        self.theme_mode = tk.StringVar(value="dark" if saved_theme == "dark" else "light")
        self._apply_theme_palette()

        default_ubs_ready = BASE_DIR / "sets" / "ubs_ready"
        self.mt5_path = tk.StringVar(value=saved_paths.get("mt5_path", str(terminal_path_from_env() or find_mt5_path(None))))
        self.mt5_data_root = tk.StringVar(value=saved_paths.get("mt5_data_root", ""))
        self.metaeditor_path = tk.StringVar(
            value=saved_paths.get("metaeditor_path", str(metaeditor_path_from_env() or find_metaeditor_path(None, None)))
        )
        self.compile_root = tk.StringVar(value=saved_paths.get("compile_root", str(load_compile_root() or "")))
        self.compile_file = tk.StringVar(value=saved_paths.get("compile_file", ""))
        self.experts_root = tk.StringVar(value=saved_paths.get("experts_root", str(load_experts_root() or "")))
        self.ubs_ex5_file = tk.StringVar(value=saved_paths.get("ubs_ex5_file", ""))
        self.set_files_root = tk.StringVar(
            value=saved_paths.get(
                "set_files_root",
                str(default_ubs_ready) if default_ubs_ready.exists() else "",
            )
        )
        self.ubs_set_file = tk.StringVar(value=saved_paths.get("ubs_set_file", ""))
        self.template_path = tk.StringVar(value=saved_paths.get("template_path", str(TEMPLATE_FILE)))
        self.ubs_generation_output = tk.StringVar(
            value=saved_paths.get("ubs_generation_output", str(BASE_DIR / "outputs" / "ubs_agent"))
        )
        self.portfolio_input = tk.StringVar(value=saved_paths.get("portfolio_input", str(REPORT_DIR)))
        self.portfolio_output = tk.StringVar(
            value=saved_paths.get("portfolio_output", str(BASE_DIR / "outputs" / "ALL_STRATEGIES.xlsx"))
        )
        self.portfolio_threshold = tk.StringVar(value=saved_general.get("portfolio_threshold", "50"))
        self.recursive = tk.BooleanVar(value=saved_general.get("recursive", "0") in {"1", "true", "yes", "on"})
        self.delay = tk.IntVar(value=self._saved_int(saved_general.get("delay"), 5))
        self.ubs_generation_count = tk.IntVar(value=self._saved_int(saved_general.get("ubs_generation_count"), 1))
        self.ubs_variants_per_seed = tk.IntVar(value=self._saved_int(saved_general.get("ubs_variants_per_seed"), 3))
        self.ubs_max_seeds = tk.IntVar(value=self._saved_int(saved_general.get("ubs_max_seeds"), 50))
        self.ubs_agent_execute = tk.BooleanVar(value=saved_general.get("ubs_agent_execute", "0") in {"1", "true", "yes", "on"})
        self.ubs_pass_min_net_profit = tk.StringVar(value=saved_general.get("ubs_pass_min_net_profit", "100"))
        self.ubs_pass_min_profit_factor = tk.StringVar(value=saved_general.get("ubs_pass_min_profit_factor", "1.20"))
        self.ubs_pass_min_trades = tk.IntVar(value=self._saved_int(saved_general.get("ubs_pass_min_trades"), 50))
        self.ubs_pass_max_drawdown_pct = tk.StringVar(value=saved_general.get("ubs_pass_max_drawdown_pct", "25"))
        self.ubs_pass_min_recovery_factor = tk.StringVar(value=saved_general.get("ubs_pass_min_recovery_factor", "1.0"))
        self.symbol_suffix_enabled = tk.BooleanVar(value=saved_general.get("symbol_suffix_enabled", "0") in {"1", "true", "yes", "on"})
        self.symbol_suffix = tk.StringVar(value=saved_general.get("symbol_suffix", ""))
        self.symbol_map_enabled = tk.BooleanVar(value=saved_general.get("symbol_map_enabled", "0") in {"1", "true", "yes", "on"})
        self.symbol_map = tk.StringVar(value=saved_general.get("symbol_map", ""))
        _tg_default = "1" if (env_value("TELEGRAM_BOT_TOKEN") and env_value("TELEGRAM_CHAT_ID")) else "0"
        self.telegram_enabled = tk.BooleanVar(value=self._bool_setting(saved_general.get("telegram_enabled", _tg_default)))
        self.multiterminal_enabled = tk.BooleanVar(value=self._bool_setting(saved_multi.get("enabled"), False))
        self.multiterminal_workers = tk.IntVar(value=max(1, self._saved_int(saved_multi.get("workers"), 1)))
        self.multiterminal_profiles = self._read_multiterminal_profiles(ui_settings)
        self.mt_selected_index: int | None = None
        self.mt_profile_enabled = tk.BooleanVar(value=True)
        self.mt_profile_portable = tk.BooleanVar(value=False)
        self.mt_profile_name = tk.StringVar(value="")
        self.mt_profile_mt5_path = tk.StringVar(value="")
        self.mt_profile_data_dir = tk.StringVar(value="")
        self.mt_profile_experts_root = tk.StringVar(value="")
        self.mt_profile_ubs_ex5_file = tk.StringVar(value="")
        self.multiterminal_summary = tk.StringVar(value="")

        self.tester_vars: dict[str, tk.StringVar] = {}
        self.status_text = tk.StringVar(value="Listo")
        self.running_text = tk.StringVar(value="Sin proceso activo")
        self.experts_count = tk.StringVar(value="0")
        self.reports_count = tk.StringVar(value="0")
        self.portfolio_count = tk.StringVar(value="Reports encontrados: 0")
        self.portfolio_status = tk.StringVar(value="Selecciona una carpeta de reportes y genera el Excel.")
        self.ubs_results_summary = tk.StringVar(value="Sin resultados UBS")
        self.ubs_results_status = tk.StringVar(value="Memoria UBS no cargada")
        self.ubs_history_summary = tk.StringVar(value="Sin historico UBS")
        self.ubs_history_candidate_summary = tk.StringVar(value="Selecciona un run")
        self.ubs_seed_eval_summary = tk.StringVar(value="Semillas: sin evaluar")
        self.ubs_universe_summary = tk.StringVar(value="Sin universo UBS")
        self.ubs_timeframe_summary = tk.StringVar(value="Sin pesos de timeframe")
        self.ubs_compare_summary = tk.StringVar(value="Sin resultados UBS")
        self.ubs_compare_detail = tk.StringVar(value="Selecciona un resultado para comparar contra su seed.")
        self.ubs_compare_run_id = tk.StringVar(value="")
        self.ubs_seed_detail = tk.StringVar(value="Selecciona una semilla")
        self.ubs_seed_override_symbol = tk.StringVar(value="")
        self.ubs_seed_override_period = tk.StringVar(value="")
        self.ubs_continue_status = tk.StringVar(value="Continuar: sin memoria UBS")
        self.mode_text = tk.StringVar(value="Real")
        self.last_log_text = tk.StringVar(value="Sin log reciente")
        self.active_task_text = tk.StringVar(value="Sin tarea activa")
        self.active_task_detail = tk.StringVar(value="Pulsa una accion para empezar")
        self.engine_status_text = tk.StringVar(value="Engine Ready")
        self.progress_var = tk.DoubleVar(value=0)
        self._progress_total = 0
        self._progress_done = 0
        self._progress_target = 0.0
        self._progress_running = False
        self.portfolio_running = False
        self.portfolio_buttons: list[ttk.Button] = []
        self.nav_buttons: dict[str, tk.Button] = {}
        self.section_frames: dict[str, ttk.Frame] = {}
        self.ubs_result_paths: dict[str, dict[str, str]] = {}
        self.ubs_history_candidate_paths: dict[str, dict[str, str]] = {}
        self.ubs_compare_paths: dict[str, dict[str, str]] = {}
        self.ubs_seed_paths: dict[str, dict[str, str]] = {}
        self._tree_sort_reverse: dict[tuple[str, str], bool] = {}
        self.ubs_continue_button: RoundedButton | None = None
        self.current_section = "panel"

        self._configure_style()
        self._build_ui()
        try:
            self._load_template()
        except Exception:
            self.status_text.set("Template tester no cargado")
        self._refresh_all()
        self.after(60, self._animate_progress)
        self.after(120, self._drain_output_queue)

    def _apply_theme_palette(self) -> None:
        COLORS.clear()
        COLORS.update(DARK_COLORS if self.theme_mode.get() == "dark" else LIGHT_COLORS)

    def _apply_window_icon(self) -> None:
        # Busca el icono junto al ejecutable (instalación) o junto al fuente (dev) o en assets/
        candidates = [
            BASE_DIR / "app_icon.ico",
            BASE_DIR / "assets" / "app_icon.ico",
            BASE_DIR / "app_icon.png",
            BASE_DIR / "assets" / "app_icon.png",
        ]
        if getattr(sys, "_MEIPASS", None):
            mei = Path(sys._MEIPASS)
            candidates = [mei / "app_icon.ico", mei / "assets" / "app_icon.ico",
                          mei / "app_icon.png", mei / "assets" / "app_icon.png"] + candidates
        ico = next((p for p in candidates if p.exists() and p.suffix.lower() == ".ico"), None)
        png = next((p for p in candidates if p.exists() and p.suffix.lower() == ".png"), None)
        try:
            if ico is not None:
                self.iconbitmap(default=str(ico))
        except tk.TclError:
            pass
        try:
            if png is not None:
                self._icon_image = tk.PhotoImage(file=str(png))
                self.iconphoto(True, self._icon_image)
        except tk.TclError:
            pass

    def _read_ui_settings(self) -> configparser.ConfigParser:
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        if UI_SETTINGS_FILE.exists():
            parser.read(UI_SETTINGS_FILE, encoding="utf-8-sig")
        return parser

    def _saved_int(self, value: str | None, default: int) -> int:
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    def _bool_setting(self, value: object, default: bool = False) -> bool:
        if value is None:
            return default
        return str(value).strip().lower() in TRUE_VALUES

    def _read_multiterminal_profiles(self, parser: configparser.ConfigParser) -> list[dict[str, object]]:
        sections = [section for section in parser.sections() if section.lower().startswith("terminal.")]

        def section_key(section: str) -> tuple[int, str]:
            suffix = section.split(".", 1)[1] if "." in section else section
            try:
                return (int(suffix), section)
            except ValueError:
                return (9999, section)

        profiles: list[dict[str, object]] = []
        for section in sorted(sections, key=section_key):
            data = parser[section]
            profiles.append({
                "enabled": self._bool_setting(data.get("enabled"), True),
                "name": data.get("name", section).strip() or section,
                "mt5_path": data.get("mt5_path", "").strip(),
                "data_dir": data.get("data_dir", "").strip(),
                "experts_root": data.get("experts_root", "").strip(),
                "ubs_ex5_file": data.get("ubs_ex5_file", "").strip(),
                "portable": self._bool_setting(data.get("portable"), False),
            })
        if profiles:
            return profiles
        return [{
            "enabled": bool(self.mt5_path.get().strip()),
            "name": "MT5 principal",
            "mt5_path": self.mt5_path.get().strip(),
            "data_dir": self.mt5_data_root.get().strip(),
            "experts_root": self.experts_root.get().strip(),
            "ubs_ex5_file": self.ubs_ex5_file.get().strip(),
            "portable": False,
        }]

    def report_callback_exception(self, exc_type, exc_value, exc_traceback) -> None:
        if exc_type is ValueError:
            self._show_error("Configuracion incompleta", str(exc_value))
            return
        details = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        self._show_error("Error", str(exc_value), details)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        base_font = ("Segoe UI", 10)
        bold_font = ("Segoe UI", 10, "bold")

        style.configure(".", font=base_font, background=COLORS["bg"], foreground=COLORS["text"])
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Panel.TFrame", background=COLORS["panel"])
        style.configure("Alt.TFrame", background=COLORS["panel_alt"])
        style.configure("Sidebar.TFrame", background=COLORS["sidebar_bg"])
        style.configure("Topbar.TFrame", background=COLORS["topbar_bg"])
        style.configure("Card.TFrame", background=COLORS["panel"], relief="solid", borderwidth=1)
        style.configure("CardSoft.TFrame", background=COLORS["panel_alt"])
        style.configure("Log.TFrame", background=COLORS["log_bg"])

        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
        style.configure("Panel.TLabel", background=COLORS["panel"], foreground=COLORS["text"])
        style.configure("Sidebar.TLabel", background=COLORS["sidebar_bg"], foreground=COLORS["text"])
        style.configure("Topbar.TLabel", background=COLORS["topbar_bg"], foreground=COLORS["text"])
        style.configure("Muted.TLabel", background=COLORS["panel"], foreground=COLORS["muted"])
        style.configure("MutedBg.TLabel", background=COLORS["bg"], foreground=COLORS["muted"])

        style.configure("LabelCaps.TLabel", background=COLORS["panel"], foreground=COLORS["muted"], font=("Segoe UI", 9, "bold"))
        style.configure("Metric.TLabel", background=COLORS["panel"], foreground=COLORS["primary"], font=("Segoe UI", 26, "bold"))
        style.configure("MetricName.TLabel", background=COLORS["panel"], foreground=COLORS["muted"], font=("Segoe UI", 9, "bold"))
        style.configure("MetricIcon.TLabel", background=COLORS["panel"], foreground=COLORS["accent"], font=("Segoe UI Symbol", 16))
        style.configure("Title.TLabel", background=COLORS["sidebar_bg"], foreground=COLORS["primary"], font=("Segoe UI", 18, "bold"))
        style.configure("Version.TLabel", background=COLORS["sidebar_bg"], foreground=COLORS["muted"], font=("Segoe UI", 9))
        style.configure("AppTitle.TLabel", background=COLORS["topbar_bg"], foreground=COLORS["primary"], font=("Segoe UI", 16, "bold"))
        style.configure("Subtitle.TLabel", background=COLORS["bg"], foreground=COLORS["muted"], font=("Segoe UI", 9))
        style.configure("SectionTitle.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Segoe UI", 14, "bold"))
        style.configure("CardTitle.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Segoe UI", 11, "bold"))
        style.configure("CardDesc.TLabel", background=COLORS["panel"], foreground=COLORS["muted"], font=("Segoe UI", 9))
        style.configure("Mono.TLabel", background=COLORS["panel"], foreground=COLORS["primary"], font=("Consolas", 9))
        style.configure("MonoAlt.TLabel", background=COLORS["panel_alt"], foreground=COLORS["primary"], font=("Consolas", 9))
        style.configure("Chip.TLabel", background=COLORS["panel_highest"], foreground=COLORS["primary"], font=("Segoe UI", 8, "bold"), padding=(8, 3))
        style.configure("ChipAccent.TLabel", background=COLORS["accent_soft"], foreground=COLORS["accent_soft_text"], font=("Segoe UI", 8, "bold"), padding=(8, 3))

        style.configure("TButton", padding=(12, 8), borderwidth=0, background=COLORS["panel_alt"], foreground=COLORS["text"], font=bold_font)
        style.map("TButton", background=[("active", COLORS["panel_high"])])
        style.configure("Primary.TButton", background=COLORS["accent"], foreground="#ffffff", padding=(14, 9), font=bold_font)
        style.map("Primary.TButton", background=[("active", COLORS["accent_hover"]), ("disabled", "#8ba59c")])
        style.configure("PrimaryDark.TButton", background=COLORS["primary"], foreground=COLORS["primary_text"], padding=(14, 9), font=bold_font)
        style.map("PrimaryDark.TButton",
                  background=[("active", COLORS["primary_container"]), ("disabled", "#5e6b7e")],
                  foreground=[("active", COLORS["primary_hover_text"]), ("disabled", COLORS["primary_text"])])
        style.configure("Danger.TButton", background=COLORS["danger"], foreground="#ffffff", padding=(14, 9), font=bold_font)
        style.map("Danger.TButton", background=[("active", "#8a0d0d")])
        style.configure("DangerOutline.TButton", background=COLORS["topbar_bg"], foreground=COLORS["danger"], padding=(14, 6), font=bold_font)
        style.map("DangerOutline.TButton", background=[("active", COLORS["danger_soft"])])
        style.configure("Tool.TButton", padding=(8, 6))
        style.configure("Action.TButton", background=COLORS["panel"], foreground=COLORS["text"], padding=(12, 10), borderwidth=1, font=bold_font, anchor="w")
        style.map("Action.TButton", background=[("active", COLORS["panel_alt"])])

        style.configure("TEntry", fieldbackground=COLORS["entry_bg"], foreground=COLORS["text"],
                        insertcolor=COLORS["text"], bordercolor=COLORS["border"],
                        lightcolor=COLORS["border"], padding=7)
        style.configure("TSpinbox", fieldbackground=COLORS["entry_bg"], foreground=COLORS["text"],
                        insertcolor=COLORS["text"], bordercolor=COLORS["border"], padding=7)
        style.configure("Treeview", background=COLORS["tree_bg"], fieldbackground=COLORS["tree_bg"],
                        foreground=COLORS["text"], rowheight=26, borderwidth=0)
        style.map("Treeview", background=[("selected", COLORS["accent"])], foreground=[("selected", "#ffffff")])
        style.configure("Treeview.Heading", background=COLORS["panel_alt"], foreground=COLORS["muted"], font=("Segoe UI", 8, "bold"), padding=(6, 4))
        style.configure("TCheckbutton", background=COLORS["panel"], foreground=COLORS["text"])
        style.configure("Panel.TCheckbutton", background=COLORS["panel"], foreground=COLORS["text"])
        style.configure("Horizontal.TProgressbar", background=COLORS["accent"], troughcolor=COLORS["panel_high"], bordercolor=COLORS["panel_high"], lightcolor=COLORS["accent"], darkcolor=COLORS["accent"], thickness=10)

    def _attach_tree_scrollbars(
        self,
        parent: ttk.Frame,
        tree: ttk.Treeview,
        row: int,
        column: int = 0,
        *,
        vertical: bool = True,
        horizontal: bool = True,
    ) -> None:
        tree.grid(row=row, column=column, sticky="nsew")
        if vertical:
            y_scroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
            y_scroll.grid(row=row, column=column + 1, sticky="ns")
            tree.configure(yscrollcommand=y_scroll.set)
        if horizontal:
            x_scroll = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
            x_scroll.grid(row=row + 1, column=column, sticky="ew")
            tree.configure(xscrollcommand=x_scroll.set)

    def _make_tree_sortable(self, tree: ttk.Treeview) -> None:
        for column in tree["columns"]:
            title = str(tree.heading(column).get("text") or column)
            tree.heading(column, text=title, command=lambda col=column: self._sort_tree_by_column(tree, col))

    def _sort_tree_by_column(self, tree: ttk.Treeview, column: str) -> None:
        sort_id = (str(tree), column)
        reverse = self._tree_sort_reverse.get(sort_id, False)
        rows = [(self._tree_sort_value(tree.set(item, column)), item) for item in tree.get_children("")]
        rows.sort(key=lambda item: item[0], reverse=reverse)
        for index, (_, item) in enumerate(rows):
            tree.move(item, "", index)
        self._tree_sort_reverse[sort_id] = not reverse

    def _tree_sort_value(self, value: object) -> tuple[int, object]:
        raw = str(value or "").strip()
        if not raw or raw == "-":
            return (2, "")
        numeric = raw.rstrip("%").replace(",", "")
        try:
            return (0, float(numeric))
        except ValueError:
            return (1, raw.casefold())

    def _short_filename(self, value: str | Path, max_length: int = 72) -> str:
        name = Path(str(value)).name
        if len(name) <= max_length:
            return name
        suffix = Path(name).suffix
        stem = name[: -len(suffix)] if suffix else name
        tail_length = max(12, max_length // 3)
        head_length = max(8, max_length - tail_length - len(suffix) - 3)
        return f"{stem[:head_length]}...{stem[-tail_length:]}{suffix}"

    def _ubs_variant_code(self, set_name: str) -> str:
        matches = re.findall(r"g\d+_s\d+_v\d+", set_name, flags=re.IGNORECASE)
        return matches[-1] if matches else ""

    def _format_ubs_set_label(self, row: sqlite3.Row) -> str:
        set_path = Path(str(row["set_path"] or ""))
        name = set_path.name
        candidate_id = str(row["id"] or "").strip()
        symbol = str(row["target_symbol"] or row["symbol"] or "").strip()
        period = str(row["period"] or "").strip()
        variant_code = self._ubs_variant_code(name)
        prefix = f"#{candidate_id} " if candidate_id else ""
        if symbol and period and variant_code:
            return f"{prefix}{symbol}_{period}_{variant_code}{set_path.suffix or '.set'}"
        if variant_code:
            return f"{prefix}{variant_code}{set_path.suffix or '.set'}"
        return f"{prefix}{self._short_filename(name)}"

    def _build_ui(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        self._build_sidebar()

        content_holder = ttk.Frame(self, padding=(24, 16, 24, 12))
        content_holder.grid(row=0, column=1, rowspan=2, sticky="nsew")
        content_holder.columnconfigure(0, weight=1)
        content_holder.rowconfigure(0, weight=1)

        for key in ("panel", "agente_ubs", "ubs_seeds", "ubs_resultados", "ubs_historico", "ubs_universo", "ubs_comparar", "portfolio", "multiterminal", "configuracion", "archivos", "logs"):
            frame = ttk.Frame(content_holder, padding=0)
            frame.grid(row=0, column=0, sticky="nsew")
            self.section_frames[key] = frame

        self._build_dashboard(self.section_frames["panel"])
        self._build_ubs_agent(self.section_frames["agente_ubs"])
        self._build_ubs_seeds(self.section_frames["ubs_seeds"])
        self._build_ubs_results(self.section_frames["ubs_resultados"])
        self._build_ubs_history(self.section_frames["ubs_historico"])
        self._build_ubs_universe(self.section_frames["ubs_universo"])
        self._build_ubs_comparison(self.section_frames["ubs_comparar"])
        self._build_portfolio(self.section_frames["portfolio"])
        self._build_multiterminal(self.section_frames["multiterminal"])
        self._build_settings(self.section_frames["configuracion"])
        self._build_files(self.section_frames["archivos"])
        self._build_logs(self.section_frames["logs"])

        self._show_section("panel")

        footer = ttk.Frame(self, padding=(24, 0, 24, 10))
        footer.grid(row=2, column=0, columnspan=2, sticky="ew")
        footer.columnconfigure(1, weight=1)
        dot = tk.Label(footer, text="●", bg=COLORS["bg"], fg=COLORS["accent"], font=("Segoe UI", 12))
        dot.grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Label(footer, textvariable=self.engine_status_text, style="Subtitle.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(footer, textvariable=self.status_text, style="Subtitle.TLabel").grid(row=0, column=2, sticky="e", padx=(0, 18))
        ttk.Label(footer, textvariable=self.running_text, style="Subtitle.TLabel").grid(row=0, column=3, sticky="e")

    def _build_sidebar(self) -> None:
        sidebar = ttk.Frame(self, style="Sidebar.TFrame", padding=(16, 18, 16, 16), width=240)
        sidebar.grid(row=0, column=0, rowspan=3, sticky="nsw")
        sidebar.grid_propagate(False)
        sidebar.rowconfigure(1, weight=1)

        header = ttk.Frame(sidebar, style="Sidebar.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 24))
        ttk.Label(header, text="MT5 Autotester", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="v1.4.0", style="Version.TLabel").grid(row=1, column=0, sticky="w")

        nav = ttk.Frame(sidebar, style="Sidebar.TFrame")
        nav.grid(row=1, column=0, sticky="new")
        nav.columnconfigure(0, weight=1)
        items = [
            ("panel", "▦  Panel"),
            ("agente_ubs", "UBS  Agente UBS"),
            ("ubs_seeds", "UBS  Seeds"),
            ("ubs_resultados", "UBS  Resultados"),
            ("ubs_historico", "UBS  Historico"),
            ("ubs_universo", "UBS  Universo"),
            ("ubs_comparar", "UBS  Comparar"),
            ("multiterminal", "MT5  Multiterminales"),
            ("portfolio", "▤  Portfolio"),
            ("configuracion", "⚙  Configuracion"),
            ("archivos", "▤  Archivos"),
            ("logs", "≣  Logs"),
        ]
        for index, (key, label) in enumerate(items):
            btn = RoundedButton(
                nav, text=label, anchor="w",
                bg=COLORS["sidebar_bg"], fg=COLORS["nav_inactive_text"],
                hover_bg=COLORS["nav_hover_bg"], hover_fg=COLORS["text"],
                font=("Segoe UI", 10, "bold"),
                radius=10, padx=14, pady=10,
                parent_bg=COLORS["sidebar_bg"],
                command=lambda k=key: self._show_section(k),
            )
            btn.grid(row=index, column=0, sticky="ew", pady=2)
            self.nav_buttons[key] = btn

        bottom = ttk.Frame(sidebar, style="Sidebar.TFrame")
        bottom.grid(row=2, column=0, sticky="sew")
        bottom.columnconfigure(0, weight=1)
        ttk.Button(
            bottom,
            text="Compilar y backtest",
            style="PrimaryDark.TButton",
            command=self._run_full_flow,
        ).grid(row=0, column=0, sticky="ew", pady=(8, 12))
        self.theme_button = ttk.Button(
            bottom,
            text=self._theme_button_text(),
            style="TButton",
            command=self._toggle_theme,
        )
        self.theme_button.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(bottom, text="ESTADO DEL SISTEMA", background=COLORS["sidebar_bg"], foreground=COLORS["muted"],
                  font=("Segoe UI", 8, "bold")).grid(row=2, column=0, sticky="w", pady=(8, 4))
        ttk.Label(bottom, text="● Engine Ready", background=COLORS["sidebar_bg"], foreground=COLORS["accent"],
                  font=("Segoe UI", 9)).grid(row=3, column=0, sticky="w")

    def _theme_button_text(self) -> str:
        return "Modo light" if self.theme_mode.get() == "dark" else "Modo dark"

    def _toggle_theme(self) -> None:
        if self.process and self.process.poll() is None:
            messagebox.showwarning("Proceso activo", "Espera a que termine el proceso antes de cambiar el tema.")
            return
        section = self.current_section
        self.theme_mode.set("light" if self.theme_mode.get() == "dark" else "dark")
        self._apply_theme_palette()
        _CornerImageCache._store.clear()
        _ButtonImageCache._store.clear()
        self.configure(bg=COLORS["bg"])
        for child in self.winfo_children():
            child.destroy()
        self.nav_buttons.clear()
        self.section_frames.clear()
        self._configure_style()
        self._build_ui()
        self._refresh_all()
        self._show_section(section)
        try:
            self._write_ui_settings()
        except Exception:
            pass
        self.status_text.set(f"Tema {self.theme_mode.get()} aplicado")

    def _show_section(self, key: str) -> None:
        self.current_section = key
        frame = self.section_frames.get(key)
        if frame is not None:
            frame.tkraise()
        for k, btn in self.nav_buttons.items():
            if isinstance(btn, RoundedButton):
                if k == key:
                    btn.set_colors(bg=COLORS["nav_active_bg"], fg=COLORS["nav_active_text"],
                                   hover_bg=COLORS["nav_active_bg"], hover_fg=COLORS["nav_active_text"])
                else:
                    btn.set_colors(bg=COLORS["sidebar_bg"], fg=COLORS["nav_inactive_text"],
                                   hover_bg=COLORS["nav_hover_bg"], hover_fg=COLORS["text"])
            else:
                if k == key:
                    btn.configure(bg=COLORS["nav_active_bg"], fg=COLORS["nav_active_text"], activebackground=COLORS["nav_active_bg"])
                else:
                    btn.configure(bg=COLORS["sidebar_bg"], fg=COLORS["nav_inactive_text"], activebackground=COLORS["nav_hover_bg"])

    def _build_dashboard(self, parent: ttk.Frame) -> None:
        outer = parent
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        canvas = tk.Canvas(outer, bg=COLORS["bg"], highlightthickness=0)
        scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        parent = ttk.Frame(canvas, padding=(0, 0, 10, 0))
        content_id = canvas.create_window((0, 0), window=parent, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(content_id, width=event.width))
        parent.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))

        def _on_wheel(event):
            delta = -1 if event.delta > 0 else 1
            if hasattr(event, "num"):
                if event.num == 4:
                    delta = -1
                elif event.num == 5:
                    delta = 1
            canvas.yview_scroll(delta, "units")

        def _bind_wheel(_e=None):
            self.bind_all("<MouseWheel>", _on_wheel)
            self.bind_all("<Button-4>", _on_wheel)
            self.bind_all("<Button-5>", _on_wheel)

        def _unbind_wheel(_e=None):
            self.unbind_all("<MouseWheel>")
            self.unbind_all("<Button-4>")
            self.unbind_all("<Button-5>")

        for widget in (canvas, outer, parent):
            widget.bind("<Enter>", _bind_wheel)
            widget.bind("<Leave>", _unbind_wheel)

        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        metrics = ttk.Frame(parent)
        metrics.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        for column in range(4):
            metrics.columnconfigure(column, weight=1)
        self._metric(metrics, 0, "Expert Advisors", self.experts_count, "▦")
        self._metric(metrics, 1, "Reportes generados", self.reports_count, "▤")
        self._metric(metrics, 2, "Modo ejecucion", self.mode_text, "⚡")


        active_task = self._card(parent, "Tarea activa", chip_text=None)
        active_task.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        active_task.columnconfigure(0, weight=1)
        task_row = ttk.Frame(active_task, style="Panel.TFrame")
        task_row.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 8))
        task_row.columnconfigure(0, weight=1)
        ttk.Label(task_row, textvariable=self.active_task_text, style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(task_row, textvariable=self.active_task_detail, style="CardDesc.TLabel").grid(row=0, column=1, sticky="e")
        self.progress_bar = ttk.Progressbar(active_task, mode="determinate", maximum=100,
                                             variable=self.progress_var, style="Horizontal.TProgressbar")
        self.progress_bar.grid(row=2, column=0, sticky="ew", padx=20, pady=(4, 18))

        body = ttk.Frame(parent)
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=8)
        body.columnconfigure(1, weight=4)

        actions_card = self._card(body, "Acciones")
        actions_card.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        actions_card.columnconfigure(0, weight=1)
        actions_card.columnconfigure(1, weight=1)
        self._build_multiterminal_inline(actions_card)

        self._action_card(
            actions_card, 2, 0,
            icon="< >",
            title="Compilar .mq5",
            description="Regenera los .ex5 a partir de los .mq5 del directorio fuente.",
            command=self._run_compile,
        )
        self._action_card(
            actions_card, 2, 1,
            icon="▶",
            title="Ejecutar backtests",
            description="Lanza la cola configurada contra los datos historicos.",
            command=self._run_backtests,
        )
        self._action_card(
            actions_card, 3, 0,
            icon="UBS",
            title="Tester UBS",
            description="Testea un solo bot usando todos los set files configurados.",
            command=self._run_ubs_tester,
        )
        self._action_card(
            actions_card, 3, 1,
            icon="GEN",
            title="Agente UBS",
            description="Elige seeds/assets, muta parametros y usa feedback si ejecuta backtests.",
            command=self._run_ubs_generator,
        )

        compile_and = RoundedButton(
            actions_card,
            text="🚀  Compilar y backtest      Flujo completo automatizado",
            bg=COLORS["accent"], hover_bg=COLORS["accent_hover"],
            font=("Segoe UI", 11, "bold"),
            radius=14, padx=20, pady=18, anchor="w",
            parent_bg=COLORS["panel"],
            command=self._run_full_flow,
        )
        compile_and.grid(row=4, column=0, columnspan=2, sticky="ew", padx=20, pady=(8, 8))

        stop_btn = RoundedButton(
            actions_card,
            text="✕  DETENER PROCESO",
            bg=COLORS["danger"], hover_bg="#8a0d0d",
            font=("Segoe UI", 10, "bold"),
            radius=12, padx=16, pady=12,
            parent_bg=COLORS["panel"],
            command=self._stop_process,
        )
        stop_btn.grid(row=5, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 18))

        config_card = self._card(body, "Configuration")
        config_card.grid(row=0, column=1, sticky="nsew")
        config_card.columnconfigure(0, weight=1)

        rec_row = tk.Frame(config_card, bg=COLORS["panel"])
        rec_row.grid(row=1, column=0, sticky="ew", padx=20, pady=(6, 0))
        rec_row.columnconfigure(0, weight=1)
        rec_text = tk.Frame(rec_row, bg=COLORS["panel"])
        rec_text.grid(row=0, column=0, sticky="w")
        tk.Label(rec_text, text="Recursivo", bg=COLORS["panel"], fg=COLORS["text"], font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(rec_text, text="Procesar todos los archivos de la carpeta raiz", bg=COLORS["panel"], fg=COLORS["muted"], font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w")
        ToggleSwitch(rec_row, variable=self.recursive, bg=COLORS["panel"], width=34, height=18).grid(row=0, column=1, sticky="ne", pady=(4, 0))

        delay_lbl = tk.Frame(config_card, bg=COLORS["panel"])
        delay_lbl.grid(row=2, column=0, sticky="ew", padx=20, pady=(18, 6))
        tk.Label(delay_lbl, text="Pausa entre tests (s)", bg=COLORS["panel"], fg=COLORS["text"], font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Spinbox(config_card, from_=0, to=120, textvariable=self.delay).grid(row=3, column=0, sticky="ew", padx=20)

        suffix_lbl = tk.Frame(config_card, bg=COLORS["panel"])
        suffix_lbl.grid(row=4, column=0, sticky="ew", padx=20, pady=(18, 6))
        suffix_lbl.columnconfigure(0, weight=1)
        suffix_text = tk.Frame(suffix_lbl, bg=COLORS["panel"])
        suffix_text.grid(row=0, column=0, sticky="w")
        tk.Label(suffix_text, text="Sufijo simbolos", bg=COLORS["panel"], fg=COLORS["text"], font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(suffix_text, text="Ejemplo: .a convierte XAUUSD en XAUUSD.a", bg=COLORS["panel"], fg=COLORS["muted"], font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w")
        ToggleSwitch(suffix_lbl, variable=self.symbol_suffix_enabled, bg=COLORS["panel"], width=34, height=18).grid(row=0, column=1, sticky="ne", pady=(4, 0))
        ttk.Entry(config_card, textvariable=self.symbol_suffix).grid(row=5, column=0, sticky="ew", padx=20)

        map_lbl = tk.Frame(config_card, bg=COLORS["panel"])
        map_lbl.grid(row=6, column=0, sticky="ew", padx=20, pady=(18, 6))
        map_lbl.columnconfigure(0, weight=1)
        map_text = tk.Frame(map_lbl, bg=COLORS["panel"])
        map_text.grid(row=0, column=0, sticky="w")
        tk.Label(map_text, text="Correspondencia simbolos", bg=COLORS["panel"], fg=COLORS["text"], font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(map_text, text="Ejemplo: XTIUSD=USOIL, GER40=DAX", bg=COLORS["panel"], fg=COLORS["muted"], font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w")
        ToggleSwitch(map_lbl, variable=self.symbol_map_enabled, bg=COLORS["panel"], width=34, height=18).grid(row=0, column=1, sticky="ne", pady=(4, 0))
        ttk.Entry(config_card, textvariable=self.symbol_map).grid(row=7, column=0, sticky="ew", padx=20)

        tg_row = tk.Frame(config_card, bg=COLORS["panel"])
        tg_row.grid(row=8, column=0, sticky="ew", padx=20, pady=(18, 0))
        tg_row.columnconfigure(0, weight=1)
        tg_text = tk.Frame(tg_row, bg=COLORS["panel"])
        tg_text.grid(row=0, column=0, sticky="w")
        tk.Label(tg_text, text="Notificaciones Telegram", bg=COLORS["panel"], fg=COLORS["text"], font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(tg_text, text="Alerta al finalizar o fallar un proceso", bg=COLORS["panel"], fg=COLORS["muted"], font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w")
        ToggleSwitch(tg_row, variable=self.telegram_enabled, command=self._write_ui_settings,
                     bg=COLORS["panel"], width=34, height=18).grid(row=0, column=1, sticky="ne", pady=(4, 0))

        save_cfg = RoundedButton(
            config_card, text="Guardar configuracion",
            bg=COLORS["primary_container"], hover_bg=COLORS["primary"],
            font=("Segoe UI", 10, "bold"),
            radius=12, padx=18, pady=14,
            parent_bg=COLORS["panel"],
            command=self._save_config_clicked,
        )
        save_cfg.grid(row=9, column=0, sticky="ew", padx=20, pady=(20, 10))

        del_btn = RoundedButton(
            config_card, text="🗑  Eliminar datos historicos",
            bg=COLORS["panel"], fg=COLORS["danger"],
            hover_bg=COLORS["danger_soft"], hover_fg=COLORS["danger"],
            border=COLORS["danger"],
            font=("Segoe UI", 10, "bold"),
            radius=12, padx=18, pady=14,
            parent_bg=COLORS["panel"],
            command=self._delete_historical_data,
        )
        del_btn.grid(row=10, column=0, sticky="ew", padx=20, pady=(0, 22))


    def _build_portfolio(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        panel = self._card(parent, "Portfolio Manager")
        panel.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        panel.columnconfigure(1, weight=1)

        ttk.Label(panel, text="Carpeta de reportes", style="Panel.TLabel").grid(row=1, column=0, sticky="w", padx=(20, 10), pady=7)
        ttk.Entry(panel, textvariable=self.portfolio_input).grid(row=1, column=1, sticky="ew", pady=7)
        ttk.Button(panel, text="Elegir", style="Tool.TButton", command=self._browse_portfolio_input).grid(row=1, column=2, padx=(8, 20), pady=7)

        ttk.Label(panel, textvariable=self.portfolio_count, style="Muted.TLabel").grid(row=2, column=1, sticky="w", pady=(0, 10))
        ttk.Button(panel, text="Recontar", style="Tool.TButton", command=self._refresh_portfolio_count).grid(row=2, column=2, sticky="e", padx=(8, 20), pady=(0, 10))

        ttk.Label(panel, text="Excel de salida", style="Panel.TLabel").grid(row=3, column=0, sticky="w", padx=(20, 10), pady=7)
        ttk.Entry(panel, textvariable=self.portfolio_output).grid(row=3, column=1, sticky="ew", pady=7)
        ttk.Button(panel, text="Guardar como", style="Tool.TButton", command=self._browse_portfolio_output).grid(row=3, column=2, padx=(8, 20), pady=7)

        ttk.Label(panel, text="Umbral DD diario", style="Panel.TLabel").grid(row=4, column=0, sticky="w", padx=(20, 10), pady=7)
        ttk.Entry(panel, textvariable=self.portfolio_threshold, width=12).grid(row=4, column=1, sticky="w", pady=7)
        ttk.Label(panel, text="Ejemplo: 50 para filtrar <= -50", style="Muted.TLabel").grid(row=4, column=1, sticky="w", padx=(110, 0), pady=7)

        actions = self._card(parent, "Generadores Excel")
        actions.grid(row=1, column=0, sticky="nsew")
        for column in range(3):
            actions.columnconfigure(column, weight=1)

        self.portfolio_buttons = []
        specs = [
            ("ALL_STRATEGIES", "Genera el workbook base de estrategias.", "all"),
            ("ALL_STRATEGIES_DD", "Genera hojas de drawdown por estrategia.", "dd"),
            ("PORTFOLIO_DD", "Genera desglose de drawdown del portfolio.", "portfolio_dd"),
            ("DD_VALLE_TOTAL", "Calcula drawdown de valles del portfolio.", "portfolio_valley"),
            ("5 PEORES VALLES", "Exporta los peores valles del portfolio.", "top_valleys"),
            ("FILTRAR DD", "Filtra estrategias por umbral de DD diario.", "threshold"),
        ]
        for index, (title, desc, action) in enumerate(specs):
            row = 1 + index // 3
            column = index % 3
            card = tk.Frame(actions, bg=COLORS["panel_alt"], highlightthickness=1, highlightbackground=COLORS["border"])
            card.grid(row=row, column=column, sticky="nsew", padx=(20 if column == 0 else 8, 20 if column == 2 else 8), pady=(0, 14))
            card.columnconfigure(0, weight=1)
            tk.Label(card, text=title, bg=COLORS["panel_alt"], fg=COLORS["text"], font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 2))
            tk.Label(card, text=desc, bg=COLORS["panel_alt"], fg=COLORS["muted"], font=("Segoe UI", 9), wraplength=250, justify="left").grid(row=1, column=0, sticky="w", padx=12, pady=(0, 10))
            btn = ttk.Button(card, text="Generar", style="Primary.TButton", command=lambda a=action: self._run_portfolio_action(a))
            btn.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
            self.portfolio_buttons.append(btn)

        self.portfolio_progress = ttk.Progressbar(actions, mode="indeterminate")
        self.portfolio_progress.grid(row=3, column=0, columnspan=3, sticky="ew", padx=20, pady=(4, 12))
        status = ttk.Label(actions, textvariable=self.portfolio_status, style="CardDesc.TLabel", wraplength=900, justify="left")
        status.grid(row=4, column=0, columnspan=3, sticky="ew", padx=20, pady=(0, 18))


    def _build_settings(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        canvas = tk.Canvas(parent, bg=COLORS["bg"], highlightthickness=0)
        scroll = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        content = ttk.Frame(canvas, padding=(0, 0, 10, 0))
        content_id = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(content_id, width=event.width))
        content.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        content.columnconfigure(0, weight=1)

        # Mouse wheel scrolling: only active while pointer is over the settings section
        def _on_wheel(event):
            delta = -1 if event.delta > 0 else 1
            if hasattr(event, "num"):
                if event.num == 4:
                    delta = -1
                elif event.num == 5:
                    delta = 1
            canvas.yview_scroll(delta, "units")

        def _bind_wheel(_e=None):
            self.bind_all("<MouseWheel>", _on_wheel)
            self.bind_all("<Button-4>", _on_wheel)
            self.bind_all("<Button-5>", _on_wheel)

        def _unbind_wheel(_e=None):
            self.unbind_all("<MouseWheel>")
            self.unbind_all("<Button-4>")
            self.unbind_all("<Button-5>")

        for widget in (canvas, parent, content):
            widget.bind("<Enter>", _bind_wheel)
            widget.bind("<Leave>", _unbind_wheel)

        paths = self._card(content, "Rutas")
        paths.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        paths.columnconfigure(1, weight=1)
        self._path_row(paths, "Terminal MT5", self.mt5_path, 1, self._browse_file)
        self._path_row(paths, "Carpeta datos MT5", self.mt5_data_root, 2, self._browse_dir)
        self._path_row(paths, "MetaEditor", self.metaeditor_path, 3, self._browse_file)
        self._path_row(paths, "Carpeta .mq5", self.compile_root, 4, self._browse_dir)
        self._path_row(paths, "Archivo .mq5", self.compile_file, 5, self._browse_mq5_file)
        self._path_row(paths, "Carpeta .ex5", self.experts_root, 6, self._browse_dir)
        self._path_row(paths, "Archivo .ex5 UBS", self.ubs_ex5_file, 7, self._browse_ex5_file)
        self._path_row(paths, "Carpeta .set", self.set_files_root, 8, self._browse_dir)
        self._path_row(paths, "Archivo .set UBS", self.ubs_set_file, 9, self._browse_set_file)
        self._path_row(paths, "Template tester", self.template_path, 10, self._browse_template_file)
        RoundedButton(
            paths, text="Guardar rutas",
            bg=COLORS["accent"], hover_bg=COLORS["accent_hover"],
            font=("Segoe UI", 10, "bold"),
            radius=12, padx=18, pady=14,
            parent_bg=COLORS["panel"],
            command=self._save_paths_clicked,
        ).grid(row=11, column=0, columnspan=3, sticky="ew", padx=20, pady=(16, 22))

        tester = self._card(content, "Tester")
        tester.grid(row=1, column=0, sticky="ew")
        for column in (1, 3):
            tester.columnconfigure(column, weight=1)

        fields = [
            ("Symbol", "Simbolo"),
            ("Period", "Timeframe"),
            ("Model", "Modelo"),
            ("FromDate", "Desde"),
            ("ToDate", "Hasta"),
            ("Deposit", "Deposito"),
            ("Currency", "Divisa"),
            ("Leverage", "Apalancamiento"),
            ("Optimization", "Optimizacion"),
            ("Visual", "Visual"),
            ("ReplaceReport", "Reemplazar reporte"),
            ("ShutdownTerminal", "Cerrar MT5"),
        ]
        for index, (key, label) in enumerate(fields):
            row = 1 + index // 2
            col = (index % 2) * 2
            self.tester_vars[key] = tk.StringVar()
            left_pad = 20 if col == 0 else 10
            right_pad = 10 if col == 0 else 20
            ttk.Label(tester, text=label, style="Panel.TLabel").grid(row=row, column=col, sticky="w", padx=(left_pad, 10), pady=7)
            ttk.Entry(tester, textvariable=self.tester_vars[key]).grid(row=row, column=col + 1, sticky="ew", padx=(0, right_pad), pady=7)
        RoundedButton(
            tester, text="Cargar template.ini",
            bg=COLORS["primary"], fg=COLORS["primary_text"],
            hover_bg=COLORS["primary_container"], hover_fg=COLORS["primary_hover_text"],
            font=("Segoe UI", 10, "bold"),
            radius=12, padx=18, pady=12,
            parent_bg=COLORS["panel"],
            command=self._load_template_clicked,
        ).grid(row=7, column=0, columnspan=4, sticky="ew", padx=20, pady=(12, 0))
        RoundedButton(
            tester, text="Guardar tester_template.ini",
            bg=COLORS["accent"], hover_bg=COLORS["accent_hover"],
            font=("Segoe UI", 10, "bold"),
            radius=12, padx=18, pady=14,
            parent_bg=COLORS["panel"],
            command=self._save_template_clicked,
        ).grid(row=8, column=0, columnspan=4, sticky="ew", padx=20, pady=(10, 22))

    def _build_multiterminal_inline(self, parent: ttk.Frame) -> None:
        bar = tk.Frame(parent, bg=COLORS["panel_alt"], highlightthickness=1, highlightbackground=COLORS["border"])
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=20, pady=(4, 14))
        bar.columnconfigure(1, weight=1)
        tk.Label(bar, text="Multiterminal", bg=COLORS["panel_alt"], fg=COLORS["text"],
                 font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", padx=(12, 10), pady=8)
        tk.Label(bar, textvariable=self.multiterminal_summary, bg=COLORS["panel_alt"], fg=COLORS["muted"],
                 font=("Segoe UI", 9)).grid(row=0, column=1, sticky="w", padx=(0, 8), pady=8)
        ToggleSwitch(
            bar,
            variable=self.multiterminal_enabled,
            command=self._on_multiterminal_changed,
            bg=COLORS["panel_alt"],
            width=34,
            height=18,
        ).grid(row=0, column=2, sticky="e", padx=(6, 8), pady=8)
        worker_spin = ttk.Spinbox(
            bar,
            from_=1,
            to=32,
            width=5,
            textvariable=self.multiterminal_workers,
            command=self._on_multiterminal_changed,
        )
        worker_spin.grid(row=0, column=3, sticky="e", padx=(0, 8), pady=8)
        worker_spin.bind("<FocusOut>", lambda _event: self._on_multiterminal_changed())
        worker_spin.bind("<Return>", lambda _event: self._on_multiterminal_changed())
        ttk.Button(
            bar,
            text="Configurar",
            style="Tool.TButton",
            command=lambda: self._show_section("multiterminal"),
        ).grid(row=0, column=4, sticky="e", padx=(0, 12), pady=8)

    def _build_multiterminal(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        panel = self._card(parent, "Multiterminales MT5")
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=7)
        panel.columnconfigure(1, weight=5)
        panel.rowconfigure(2, weight=1)

        top = tk.Frame(panel, bg=COLORS["panel_alt"])
        top.grid(row=1, column=0, columnspan=2, sticky="ew", padx=20, pady=(4, 12))
        top.columnconfigure(1, weight=1)
        tk.Label(top, text="Modo multiterminal", bg=COLORS["panel_alt"], fg=COLORS["text"],
                 font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", padx=(10, 10), pady=8)
        tk.Label(top, textvariable=self.multiterminal_summary, bg=COLORS["panel_alt"], fg=COLORS["muted"],
                 font=("Segoe UI", 9)).grid(row=0, column=1, sticky="w", padx=(0, 10), pady=8)
        ToggleSwitch(
            top,
            variable=self.multiterminal_enabled,
            command=self._on_multiterminal_changed,
            bg=COLORS["panel_alt"],
            width=34,
            height=18,
        ).grid(row=0, column=2, sticky="e", padx=(0, 8), pady=8)
        ttk.Label(top, text="Terminales a usar", style="MutedBg.TLabel").grid(row=0, column=3, sticky="e", padx=(8, 6), pady=8)
        worker_spin = ttk.Spinbox(
            top,
            from_=1,
            to=32,
            width=6,
            textvariable=self.multiterminal_workers,
            command=self._on_multiterminal_changed,
        )
        worker_spin.grid(row=0, column=4, sticky="e", padx=(0, 8), pady=8)
        worker_spin.bind("<FocusOut>", lambda _event: self._on_multiterminal_changed())
        worker_spin.bind("<Return>", lambda _event: self._on_multiterminal_changed())
        ttk.Button(top, text="Validar", style="Tool.TButton", command=self._validate_multiterminal_profiles).grid(
            row=0, column=5, sticky="e", padx=(0, 6), pady=8
        )
        ttk.Button(top, text="Guardar", style="Primary.TButton", command=self._save_multiterminal_clicked).grid(
            row=0, column=6, sticky="e", padx=(0, 10), pady=8
        )

        table_frame = ttk.Frame(panel, style="Panel.TFrame")
        table_frame.grid(row=2, column=0, sticky="nsew", padx=(20, 12), pady=(0, 10))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("enabled", "name", "mt5_path", "data_dir", "experts_root", "ubs_ex5_file", "portable")
        self.multiterminal_tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=14)
        headings = {
            "enabled": "ON",
            "name": "NOMBRE",
            "mt5_path": "TERMINAL64.EXE",
            "data_dir": "DATOS MT5",
            "experts_root": "MQL5\\EXPERTS",
            "ubs_ex5_file": "UBS .EX5",
            "portable": "PORTABLE",
        }
        widths = {
            "enabled": 58,
            "name": 150,
            "mt5_path": 260,
            "data_dir": 260,
            "experts_root": 240,
            "ubs_ex5_file": 220,
            "portable": 80,
        }
        for column in columns:
            self.multiterminal_tree.heading(column, text=headings[column])
            self.multiterminal_tree.column(column, width=widths[column], minwidth=50, stretch=column not in {"enabled", "portable"})
        self._attach_tree_scrollbars(table_frame, self.multiterminal_tree, 0)
        self._make_tree_sortable(self.multiterminal_tree)
        self.multiterminal_tree.bind("<<TreeviewSelect>>", self._on_multiterminal_tree_select)

        table_buttons = ttk.Frame(panel, style="Panel.TFrame")
        table_buttons.grid(row=3, column=0, sticky="ew", padx=(20, 12), pady=(0, 18))
        for column in range(5):
            table_buttons.columnconfigure(column, weight=1)
        ttk.Button(table_buttons, text="Anadir", style="Tool.TButton", command=self._add_multiterminal_profile).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(table_buttons, text="Duplicar", style="Tool.TButton", command=self._duplicate_multiterminal_profile).grid(row=0, column=1, sticky="ew", padx=(0, 6))
        ttk.Button(table_buttons, text="Eliminar", style="DangerOutline.TButton", command=self._delete_multiterminal_profile).grid(row=0, column=2, sticky="ew", padx=(0, 6))
        ttk.Button(table_buttons, text="Validar", style="Tool.TButton", command=self._validate_multiterminal_profiles).grid(row=0, column=3, sticky="ew", padx=(0, 6))
        ttk.Button(table_buttons, text="Guardar", style="Primary.TButton", command=self._save_multiterminal_clicked).grid(row=0, column=4, sticky="ew")

        editor = tk.Frame(panel, bg=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["border"])
        editor.grid(row=2, column=1, rowspan=2, sticky="nsew", padx=(0, 20), pady=(0, 18))
        editor.columnconfigure(1, weight=1)
        tk.Label(editor, text="Editor de terminal", bg=COLORS["panel"], fg=COLORS["text"],
                 font=("Segoe UI", 11, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", padx=16, pady=(14, 8))
        state_row = tk.Frame(editor, bg=COLORS["panel"])
        state_row.grid(row=1, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 8))
        state_row.columnconfigure(0, weight=1)
        ttk.Checkbutton(state_row, text="Habilitada", variable=self.mt_profile_enabled, style="Panel.TCheckbutton").grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(state_row, text="Portable", variable=self.mt_profile_portable, style="Panel.TCheckbutton").grid(row=0, column=1, sticky="e")
        ttk.Label(editor, text="Nombre", style="Panel.TLabel").grid(row=2, column=0, sticky="w", padx=(16, 10), pady=7)
        ttk.Entry(editor, textvariable=self.mt_profile_name).grid(row=2, column=1, columnspan=2, sticky="ew", padx=(0, 16), pady=7)
        self._path_row(editor, "Terminal MT5", self.mt_profile_mt5_path, 3, self._browse_file)
        self._path_row(editor, "Carpeta datos MT5", self.mt_profile_data_dir, 4, self._browse_dir)
        self._path_row(editor, "MQL5\\Experts", self.mt_profile_experts_root, 5, self._browse_dir)
        self._path_row(editor, "Archivo UBS .ex5", self.mt_profile_ubs_ex5_file, 6, self._browse_profile_ex5_file)
        ttk.Button(editor, text="Aplicar fila", style="Primary.TButton", command=self._apply_multiterminal_editor).grid(
            row=7, column=0, columnspan=3, sticky="ew", padx=16, pady=(12, 8)
        )
        ttk.Label(
            editor,
            text="La cantidad es un limite: se usan hasta N terminales habilitadas. Compilar sigue siendo secuencial.",
            style="Muted.TLabel",
            wraplength=420,
            justify="left",
        ).grid(row=8, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 14))

        self._refresh_multiterminal_tree()
        if self.multiterminal_profiles:
            self._select_multiterminal_profile(0)
        self._update_multiterminal_summary()

    def _build_ubs_multiterminal_row(self, parent: ttk.Frame, *, row: int) -> None:
        mt_row = tk.Frame(parent, bg=COLORS["panel_alt"], highlightthickness=1, highlightbackground=COLORS["border"])
        mt_row.grid(row=row, column=0, columnspan=6, sticky="ew", padx=20, pady=(10, 0))
        mt_row.columnconfigure(1, weight=1)
        tk.Label(
            mt_row,
            text="Multiterminal",
            bg=COLORS["panel_alt"],
            fg=COLORS["text"],
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=(12, 10), pady=8)
        tk.Label(
            mt_row,
            textvariable=self.multiterminal_summary,
            bg=COLORS["panel_alt"],
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=1, sticky="w", padx=(0, 10), pady=8)
        ToggleSwitch(
            mt_row,
            variable=self.multiterminal_enabled,
            command=self._on_multiterminal_changed,
            bg=COLORS["panel_alt"],
            width=34,
            height=18,
        ).grid(row=0, column=2, sticky="e", padx=(0, 8), pady=8)
        ttk.Label(mt_row, text="Terminales a usar", style="MutedBg.TLabel").grid(row=0, column=3, sticky="e", padx=(0, 6), pady=8)
        worker_spin = ttk.Spinbox(
            mt_row,
            from_=1,
            to=32,
            width=6,
            textvariable=self.multiterminal_workers,
            command=self._on_multiterminal_changed,
        )
        worker_spin.grid(row=0, column=4, sticky="e", padx=(0, 8), pady=8)
        worker_spin.bind("<FocusOut>", lambda _event: self._on_multiterminal_changed())
        worker_spin.bind("<Return>", lambda _event: self._on_multiterminal_changed())
        ttk.Button(
            mt_row,
            text="Configurar",
            style="Tool.TButton",
            command=lambda: self._show_section("multiterminal"),
        ).grid(row=0, column=5, sticky="e", padx=(0, 12), pady=8)

    def _build_ubs_agent(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)

        paths = self._card(parent, "Rutas Agente UBS")
        paths.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        paths.columnconfigure(1, weight=1)
        self._path_row(paths, "Archivo .ex5 UBS", self.ubs_ex5_file, 1, self._browse_ex5_file)
        self._path_row(paths, "Carpeta seeds UBS", self.set_files_root, 2, self._browse_dir)
        self._path_row(paths, "Salida Agente UBS", self.ubs_generation_output, 3, self._browse_dir)
        seed_eval_row = ttk.Frame(paths, style="Panel.TFrame")
        seed_eval_row.grid(row=4, column=0, columnspan=3, sticky="ew", padx=20, pady=(10, 18))
        seed_eval_row.columnconfigure(0, weight=1)
        ttk.Label(seed_eval_row, textvariable=self.ubs_seed_eval_summary, style="Muted.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 10)
        )
        ttk.Button(
            seed_eval_row,
            text="Evaluar semillas",
            style="Primary.TButton",
            command=self._run_ubs_seed_evaluation,
        ).grid(row=0, column=1, sticky="e")

        agent = self._card(parent, "Configuracion Agente UBS")
        agent.grid(row=1, column=0, sticky="ew")
        for column in (1, 3, 5):
            agent.columnconfigure(column, weight=1)

        gen_fields = [
            ("Generaciones", self.ubs_generation_count, 1, 100),
            ("Variantes por set", self.ubs_variants_per_seed, 1, 100),
            ("Max seeds/gen", self.ubs_max_seeds, 0, 5000),
        ]
        for index, (label, variable, from_value, to_value) in enumerate(gen_fields):
            column = index * 2
            left_pad = 20 if index == 0 else 10
            right_pad = 10 if index < len(gen_fields) - 1 else 20
            ttk.Label(agent, text=label, style="Panel.TLabel").grid(
                row=1, column=column, sticky="w", padx=(left_pad, 10), pady=7
            )
            ttk.Spinbox(agent, from_=from_value, to=to_value, textvariable=variable, width=10).grid(
                row=1, column=column + 1, sticky="ew", padx=(0, right_pad), pady=7
            )

        exec_row = tk.Frame(agent, bg=COLORS["panel"])
        exec_row.grid(row=2, column=0, columnspan=6, sticky="ew", padx=20, pady=(12, 6))
        exec_row.columnconfigure(0, weight=1)
        exec_text = tk.Frame(exec_row, bg=COLORS["panel"])
        exec_text.grid(row=0, column=0, sticky="w")
        tk.Label(exec_text, text="Ejecutar backtests", bg=COLORS["panel"], fg=COLORS["text"], font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(exec_text, text="Activa feedback real; apagado solo genera variantes.", bg=COLORS["panel"], fg=COLORS["muted"], font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w")
        ToggleSwitch(exec_row, variable=self.ubs_agent_execute, bg=COLORS["panel"], width=34, height=18).grid(row=0, column=1, sticky="ne", pady=(4, 0))

        self._build_ubs_multiterminal_row(agent, row=3)

        buttons = ttk.Frame(agent, style="Panel.TFrame")
        buttons.grid(row=4, column=0, columnspan=6, sticky="ew", padx=20, pady=(14, 22))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        buttons.columnconfigure(2, weight=1)
        RoundedButton(
            buttons, text="Guardar configuracion Agente UBS",
            bg=COLORS["primary_container"], hover_bg=COLORS["primary"],
            font=("Segoe UI", 10, "bold"),
            radius=12, padx=18, pady=14,
            parent_bg=COLORS["panel"],
            command=self._save_ubs_agent_clicked,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        RoundedButton(
            buttons, text="Lanzar Agente UBS",
            bg=COLORS["accent"], hover_bg=COLORS["accent_hover"],
            font=("Segoe UI", 10, "bold"),
            radius=12, padx=18, pady=14,
            parent_bg=COLORS["panel"],
            command=self._run_ubs_generator,
        ).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.ubs_continue_button = RoundedButton(
            buttons, text="Continuar iteracion",
            bg=COLORS["primary"], fg=COLORS["primary_text"],
            hover_bg=COLORS["primary_container"], hover_fg=COLORS["primary_hover_text"],
            font=("Segoe UI", 10, "bold"),
            radius=12, padx=18, pady=14,
            parent_bg=COLORS["panel"],
            command=self._run_ubs_continue,
        )
        self.ubs_continue_button.grid(row=0, column=2, sticky="ew", padx=(8, 0))
        ttk.Label(agent, textvariable=self.ubs_continue_status, style="Muted.TLabel").grid(
            row=5, column=0, columnspan=6, sticky="w", padx=20, pady=(0, 14)
        )

        pass_config = self._card(parent, "Filtros de aceptacion")
        pass_config.grid(row=2, column=0, sticky="ew", pady=(16, 0))
        for column in (1, 3, 5):
            pass_config.columnconfigure(column, weight=1)
        pass_fields = [
            ("Profit neto min", self.ubs_pass_min_net_profit, "entry"),
            ("Profit factor min", self.ubs_pass_min_profit_factor, "entry"),
            ("Trades min", self.ubs_pass_min_trades, "spin"),
            ("DD max %", self.ubs_pass_max_drawdown_pct, "entry"),
            ("Recovery min", self.ubs_pass_min_recovery_factor, "entry"),
        ]
        for index, (label, variable, kind) in enumerate(pass_fields):
            row = 1 + index // 3
            column = (index % 3) * 2
            left_pad = 20 if column == 0 else 10
            right_pad = 10 if column < 4 else 20
            ttk.Label(pass_config, text=label, style="Panel.TLabel").grid(
                row=row, column=column, sticky="w", padx=(left_pad, 10), pady=7
            )
            if kind == "spin":
                ttk.Spinbox(pass_config, from_=0, to=100000, textvariable=variable, width=10).grid(
                    row=row, column=column + 1, sticky="ew", padx=(0, right_pad), pady=7
                )
            else:
                ttk.Entry(pass_config, textvariable=variable).grid(
                    row=row, column=column + 1, sticky="ew", padx=(0, right_pad), pady=7
                )
        ttk.Label(
            pass_config,
            text="Profit neto min es moneda de la cuenta. Con deposito 1000, default 100 = 10%. Estabilidad mensual: score, no filtro hard.",
            style="Muted.TLabel",
        ).grid(row=3, column=0, columnspan=6, sticky="w", padx=20, pady=(4, 18))

    def _build_ubs_seeds(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        card = self._card(parent, "Semillas UBS")
        card.grid(row=0, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(2, weight=1)

        toolbar = ttk.Frame(card, style="Panel.TFrame")
        toolbar.grid(row=1, column=0, sticky="ew", padx=20, pady=(10, 8))
        toolbar.columnconfigure(0, weight=1)
        ttk.Label(toolbar, textvariable=self.ubs_seed_eval_summary, style="Muted.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 10)
        )
        ttk.Button(toolbar, text="Evaluar semillas", style="Primary.TButton", command=self._run_ubs_seed_evaluation).grid(
            row=0, column=1, sticky="e", padx=(0, 8)
        )
        ttk.Button(toolbar, text="Abrir seed", style="TButton", command=self._open_selected_ubs_seed).grid(
            row=0, column=2, sticky="e", padx=(0, 8)
        )
        ttk.Button(toolbar, text="Guardar Symbol/TF", style="TButton", command=self._save_ubs_seed_override).grid(
            row=0, column=3, sticky="e", padx=(0, 8)
        )
        ttk.Button(toolbar, text="Actualizar", style="TButton", command=self._refresh_ubs_seeds).grid(
            row=0, column=4, sticky="e"
        )

        table_frame = ttk.Frame(card, style="Panel.TFrame")
        table_frame.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 10))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("status", "symbol", "period", "score", "accepted", "override", "seed")
        self.ubs_seeds_tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "status": "ESTADO",
            "symbol": "SYMBOL",
            "period": "TF",
            "score": "SCORE",
            "accepted": "OK",
            "override": "OVERRIDE",
            "seed": "SEED",
        }
        widths = {"status": 125, "symbol": 90, "period": 60, "score": 90, "accepted": 70, "override": 90, "seed": 720}
        for column in columns:
            self.ubs_seeds_tree.heading(column, text=headings[column])
            anchor = "e" if column == "score" else "center" if column in {"period", "accepted", "override"} else "w"
            self.ubs_seeds_tree.column(column, width=widths[column], minwidth=50, stretch=column == "seed", anchor=anchor)
        self._make_tree_sortable(self.ubs_seeds_tree)
        self._attach_tree_scrollbars(table_frame, self.ubs_seeds_tree, 0)
        self.ubs_seeds_tree.tag_configure("accepted", foreground=COLORS["accent_soft_text"])
        self.ubs_seeds_tree.tag_configure("rejected", foreground=COLORS["danger"])
        self.ubs_seeds_tree.tag_configure("pending", foreground=COLORS["muted"])
        self.ubs_seeds_tree.bind("<<TreeviewSelect>>", lambda _event: self._on_ubs_seed_select())

        editor = ttk.Frame(card, style="Panel.TFrame")
        editor.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 18))
        editor.columnconfigure(1, weight=1)
        editor.columnconfigure(3, weight=1)
        ttk.Label(editor, textvariable=self.ubs_seed_detail, style="Muted.TLabel").grid(
            row=0, column=0, columnspan=5, sticky="ew", pady=(0, 8)
        )
        ttk.Label(editor, text="Symbol correcto", style="Panel.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(editor, textvariable=self.ubs_seed_override_symbol, width=18).grid(
            row=1, column=1, sticky="ew", padx=(0, 16)
        )
        ttk.Label(editor, text="Timeframe correcto", style="Panel.TLabel").grid(row=1, column=2, sticky="w", padx=(0, 8))
        ttk.Combobox(
            editor,
            textvariable=self.ubs_seed_override_period,
            values=("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"),
            width=10,
            state="readonly",
        ).grid(row=1, column=3, sticky="w", padx=(0, 16))
        ttk.Button(editor, text="Guardar override", style="Primary.TButton", command=self._save_ubs_seed_override).grid(
            row=1, column=4, sticky="e"
        )

    def _build_ubs_results(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        results = self._card(parent, "Resultados Agente UBS")
        results.grid(row=0, column=0, sticky="nsew")
        results.columnconfigure(0, weight=1)
        results.rowconfigure(3, weight=1)

        results_bar = tk.Frame(results, bg=COLORS["panel_alt"])
        results_bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 8))
        results_bar.columnconfigure(0, weight=1)
        tk.Label(
            results_bar,
            textvariable=self.ubs_results_summary,
            bg=COLORS["panel_alt"],
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=10, pady=6)
        tk.Button(
            results_bar,
            text="Abrir output",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=4,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._open_ubs_output_dir,
        ).grid(row=0, column=1, sticky="e", padx=(0, 6), pady=4)
        tk.Button(
            results_bar,
            text="Abrir set",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=4,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._open_selected_ubs_set,
        ).grid(row=0, column=2, sticky="e", padx=(0, 6), pady=4)
        tk.Button(
            results_bar,
            text="Abrir reporte",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=4,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._open_selected_ubs_report,
        ).grid(row=0, column=3, sticky="e", padx=(0, 6), pady=4)
        tk.Button(
            results_bar,
            text="Reprobar mismatch",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=4,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._retry_selected_ubs_mismatch,
        ).grid(row=0, column=4, sticky="e", padx=(0, 6), pady=4)
        tk.Button(
            results_bar,
            text="Reprobar run",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=4,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._retry_visible_ubs_run_mismatches,
        ).grid(row=0, column=5, sticky="e", padx=(0, 6), pady=4)
        tk.Button(
            results_bar,
            text="Limpiar vista",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=4,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._hide_latest_ubs_results,
        ).grid(row=0, column=6, sticky="e", padx=(0, 6), pady=4)
        tk.Button(
            results_bar,
            text="Actualizar",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=4,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._refresh_ubs_results,
        ).grid(row=0, column=7, sticky="e", padx=(0, 10), pady=4)

        ttk.Label(results, textvariable=self.ubs_results_status, style="Muted.TLabel").grid(
            row=2, column=0, sticky="w", padx=20, pady=(0, 6)
        )

        table_frame = ttk.Frame(results, style="Panel.TFrame")
        table_frame.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 18))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("run", "gen", "status", "symbol", "period", "score", "profit", "pf", "dd", "trades", "set")
        self.ubs_results_tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=10)
        headings = {
            "run": "RUN",
            "gen": "GEN",
            "status": "ESTADO",
            "symbol": "SYMBOL",
            "period": "TF",
            "score": "SCORE",
            "profit": "NET",
            "pf": "PF",
            "dd": "DD %",
            "trades": "TRADES",
            "set": "SET",
        }
        widths = {
            "run": 56,
            "gen": 50,
            "status": 86,
            "symbol": 96,
            "period": 58,
            "score": 82,
            "profit": 90,
            "pf": 72,
            "dd": 72,
            "trades": 74,
            "set": 240,
        }
        anchors = {"score": "e", "profit": "e", "pf": "e", "dd": "e", "trades": "e"}
        for column in columns:
            self.ubs_results_tree.heading(column, text=headings[column])
            self.ubs_results_tree.column(
                column,
                width=widths[column],
                minwidth=42,
                anchor=anchors.get(column, "w"),
                stretch=False,
            )
        self.ubs_results_tree.tag_configure("accepted", foreground=COLORS["accent_soft_text"])
        self.ubs_results_tree.tag_configure("rejected", foreground=COLORS["danger"])
        self.ubs_results_tree.tag_configure("pending", foreground=COLORS["muted"])
        self._make_tree_sortable(self.ubs_results_tree)
        self.ubs_results_tree.bind("<Double-1>", lambda _event: self._open_selected_ubs_report())
        self._attach_tree_scrollbars(table_frame, self.ubs_results_tree, 0)

    def _build_ubs_history(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        panel = self._card(parent, "Historico SQLite UBS")
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(3, weight=1)

        bar = tk.Frame(panel, bg=COLORS["panel_alt"])
        bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 8))
        bar.columnconfigure(0, weight=1)
        tk.Label(bar, textvariable=self.ubs_history_summary, bg=COLORS["panel_alt"], fg=COLORS["muted"], font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", padx=10, pady=6)
        tk.Button(
            bar, text="Actualizar", bg=COLORS["panel"], fg=COLORS["muted"],
            relief="solid", borderwidth=1, padx=10, pady=4, font=("Segoe UI", 9),
            cursor="hand2", command=self._refresh_ubs_history,
        ).grid(row=0, column=1, sticky="e", padx=(0, 10), pady=4)

        runs_frame = ttk.Frame(panel, style="Panel.TFrame")
        runs_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 12))
        runs_frame.columnconfigure(0, weight=1)
        run_columns = ("id", "created", "gens", "variants", "seeds", "backtests", "hidden", "total", "accepted", "rejected", "output")
        self.ubs_history_runs_tree = ttk.Treeview(runs_frame, columns=run_columns, show="headings", height=6)
        run_headings = {
            "id": "RUN", "created": "FECHA", "gens": "GENS", "variants": "VAR/SET",
            "seeds": "SEEDS", "backtests": "BT", "hidden": "ARCH", "total": "TOTAL",
            "accepted": "OK", "rejected": "BAD", "output": "OUTPUT",
        }
        run_widths = {"id": 56, "created": 150, "gens": 54, "variants": 70, "seeds": 70, "backtests": 50, "hidden": 55, "total": 70, "accepted": 55, "rejected": 55, "output": 380}
        for column in run_columns:
            self.ubs_history_runs_tree.heading(column, text=run_headings[column])
            self.ubs_history_runs_tree.column(column, width=run_widths[column], anchor="e" if column in {"id", "gens", "variants", "seeds", "total", "accepted", "rejected"} else "w", stretch=False)
        self._make_tree_sortable(self.ubs_history_runs_tree)
        self._attach_tree_scrollbars(runs_frame, self.ubs_history_runs_tree, 0, vertical=False)
        self.ubs_history_runs_tree.bind("<<TreeviewSelect>>", lambda _event: self._refresh_ubs_history_candidates())

        candidates_panel = ttk.Frame(panel, style="Panel.TFrame")
        candidates_panel.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 18))
        candidates_panel.columnconfigure(0, weight=1)
        candidates_panel.rowconfigure(1, weight=1)
        ttk.Label(candidates_panel, textvariable=self.ubs_history_candidate_summary, style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        cand_columns = ("id", "gen", "status", "symbol", "period", "score", "profit", "pf", "dd", "trades", "set")
        self.ubs_history_candidates_tree = ttk.Treeview(candidates_panel, columns=cand_columns, show="headings", height=12)
        cand_headings = {"id": "ID", "gen": "GEN", "status": "ESTADO", "symbol": "SYMBOL", "period": "TF", "score": "SCORE", "profit": "NET", "pf": "PF", "dd": "DD %", "trades": "TRADES", "set": "SET"}
        cand_widths = {"id": 60, "gen": 50, "status": 86, "symbol": 96, "period": 58, "score": 82, "profit": 90, "pf": 72, "dd": 72, "trades": 74, "set": 240}
        for column in cand_columns:
            self.ubs_history_candidates_tree.heading(column, text=cand_headings[column])
            self.ubs_history_candidates_tree.column(column, width=cand_widths[column], anchor="e" if column in {"id", "gen", "score", "profit", "pf", "dd", "trades"} else "w", stretch=False)
        self.ubs_history_candidates_tree.tag_configure("accepted", foreground=COLORS["accent_soft_text"])
        self.ubs_history_candidates_tree.tag_configure("rejected", foreground=COLORS["danger"])
        self.ubs_history_candidates_tree.tag_configure("pending", foreground=COLORS["muted"])
        self._make_tree_sortable(self.ubs_history_candidates_tree)
        self._attach_tree_scrollbars(candidates_panel, self.ubs_history_candidates_tree, 1)

    def _build_ubs_comparison(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        panel = self._card(parent, "Comparar resultados contra seed")
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(2, weight=1)

        bar = tk.Frame(panel, bg=COLORS["panel_alt"])
        bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 8))
        bar.columnconfigure(0, weight=1)
        tk.Label(bar, textvariable=self.ubs_compare_summary, bg=COLORS["panel_alt"], fg=COLORS["muted"], font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", padx=10, pady=6)
        ttk.Label(bar, text="Run", style="MutedBg.TLabel").grid(row=0, column=1, sticky="e", padx=(0, 6), pady=4)
        self.ubs_compare_run_combo = ttk.Combobox(
            bar,
            textvariable=self.ubs_compare_run_id,
            state="readonly",
            width=12,
        )
        self.ubs_compare_run_combo.grid(row=0, column=2, sticky="e", padx=(0, 6), pady=4)
        self.ubs_compare_run_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_ubs_comparison())
        tk.Button(
            bar, text="Abrir seed", bg=COLORS["panel"], fg=COLORS["muted"],
            relief="solid", borderwidth=1, padx=10, pady=4, font=("Segoe UI", 9),
            cursor="hand2", command=self._open_selected_ubs_compare_seed,
        ).grid(row=0, column=3, sticky="e", padx=(0, 6), pady=4)
        tk.Button(
            bar, text="Abrir set", bg=COLORS["panel"], fg=COLORS["muted"],
            relief="solid", borderwidth=1, padx=10, pady=4, font=("Segoe UI", 9),
            cursor="hand2", command=self._open_selected_ubs_compare_set,
        ).grid(row=0, column=4, sticky="e", padx=(0, 6), pady=4)
        tk.Button(
            bar, text="Reporte completo", bg=COLORS["panel"], fg=COLORS["muted"],
            relief="solid", borderwidth=1, padx=10, pady=4, font=("Segoe UI", 9),
            cursor="hand2", command=self._generate_ubs_compare_report,
        ).grid(row=0, column=5, sticky="e", padx=(0, 10), pady=4)
        tk.Button(
            bar, text="Actualizar", bg=COLORS["panel"], fg=COLORS["muted"],
            relief="solid", borderwidth=1, padx=10, pady=4, font=("Segoe UI", 9),
            cursor="hand2", command=self._refresh_ubs_comparison,
        ).grid(row=0, column=6, sticky="e", padx=(0, 10), pady=4)

        body = ttk.Frame(panel, style="Panel.TFrame")
        body.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 18))
        body.columnconfigure(0, weight=2)
        body.columnconfigure(1, weight=3)
        body.rowconfigure(0, weight=1)

        accepted_frame = ttk.Frame(body, style="Panel.TFrame")
        accepted_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        accepted_frame.columnconfigure(0, weight=1)
        accepted_frame.rowconfigure(1, weight=1)
        ttk.Label(accepted_frame, text="Resultados", style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        accepted_columns = ("run", "gen", "status", "symbol", "period", "score", "profit", "pf", "dd", "set")
        self.ubs_compare_sets_tree = ttk.Treeview(accepted_frame, columns=accepted_columns, show="headings", height=18)
        accepted_headings = {"run": "RUN", "gen": "GEN", "status": "ESTADO", "symbol": "SYMBOL", "period": "TF", "score": "SCORE", "profit": "NET", "pf": "PF", "dd": "DD %", "set": "SET"}
        accepted_widths = {"run": 50, "gen": 44, "status": 82, "symbol": 82, "period": 46, "score": 72, "profit": 82, "pf": 58, "dd": 62, "set": 220}
        for column in accepted_columns:
            self.ubs_compare_sets_tree.heading(column, text=accepted_headings[column])
            self.ubs_compare_sets_tree.column(column, width=accepted_widths[column], anchor="e" if column in {"run", "gen", "score", "profit", "pf", "dd"} else "w", stretch=False)
        self.ubs_compare_sets_tree.tag_configure("accepted", foreground=COLORS["accent_soft_text"])
        self.ubs_compare_sets_tree.tag_configure("rejected", foreground=COLORS["danger"])
        self._make_tree_sortable(self.ubs_compare_sets_tree)
        self.ubs_compare_sets_tree.bind("<<TreeviewSelect>>", lambda _event: self._refresh_ubs_comparison_diff())
        self._attach_tree_scrollbars(accepted_frame, self.ubs_compare_sets_tree, 1)

        diff_panel = ttk.Frame(body, style="Panel.TFrame")
        diff_panel.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        diff_panel.columnconfigure(0, weight=1)
        diff_panel.rowconfigure(1, weight=1)
        ttk.Label(diff_panel, textvariable=self.ubs_compare_detail, style="Muted.TLabel", wraplength=760).grid(row=0, column=0, sticky="ew", pady=(0, 6))
        diff_columns = ("key", "seed", "accepted")
        self.ubs_compare_diff_tree = ttk.Treeview(diff_panel, columns=diff_columns, show="headings", height=18)
        for column, heading, width in (("key", "PARAMETRO", 210), ("seed", "SEED", 240), ("accepted", "ACEPTADO", 240)):
            self.ubs_compare_diff_tree.heading(column, text=heading)
            self.ubs_compare_diff_tree.column(column, width=width, anchor="w", stretch=False)
        self._make_tree_sortable(self.ubs_compare_diff_tree)
        self._attach_tree_scrollbars(diff_panel, self.ubs_compare_diff_tree, 1)

    def _build_ubs_universe(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        panel = self._card(parent, "Universo, scores y pesos UBS")
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(2, weight=1)

        bar = tk.Frame(panel, bg=COLORS["panel_alt"])
        bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 8))
        bar.columnconfigure(0, weight=1)
        tk.Label(
            bar,
            textvariable=self.ubs_universe_summary,
            bg=COLORS["panel_alt"],
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=10, pady=6)
        tk.Button(
            bar,
            text="Actualizar",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=4,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._refresh_ubs_universe,
        ).grid(row=0, column=1, sticky="e", padx=(0, 10), pady=4)

        body = ttk.Frame(panel, style="Panel.TFrame")
        body.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 18))
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        asset_frame = ttk.Frame(body, style="Panel.TFrame")
        asset_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        asset_frame.columnconfigure(0, weight=1)
        asset_frame.rowconfigure(1, weight=1)
        ttk.Label(asset_frame, text="Activos RoboForex", style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        asset_columns = ("group", "symbol", "aliases", "weight", "avg", "best", "tests", "accepted", "pending")
        self.ubs_universe_assets_tree = ttk.Treeview(asset_frame, columns=asset_columns, show="headings", height=18)
        asset_headings = {
            "group": "GRUPO",
            "symbol": "ACTIVO",
            "aliases": "ALIAS",
            "weight": "PESO",
            "avg": "AVG",
            "best": "BEST",
            "tests": "TESTS",
            "accepted": "OK",
            "pending": "PEND",
        }
        asset_widths = {"group": 110, "symbol": 110, "aliases": 150, "weight": 80, "avg": 80, "best": 80, "tests": 62, "accepted": 54, "pending": 58}
        for column in asset_columns:
            self.ubs_universe_assets_tree.heading(column, text=asset_headings[column])
            self.ubs_universe_assets_tree.column(column, width=asset_widths[column], anchor="e" if column in {"weight", "avg", "best", "tests", "accepted", "pending"} else "w", stretch=False)
        self.ubs_universe_assets_tree.tag_configure("positive", foreground=COLORS["accent_soft_text"])
        self.ubs_universe_assets_tree.tag_configure("negative", foreground=COLORS["danger"])
        self.ubs_universe_assets_tree.tag_configure("neutral", foreground=COLORS["muted"])
        self._make_tree_sortable(self.ubs_universe_assets_tree)
        self._attach_tree_scrollbars(asset_frame, self.ubs_universe_assets_tree, 1)

        tf_frame = ttk.Frame(body, style="Panel.TFrame")
        tf_frame.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        tf_frame.columnconfigure(0, weight=1)
        tf_frame.rowconfigure(2, weight=1)
        ttk.Label(tf_frame, text="Timeframes", style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        ttk.Label(tf_frame, textvariable=self.ubs_timeframe_summary, style="Muted.TLabel", wraplength=520).grid(row=1, column=0, sticky="ew", pady=(0, 6))
        tf_columns = ("period", "weight", "avg", "best", "tests", "accepted", "pending")
        self.ubs_timeframes_tree = ttk.Treeview(tf_frame, columns=tf_columns, show="headings", height=18)
        tf_headings = {"period": "TF", "weight": "PESO", "avg": "AVG", "best": "BEST", "tests": "TESTS", "accepted": "OK", "pending": "PEND"}
        tf_widths = {"period": 70, "weight": 88, "avg": 88, "best": 88, "tests": 65, "accepted": 55, "pending": 60}
        for column in tf_columns:
            self.ubs_timeframes_tree.heading(column, text=tf_headings[column])
            self.ubs_timeframes_tree.column(column, width=tf_widths[column], anchor="e" if column != "period" else "w", stretch=False)
        self.ubs_timeframes_tree.tag_configure("positive", foreground=COLORS["accent_soft_text"])
        self.ubs_timeframes_tree.tag_configure("negative", foreground=COLORS["danger"])
        self.ubs_timeframes_tree.tag_configure("neutral", foreground=COLORS["muted"])
        self._make_tree_sortable(self.ubs_timeframes_tree)
        self._attach_tree_scrollbars(tf_frame, self.ubs_timeframes_tree, 2)

    def _build_files(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        experts_panel = self._card(parent, "Expert Advisors detectados")
        experts_panel.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        experts_panel.columnconfigure(0, weight=1)
        self.experts_tree = ttk.Treeview(experts_panel, columns=("name",), show="headings", height=6)
        self.experts_tree.heading("name", text="ARCHIVO")
        self.experts_tree.tag_configure("odd", background=COLORS["tree_odd"])
        self.experts_tree.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 18))

        reports_panel = self._card(parent, "Generated Reports")
        reports_panel.grid(row=1, column=0, sticky="nsew")
        reports_panel.columnconfigure(0, weight=1)
        reports_panel.rowconfigure(2, weight=1)

        actions_bar = tk.Frame(reports_panel, bg=COLORS["panel_alt"])
        actions_bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 8))
        actions_bar.columnconfigure(0, weight=1)
        tk.Label(actions_bar, text=f"Mostrando reportes de {REPORT_DIR.name}/",
                 bg=COLORS["panel_alt"], fg=COLORS["muted"], font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", padx=10, pady=6)
        tk.Button(actions_bar, text="📂  Abrir carpeta", bg=COLORS["panel"], fg=COLORS["muted"],
                  relief="solid", borderwidth=1, padx=10, pady=4, font=("Segoe UI", 9), cursor="hand2",
                  command=lambda: subprocess.Popen(["explorer", str(REPORT_DIR)]) if REPORT_DIR.exists() else None
                  ).grid(row=0, column=1, sticky="e", padx=(0, 6), pady=4)
        tk.Button(actions_bar, text="🗘  Actualizar", bg=COLORS["panel"], fg=COLORS["muted"],
                  relief="solid", borderwidth=1, padx=10, pady=4, font=("Segoe UI", 9), cursor="hand2",
                  command=self._refresh_reports
                  ).grid(row=0, column=2, sticky="e", padx=(0, 6), pady=4)
        tk.Button(actions_bar, text="Borrar antiguos", bg=COLORS["danger"], fg="#ffffff",
                  relief="solid", borderwidth=1, padx=10, pady=4, font=("Segoe UI", 9, "bold"), cursor="hand2",
                  command=self._delete_old_reports
                  ).grid(row=0, column=3, sticky="e", padx=(0, 10), pady=4)

        self.reports_tree = ttk.Treeview(reports_panel, columns=("name", "date", "size"), show="headings")
        self.reports_tree.heading("name", text="REPORT NAME")
        self.reports_tree.heading("date", text="DATE")
        self.reports_tree.heading("size", text="SIZE (KB)")
        self.reports_tree.column("date", width=160, anchor="w")
        self.reports_tree.column("size", width=100, anchor="e")
        self.reports_tree.tag_configure("odd", background=COLORS["tree_odd"])
        self.reports_tree.tag_configure("even", background=COLORS["tree_even"])
        self.reports_tree.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 18))

    def _build_logs(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        term_bg = COLORS["primary_container"]

        terminal = RoundedCard(parent, radius=14, bg=term_bg, border="#334155",
                               parent_bg=COLORS["bg"])
        terminal.grid(row=0, column=0, sticky="nsew")
        terminal.columnconfigure(0, weight=1)
        terminal.rowconfigure(1, weight=1)

        header = tk.Frame(terminal, bg=term_bg)
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(12, 4))
        tk.Label(header, text="●", bg=term_bg, fg=COLORS["log_info"], font=("Segoe UI", 10)).grid(row=0, column=0, padx=(0, 6))
        tk.Label(header, text="LIVE LOG OUTPUT", bg=term_bg, fg=COLORS["log_muted"],
                 font=("Segoe UI", 9, "bold")).grid(row=0, column=1, sticky="w")

        self.console = tk.Text(
            terminal,
            bg=term_bg,
            fg=COLORS["log_text"],
            insertbackground=COLORS["log_text"],
            relief="flat",
            padx=20, pady=10,
            wrap="word",
            font=("Consolas", 10),
            borderwidth=0,
        )
        self.console.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self.console.tag_configure("info", foreground=COLORS["log_info"])
        self.console.tag_configure("error", foreground=COLORS["log_error"])
        self.console.tag_configure("warn", foreground="#ffb95f")
        self.console.tag_configure("debug", foreground=COLORS["log_debug"])
        self.console.tag_configure("muted", foreground=COLORS["log_muted"])
        self.console.tag_configure("telegram", foreground=COLORS["log_info"])

    def _panel(self, parent: ttk.Frame, title: str) -> ttk.Frame:
        return self._card(parent, title)

    def _card(self, parent, title: str, chip_text: str | None = None) -> tk.Frame:
        card = RoundedCard(parent, radius=14, bg=COLORS["panel"], border=COLORS["border"])
        card.columnconfigure(0, weight=1)
        header = tk.Frame(card, bg=COLORS["panel"])
        header.grid(row=0, column=0, columnspan=99, sticky="ew", padx=20, pady=(16, 4))
        header.columnconfigure(0, weight=1)
        tk.Label(header, text=title, bg=COLORS["panel"], fg=COLORS["text"],
                 font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w")
        if chip_text:
            tk.Label(header, text=chip_text, bg=COLORS["panel_highest"], fg=COLORS["primary"],
                     font=("Segoe UI", 8, "bold"), padx=8, pady=3).grid(row=0, column=1, sticky="e")
        return card

    def _metric(self, parent: ttk.Frame, column: int, label: str, value: tk.StringVar, icon: str = "") -> None:
        card = RoundedCard(parent, radius=12, bg=COLORS["panel"], border=COLORS["border"])
        card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 6, 6 if column < 3 else 0))
        card.columnconfigure(0, weight=1)
        inner = tk.Frame(card, bg=COLORS["panel"])
        inner.grid(row=0, column=0, sticky="ew", padx=16, pady=14)
        inner.columnconfigure(0, weight=1)
        tk.Label(inner, text=label.upper(), bg=COLORS["panel"], fg=COLORS["muted"],
                 font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w", columnspan=2)
        tk.Label(inner, textvariable=value, bg=COLORS["panel"], fg=COLORS["primary"],
                 font=("Segoe UI", 26, "bold")).grid(row=1, column=0, sticky="w", pady=(6, 0))
        if icon:
            tk.Label(inner, text=icon, bg=COLORS["panel"], fg=COLORS["accent"],
                     font=("Segoe UI Symbol", 16)).grid(row=1, column=1, sticky="e", pady=(6, 0))

    def _action_card(self, parent: ttk.Frame, row: int, column: int, *, icon: str, title: str, description: str, command) -> None:
        card = RoundedCard(parent, radius=14, bg=COLORS["panel"], border=COLORS["border"])
        card.grid(row=row, column=column, sticky="nsew",
                  padx=(20 if column == 0 else 8, 20 if column == 1 else 8), pady=(0, 12),
                  ipady=18)
        card.configure(cursor="hand2")
        icon_lbl = tk.Label(card, text=icon, bg=COLORS["panel"], fg=COLORS["accent"], font=("Segoe UI", 20, "bold"))
        icon_lbl.pack(anchor="w", padx=18, pady=(22, 10))
        title_lbl = tk.Label(card, text=title, bg=COLORS["panel"], fg=COLORS["text"],
                             font=("Segoe UI", 12, "bold"), anchor="w")
        title_lbl.pack(anchor="w", padx=18, pady=(0, 4))
        desc_lbl = tk.Label(card, text=description, bg=COLORS["panel"], fg=COLORS["muted"],
                            font=("Segoe UI", 9), anchor="w", justify="left", wraplength=240)
        desc_lbl.pack(anchor="w", padx=18, pady=(2, 24))

        def on_click(_event=None):
            command()
        for widget in (card, icon_lbl, title_lbl, desc_lbl):
            widget.bind("<Button-1>", on_click)
        def on_enter(_e):
            for w in (icon_lbl, title_lbl, desc_lbl):
                w.configure(bg=COLORS["panel_alt"])
        def on_leave(_e):
            for w in (icon_lbl, title_lbl, desc_lbl):
                w.configure(bg=COLORS["panel"])
        for widget in (card, icon_lbl, title_lbl, desc_lbl):
            widget.bind("<Enter>", on_enter)
            widget.bind("<Leave>", on_leave)

    def _path_row(self, parent: ttk.Frame, label: str, variable: tk.StringVar, row: int, browse_func) -> None:
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", padx=(20, 10), pady=7)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=7)
        ttk.Button(parent, text="Elegir", style="Tool.TButton", command=lambda: browse_func(variable)).grid(
            row=row, column=2, sticky="e", padx=(8, 20), pady=7
        )

    def _readonly_path(self, parent: ttk.Frame, label: str, variable: tk.StringVar, row: int) -> None:
        ttk.Label(parent, text=label, style="CardDesc.TLabel").grid(row=row, column=0, sticky="w", padx=20, pady=(4, 2))
        path_frame = tk.Frame(parent, bg=COLORS["panel_alt"], highlightthickness=1, highlightbackground=COLORS["border"])
        path_frame.grid(row=row + 1, column=0, sticky="ew", padx=20, pady=(0, 10))
        path_frame.columnconfigure(0, weight=1)
        tk.Label(path_frame, textvariable=variable, bg=COLORS["panel_alt"], fg=COLORS["primary"],
                 font=("Consolas", 9), anchor="w", padx=8, pady=6).grid(row=0, column=0, sticky="ew")

    def _browse_file(self, variable: tk.StringVar) -> None:
        path = filedialog.askopenfilename(initialdir=str(BASE_DIR))
        if path:
            variable.set(path)

    def _browse_template_file(self, variable: tk.StringVar) -> None:
        current = Path(variable.get()).expanduser() if variable.get().strip() else BASE_DIR
        initial_dir = current.parent if current.parent.exists() else BASE_DIR
        path = filedialog.askopenfilename(
            initialdir=str(initial_dir),
            filetypes=(("INI files", "*.ini"), ("Todos", "*.*")),
        )
        if path:
            variable.set(path)
            self._load_template_clicked(show_success=False)

    def _browse_dir(self, variable: tk.StringVar) -> None:
        path = filedialog.askdirectory(initialdir=str(BASE_DIR))
        if path:
            variable.set(path)

    def _browse_mq5_file(self, variable: tk.StringVar) -> None:
        initial_dir = self.compile_root.get().strip() or str(BASE_DIR)
        path = filedialog.askopenfilename(
            initialdir=initial_dir,
            filetypes=(("MQL5 source", "*.mq5"), ("Todos", "*.*")),
        )
        if path:
            selected = Path(path)
            variable.set(str(selected))

    def _browse_ex5_file(self, variable: tk.StringVar) -> None:
        initial_dir = self.experts_root.get().strip() or str(BASE_DIR)
        path = filedialog.askopenfilename(
            initialdir=initial_dir,
            filetypes=(("Compiled Expert Advisor", "*.ex5"), ("Todos", "*.*")),
        )
        if path:
            selected = Path(path)
            root = Path(self.experts_root.get()).expanduser() if self.experts_root.get().strip() else None
            if root:
                try:
                    variable.set(str(selected.relative_to(root)))
                    return
                except ValueError:
                    pass
            variable.set(str(selected))

    def _browse_profile_ex5_file(self, variable: tk.StringVar) -> None:
        current = Path(variable.get()).expanduser() if variable.get().strip() else None
        experts_root = Path(self.mt_profile_experts_root.get()).expanduser() if self.mt_profile_experts_root.get().strip() else None
        initial_dir = (
            str(current.parent)
            if current and current.parent.exists()
            else str(experts_root if experts_root and experts_root.exists() else BASE_DIR)
        )
        path = filedialog.askopenfilename(
            initialdir=initial_dir,
            filetypes=(("Compiled Expert Advisor", "*.ex5"), ("Todos", "*.*")),
        )
        if path:
            variable.set(str(Path(path)))

    def _browse_set_file(self, variable: tk.StringVar) -> None:
        current = Path(variable.get()).expanduser() if variable.get().strip() else None
        initial_dir = (
            str(current.parent)
            if current and current.parent.exists()
            else (self.set_files_root.get().strip() or str(BASE_DIR))
        )
        path = filedialog.askopenfilename(
            initialdir=initial_dir,
            filetypes=(("Set files", "*.set"), ("Todos", "*.*")),
        )
        if path:
            variable.set(path)

    def _browse_portfolio_input(self) -> None:
        path = filedialog.askdirectory(initialdir=self.portfolio_input.get().strip() or str(REPORT_DIR))
        if path:
            self.portfolio_input.set(path)
            output = Path(path) / "ALL_STRATEGIES.xlsx"
            if not self.portfolio_output.get().strip():
                self.portfolio_output.set(str(output))
            self._write_ui_settings()
            self._refresh_portfolio_count()

    def _browse_portfolio_output(self) -> None:
        current = Path(self.portfolio_output.get()).expanduser() if self.portfolio_output.get().strip() else BASE_DIR / "outputs" / "ALL_STRATEGIES.xlsx"
        path = filedialog.asksaveasfilename(
            initialdir=str(current.parent if current.parent.exists() else BASE_DIR),
            initialfile=current.name,
            defaultextension=".xlsx",
            filetypes=(("Excel workbook", "*.xlsx"), ("Todos", "*.*")),
        )
        if path:
            self.portfolio_output.set(path)
            self._write_ui_settings()

    def _refresh_portfolio_count(self) -> None:
        if portfolio_find_report_files is None:
            self.portfolio_count.set("Portfolio Manager no disponible.")
            return
        try:
            input_dir = Path(self.portfolio_input.get()).expanduser()
            if not input_dir.exists() or not input_dir.is_dir():
                self.portfolio_count.set("La carpeta no existe.")
                return
            count = len(portfolio_find_report_files(input_dir))
            self.portfolio_count.set(f"Reports encontrados: {count}")
        except Exception as exc:
            self.portfolio_count.set(f"No se pudo leer la carpeta: {exc}")

    def _portfolio_output_path(self, filename: str) -> Path:
        output = Path(self.portfolio_output.get()).expanduser()
        if output.suffix.lower() != ".xlsx":
            output = output.with_suffix(".xlsx")
        return output.with_name(filename)

    def _set_portfolio_running(self, running: bool) -> None:
        self.portfolio_running = running
        state = "disabled" if running else "normal"
        for button in self.portfolio_buttons:
            button.configure(state=state)
        if hasattr(self, "portfolio_progress"):
            if running:
                self.portfolio_progress.start(12)
            else:
                self.portfolio_progress.stop()

    def _run_portfolio_action(self, action: str) -> None:
        if self.portfolio_running:
            messagebox.showwarning("Portfolio en ejecucion", "Ya hay un proceso de Portfolio Manager en marcha.")
            return
        if portfolio_find_report_files is None:
            messagebox.showerror("Portfolio Manager no disponible", "No pude cargar el modulo local portfolio_manager.")
            return

        input_dir = Path(self.portfolio_input.get()).expanduser()
        if not input_dir.exists() or not input_dir.is_dir():
            messagebox.showerror("Carpeta invalida", f"No existe la carpeta de reportes:\n{input_dir}")
            return

        try:
            count = len(portfolio_find_report_files(input_dir))
        except Exception as exc:
            messagebox.showerror("No se pudo leer reports", str(exc))
            return
        if count <= 0:
            messagebox.showwarning("Sin reports", f"No hay .htm/.html en:\n{input_dir}")
            return

        try:
            threshold = abs(float(self.portfolio_threshold.get().replace(",", ".")))
        except ValueError:
            messagebox.showerror("Umbral invalido", "Umbral DD diario debe ser un numero. Ejemplo: 50")
            return

        actions = {
            "all": ("ALL_STRATEGIES", generate_portfolio_workbook, self._portfolio_output_path("ALL_STRATEGIES.xlsx"), ()),
            "dd": ("ALL_STRATEGIES_DD", generate_drawdown_workbook, self._portfolio_output_path("ALL_STRATEGIES_DD.xlsx"), ()),
            "portfolio_dd": ("PORTFOLIO_DD", generate_portfolio_drawdown_workbook, self._portfolio_output_path("PORTFOLIO_DD.xlsx"), ()),
            "portfolio_valley": ("DD_VALLE_TOTAL", generate_portfolio_valley_drawdown_workbook, self._portfolio_output_path("PORTFOLIO_VALLEY_DD.xlsx"), ()),
            "top_valleys": ("5 PEORES VALLES", generate_top_portfolio_valleys_workbook, self._portfolio_output_path("PORTFOLIO_TOP5_VALLEYS.xlsx"), ()),
            "threshold": ("FILTRAR DD", generate_dd_threshold_workbook, self._portfolio_output_path("DD_THRESHOLD.xlsx"), (threshold,)),
        }
        title, func, output, extra_args = actions[action]
        if func is None:
            messagebox.showerror("Portfolio Manager no disponible", "No pude cargar el generador seleccionado.")
            return

        if not messagebox.askyesno(
            f"Generar {title}",
            f"Se procesaran {count} reporte(s) desde:\n{input_dir}\n\nSalida:\n{output}\n\nEmpezar?",
        ):
            return

        self._write_ui_settings()
        self._set_portfolio_running(True)
        self.portfolio_status.set(f"Iniciando {title}...")
        thread = threading.Thread(
            target=self._portfolio_worker,
            args=(title, func, input_dir, output, extra_args),
            daemon=True,
        )
        thread.start()

    def _portfolio_worker(self, title: str, func, input_dir: Path, output: Path, extra_args: tuple) -> None:
        try:
            reports = func(input_dir, output, *extra_args, progress=lambda msg: self.after(0, self.portfolio_status.set, msg))
        except Exception as exc:
            self.after(0, self._portfolio_finished, False, title, str(exc), None, 0)
            return
        self.after(0, self._portfolio_finished, True, title, "", output, len(reports))

    def _portfolio_finished(self, ok: bool, title: str, error: str, output: Path | None, count: int) -> None:
        self._set_portfolio_running(False)
        self._refresh_portfolio_count()
        if ok:
            message = f"{title} creado: {output}\nEstrategias procesadas: {count}"
            self.portfolio_status.set(message)
            messagebox.showinfo("Portfolio terminado", message)
        else:
            self.portfolio_status.set(error)
            messagebox.showerror(f"Error generando {title}", error)

    def _load_template(self) -> None:
        template_text = self.template_path.get().strip()
        if not template_text:
            raise ValueError("Indica la ruta del template tester.")
        template = Path(template_text).expanduser()
        if not template.exists():
            fallback = BASE_DIR / template.name
            if fallback.exists():
                template = fallback
                self.template_path.set(str(fallback))
            else:
                raise FileNotFoundError(f"No existe el archivo:\n{template}")
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read(template, encoding="utf-8-sig")
        tester = parser["Tester"] if parser.has_section("Tester") else {}
        for key, variable in self.tester_vars.items():
            variable.set(tester.get(key, ""))
        self.status_text.set(f"Cargado {template.name}")

    def _load_template_clicked(self, show_success: bool = True) -> None:
        try:
            self._load_template()
            self._write_ui_settings()
        except Exception as exc:
            self._show_error("No se pudo cargar el template", str(exc))
            return
        if show_success:
            messagebox.showinfo("Template cargado", f"Datos cargados desde:\n{self.template_path.get().strip()}")

    def _save_template(self) -> None:
        template_text = self.template_path.get().strip()
        if not template_text:
            raise ValueError("Indica una ruta para el template tester antes de guardar.")

        template = Path(template_text).expanduser()
        template.parent.mkdir(parents=True, exist_ok=True)
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser["Tester"] = {"Expert": ""}
        for key, variable in self.tester_vars.items():
            parser["Tester"][key] = variable.get().strip()
        parser["Tester"]["Report"] = ""
        with template.open("w", encoding="utf-8", newline="\n") as file:
            parser.write(file, space_around_delimiters=False)
        self._write_ui_settings()
        self.status_text.set(f"Guardado {template.name}")

    def _save_template_clicked(self) -> None:
        try:
            self._save_template()
        except Exception as exc:
            self._show_error("No se pudo guardar el template", str(exc))
            return
        messagebox.showinfo("Template guardado", f"tester_template.ini guardado correctamente en:\n{self.template_path.get().strip()}")

    def _save_paths(self) -> None:
        self._write_single_path(COMPILE_ROOT_FILE, self.compile_root.get(), "Carpeta raiz donde estan los .mq5 a compilar.")
        self._write_single_path(EXPERTS_ROOT_FILE, self.experts_root.get(), "Carpeta raiz donde estan los .ex5 a testear.")
        self._update_env_vars({
            "MT5_TERMINAL_PATH": self.mt5_path.get().strip(),
            "MT5_METAEDITOR_PATH": self.metaeditor_path.get().strip(),
        })
        self._write_ui_settings()
        self.status_text.set("Rutas y opciones guardadas")
        try:
            self._load_template()
        except Exception:
            self.status_text.set("Rutas guardadas; template tester no cargado")
        self._refresh_all()

    def _update_env_vars(self, updates: dict[str, str]) -> None:
        existing_lines: list[str] = []
        if ENV_FILE.exists():
            existing_lines = ENV_FILE.read_text(encoding="utf-8-sig").splitlines()

        remaining = dict(updates)
        new_lines: list[str] = []
        for line in existing_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                new_lines.append(line)
                continue
            name = stripped.split("=", 1)[0].strip()
            if name in remaining:
                new_lines.append(f"{name}={remaining.pop(name)}")
            else:
                new_lines.append(line)

        for name, value in remaining.items():
            new_lines.append(f"{name}={value}")

        ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        import mt5_env
        mt5_env._PROJECT_ENV = None

    def _save_paths_clicked(self) -> None:
        try:
            self._save_paths()
        except Exception as exc:
            self._show_error("No se pudieron guardar las rutas", str(exc))
            return
        messagebox.showinfo("Rutas guardadas", "Las rutas y opciones se guardaron correctamente.")

    def _save_config_clicked(self) -> None:
        try:
            self._write_ui_settings()
        except Exception as exc:
            self._show_error("No se pudo guardar la configuracion", str(exc))
            return
        self.status_text.set("Configuracion guardada")
        messagebox.showinfo("Configuracion guardada", "La configuracion se guardo correctamente.")

    def _save_ubs_agent_clicked(self) -> None:
        try:
            self._write_ui_settings()
        except Exception as exc:
            self._show_error("No se pudo guardar Agente UBS", str(exc))
            return
        self.status_text.set("Configuracion Agente UBS guardada")
        messagebox.showinfo("Agente UBS", "La configuracion del Agente UBS se guardo correctamente.")

    def _multiterminal_worker_limit(self) -> int:
        try:
            workers = int(self.multiterminal_workers.get())
        except (tk.TclError, ValueError):
            workers = 1
        workers = max(1, workers)
        try:
            if int(self.multiterminal_workers.get()) != workers:
                self.multiterminal_workers.set(workers)
        except (tk.TclError, ValueError):
            self.multiterminal_workers.set(workers)
        return workers

    def _active_multiterminal_profiles(self) -> list[dict[str, object]]:
        return [profile for profile in self.multiterminal_profiles if bool(profile.get("enabled"))]

    def _update_multiterminal_summary(self) -> None:
        if not hasattr(self, "multiterminal_summary"):
            return
        active = len(self._active_multiterminal_profiles())
        workers = min(self._multiterminal_worker_limit(), active) if active else 0
        mode = "on" if self.multiterminal_enabled.get() else "off"
        self.multiterminal_summary.set(f"{active} activas / usando hasta {workers} / {mode}")

    def _save_current_multiterminal_editor(self) -> None:
        if not hasattr(self, "mt_profile_name"):
            return
        index = self.mt_selected_index
        if index is None or index < 0 or index >= len(self.multiterminal_profiles):
            return
        self.multiterminal_profiles[index] = {
            "enabled": bool(self.mt_profile_enabled.get()),
            "name": self.mt_profile_name.get().strip() or f"Terminal {index + 1}",
            "mt5_path": self.mt_profile_mt5_path.get().strip(),
            "data_dir": self.mt_profile_data_dir.get().strip(),
            "experts_root": self.mt_profile_experts_root.get().strip(),
            "ubs_ex5_file": self.mt_profile_ubs_ex5_file.get().strip(),
            "portable": bool(self.mt_profile_portable.get()),
        }

    def _multiterminal_tree_values(self, profile: dict[str, object]) -> tuple[str, str, str, str, str, str, str]:
        return (
            "si" if bool(profile.get("enabled")) else "no",
            str(profile.get("name") or ""),
            str(profile.get("mt5_path") or ""),
            str(profile.get("data_dir") or ""),
            str(profile.get("experts_root") or ""),
            str(profile.get("ubs_ex5_file") or ""),
            "si" if bool(profile.get("portable")) else "no",
        )

    def _refresh_multiterminal_tree(self) -> None:
        if not hasattr(self, "multiterminal_tree"):
            self._update_multiterminal_summary()
            return
        selected_index = self.mt_selected_index
        for item in self.multiterminal_tree.get_children():
            self.multiterminal_tree.delete(item)
        for index, profile in enumerate(self.multiterminal_profiles):
            tag = "odd" if index % 2 else "even"
            self.multiterminal_tree.insert(
                "",
                "end",
                iid=str(index),
                values=self._multiterminal_tree_values(profile),
                tags=(tag,),
            )
        if selected_index is not None and 0 <= selected_index < len(self.multiterminal_profiles):
            self.multiterminal_tree.selection_set(str(selected_index))
            self.multiterminal_tree.focus(str(selected_index))
        self._update_multiterminal_summary()

    def _update_multiterminal_tree_item(self, index: int) -> None:
        if not hasattr(self, "multiterminal_tree"):
            return
        if index < 0 or index >= len(self.multiterminal_profiles):
            return
        iid = str(index)
        if self.multiterminal_tree.exists(iid):
            self.multiterminal_tree.item(iid, values=self._multiterminal_tree_values(self.multiterminal_profiles[index]))

    def _load_multiterminal_profile_editor(self, index: int) -> None:
        if index < 0 or index >= len(self.multiterminal_profiles):
            self.mt_selected_index = None
            self.mt_profile_enabled.set(False)
            self.mt_profile_portable.set(False)
            self.mt_profile_name.set("")
            self.mt_profile_mt5_path.set("")
            self.mt_profile_data_dir.set("")
            self.mt_profile_experts_root.set("")
            self.mt_profile_ubs_ex5_file.set("")
            return
        profile = self.multiterminal_profiles[index]
        self.mt_selected_index = index
        self.mt_profile_enabled.set(bool(profile.get("enabled")))
        self.mt_profile_portable.set(bool(profile.get("portable")))
        self.mt_profile_name.set(str(profile.get("name") or f"Terminal {index + 1}"))
        self.mt_profile_mt5_path.set(str(profile.get("mt5_path") or ""))
        self.mt_profile_data_dir.set(str(profile.get("data_dir") or ""))
        self.mt_profile_experts_root.set(str(profile.get("experts_root") or ""))
        self.mt_profile_ubs_ex5_file.set(str(profile.get("ubs_ex5_file") or ""))

    def _select_multiterminal_profile(self, index: int) -> None:
        self._load_multiterminal_profile_editor(index)
        if hasattr(self, "multiterminal_tree") and 0 <= index < len(self.multiterminal_profiles):
            self.multiterminal_tree.selection_set(str(index))
            self.multiterminal_tree.focus(str(index))

    def _on_multiterminal_tree_select(self, _event=None) -> None:
        if not hasattr(self, "multiterminal_tree"):
            return
        selected = self.multiterminal_tree.selection()
        if not selected:
            return
        try:
            index = int(selected[0])
        except (TypeError, ValueError):
            return
        if index == self.mt_selected_index:
            return
        old_index = self.mt_selected_index
        self._save_current_multiterminal_editor()
        if old_index is not None:
            self._update_multiterminal_tree_item(old_index)
        self._load_multiterminal_profile_editor(index)
        self._update_multiterminal_summary()

    def _apply_multiterminal_editor(self) -> None:
        self._save_current_multiterminal_editor()
        if self.mt_selected_index is not None:
            self._update_multiterminal_tree_item(self.mt_selected_index)
        self._update_multiterminal_summary()
        self.status_text.set("Fila multiterminal aplicada")

    def _new_multiterminal_profile(self, name: str | None = None) -> dict[str, object]:
        index = len(self.multiterminal_profiles) + 1
        return {
            "enabled": True,
            "name": name or f"Terminal {index}",
            "mt5_path": self.mt5_path.get().strip(),
            "data_dir": self.mt5_data_root.get().strip(),
            "experts_root": self.experts_root.get().strip(),
            "ubs_ex5_file": self.ubs_ex5_file.get().strip(),
            "portable": False,
        }

    def _add_multiterminal_profile(self) -> None:
        self._save_current_multiterminal_editor()
        self.multiterminal_profiles.append(self._new_multiterminal_profile())
        self._refresh_multiterminal_tree()
        self._select_multiterminal_profile(len(self.multiterminal_profiles) - 1)

    def _duplicate_multiterminal_profile(self) -> None:
        self._save_current_multiterminal_editor()
        index = self.mt_selected_index if self.mt_selected_index is not None else 0
        if index < 0 or index >= len(self.multiterminal_profiles):
            return
        source = dict(self.multiterminal_profiles[index])
        source["name"] = f"{source.get('name') or f'Terminal {index + 1}'} copia"
        self.multiterminal_profiles.append(source)
        self._refresh_multiterminal_tree()
        self._select_multiterminal_profile(len(self.multiterminal_profiles) - 1)

    def _delete_multiterminal_profile(self) -> None:
        index = self.mt_selected_index
        if index is None or index < 0 or index >= len(self.multiterminal_profiles):
            messagebox.showinfo("Multiterminales", "Selecciona una terminal para eliminar.")
            return
        name = str(self.multiterminal_profiles[index].get("name") or f"Terminal {index + 1}")
        if not messagebox.askyesno("Eliminar terminal", f"Eliminar el perfil '{name}'?"):
            return
        del self.multiterminal_profiles[index]
        self.mt_selected_index = None
        self._refresh_multiterminal_tree()
        if self.multiterminal_profiles:
            self._select_multiterminal_profile(min(index, len(self.multiterminal_profiles) - 1))
        else:
            self._load_multiterminal_profile_editor(-1)
        self._update_multiterminal_summary()

    def _profile_path(self, profile: dict[str, object], key: str, *, base_key: str | None = None) -> Path | None:
        raw = str(profile.get(key) or "").strip()
        if not raw:
            return None
        path = Path(raw).expanduser()
        if path.is_absolute() or not base_key:
            return path
        base_raw = str(profile.get(base_key) or "").strip()
        return (Path(base_raw).expanduser() / path) if base_raw else path

    def _validate_multiterminal_errors(self, *, require_ubs: bool = True) -> list[str]:
        self._save_current_multiterminal_editor()
        errors: list[str] = []
        active = self._active_multiterminal_profiles()
        if not active:
            errors.append("No hay terminales habilitadas.")
        for index, profile in enumerate(self.multiterminal_profiles, start=1):
            if not bool(profile.get("enabled")):
                continue
            name = str(profile.get("name") or f"Terminal {index}")
            mt5_path = self._profile_path(profile, "mt5_path")
            data_dir = self._profile_path(profile, "data_dir")
            experts_root = self._profile_path(profile, "experts_root")
            ubs_ex5 = self._profile_path(profile, "ubs_ex5_file", base_key="experts_root")
            if not mt5_path:
                errors.append(f"{name}: falta terminal64.exe.")
            elif not mt5_path.exists() or not mt5_path.is_file():
                errors.append(f"{name}: no existe terminal64.exe: {mt5_path}")
            if data_dir and (not data_dir.exists() or not data_dir.is_dir()):
                errors.append(f"{name}: carpeta datos MT5 invalida: {data_dir}")
            if not experts_root:
                errors.append(f"{name}: falta carpeta MQL5\\Experts.")
            elif not experts_root.exists() or not experts_root.is_dir():
                errors.append(f"{name}: carpeta MQL5\\Experts invalida: {experts_root}")
            if require_ubs:
                if not ubs_ex5:
                    errors.append(f"{name}: falta archivo UBS .ex5.")
                elif not ubs_ex5.exists() or not ubs_ex5.is_file():
                    errors.append(f"{name}: no existe UBS .ex5: {ubs_ex5}")
        return errors

    def _validate_multiterminal_profiles(self) -> bool:
        errors = self._validate_multiterminal_errors()
        if errors:
            details = "\n".join(f"- {item}" for item in errors[:20])
            if len(errors) > 20:
                details += f"\n- ... y {len(errors) - 20} mas"
            self._show_error("Multiterminal invalido", details)
            return False
        self.status_text.set("Multiterminal validado")
        messagebox.showinfo("Multiterminales", "Perfiles multiterminal validados correctamente.")
        return True

    def _save_multiterminal_clicked(self) -> None:
        try:
            self._write_ui_settings()
        except Exception as exc:
            self._show_error("No se pudo guardar Multiterminales", str(exc))
            return
        self._refresh_multiterminal_tree()
        self.status_text.set("Multiterminales guardados")
        messagebox.showinfo("Multiterminales", "La configuracion multiterminal se guardo correctamente.")

    def _on_multiterminal_changed(self) -> None:
        self._multiterminal_worker_limit()
        self._save_current_multiterminal_editor()
        self._update_multiterminal_summary()
        try:
            self._write_ui_settings()
        except Exception:
            pass

    def _delete_old_reports(self) -> None:
        report_suffixes = {".htm", ".html", ".png", ".set"}
        files = [
            path for path in REPORT_DIR.iterdir()
            if path.is_file() and path.suffix.lower() in report_suffixes
        ]
        if not files:
            messagebox.showinfo("Sin reportes", "No hay reportes generados para borrar.")
            return
        if not messagebox.askyesno(
            "Borrar reportes antiguos",
            f"Se borraran {len(files)} archivo(s) de reportes de la carpeta {REPORT_DIR}.\n\nContinuar?"
        ):
            return

        deleted = 0
        failures: list[str] = []
        for path in files:
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                failures.append(f"{path.name}: {exc}")

        self._refresh_reports()
        self.status_text.set(f"Reportes borrados: {deleted}")
        self._append_console(f"\nReportes borrados: {deleted}\n", tag="warn")
        if failures:
            details = "\n".join(failures[:12])
            self._show_error("No se pudieron borrar todos los reportes", details)
        else:
            messagebox.showinfo("Reportes borrados", f"Se borraron {deleted} reporte(s).")

    def _write_ui_settings(self) -> None:
        self._save_current_multiterminal_editor()
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser["Paths"] = {
            "mt5_path": self.mt5_path.get().strip(),
            "mt5_data_root": self.mt5_data_root.get().strip(),
            "metaeditor_path": self.metaeditor_path.get().strip(),
            "compile_root": self.compile_root.get().strip(),
            "compile_file": self.compile_file.get().strip(),
            "experts_root": self.experts_root.get().strip(),
            "ubs_ex5_file": self.ubs_ex5_file.get().strip(),
            "set_files_root": self.set_files_root.get().strip(),
            "ubs_set_file": self.ubs_set_file.get().strip(),
            "template_path": self.template_path.get().strip(),
            "ubs_generation_output": self.ubs_generation_output.get().strip(),
            "portfolio_input": self.portfolio_input.get().strip(),
            "portfolio_output": self.portfolio_output.get().strip(),
        }
        parser["General"] = {
            "recursive": "1" if self.recursive.get() else "0",
            "delay": str(self.delay.get()),
            "ubs_generation_count": str(self.ubs_generation_count.get()),
            "ubs_variants_per_seed": str(self.ubs_variants_per_seed.get()),
            "ubs_max_seeds": str(self.ubs_max_seeds.get()),
            "ubs_agent_execute": "1" if self.ubs_agent_execute.get() else "0",
            "ubs_pass_min_net_profit": self.ubs_pass_min_net_profit.get().strip(),
            "ubs_pass_min_profit_factor": self.ubs_pass_min_profit_factor.get().strip(),
            "ubs_pass_min_trades": str(self.ubs_pass_min_trades.get()),
            "ubs_pass_max_drawdown_pct": self.ubs_pass_max_drawdown_pct.get().strip(),
            "ubs_pass_min_recovery_factor": self.ubs_pass_min_recovery_factor.get().strip(),
            "symbol_suffix_enabled": "1" if self.symbol_suffix_enabled.get() else "0",
            "symbol_suffix": self.symbol_suffix.get().strip(),
            "symbol_map_enabled": "1" if self.symbol_map_enabled.get() else "0",
            "symbol_map": self.symbol_map.get().strip(),
            "telegram_enabled": "1" if self.telegram_enabled.get() else "0",
            "portfolio_threshold": self.portfolio_threshold.get().strip(),
            "theme": self.theme_mode.get(),
        }
        parser["Multiterminal"] = {
            "enabled": "1" if self.multiterminal_enabled.get() else "0",
            "workers": str(self._multiterminal_worker_limit()),
        }
        for index, profile in enumerate(self.multiterminal_profiles, start=1):
            parser[f"Terminal.{index}"] = {
                "enabled": "1" if bool(profile.get("enabled")) else "0",
                "name": str(profile.get("name") or f"Terminal {index}").strip(),
                "mt5_path": str(profile.get("mt5_path") or "").strip(),
                "data_dir": str(profile.get("data_dir") or "").strip(),
                "experts_root": str(profile.get("experts_root") or "").strip(),
                "ubs_ex5_file": str(profile.get("ubs_ex5_file") or "").strip(),
                "portable": "1" if bool(profile.get("portable")) else "0",
            }
        with UI_SETTINGS_FILE.open("w", encoding="utf-8", newline="\n") as file:
            parser.write(file, space_around_delimiters=False)
        self._update_multiterminal_summary()

    def _delete_historical_data(self) -> None:
        if self.process and self.process.poll() is None:
            messagebox.showwarning("Proceso activo", "Hay un proceso en ejecucion. Detenlo antes de limpiar.")
            return
        scripts = self._find_clean_scripts()
        if not scripts:
            messagebox.showerror(
                "Scripts no encontrados",
                "No se encontraron cleanOldTest.ps1 / cleanOlddata.ps1 en la carpeta scripts/."
            )
            return
        if not messagebox.askyesno(
            "Eliminar datos historicos",
            "Esto cerrara MetaTrader y borrara cache de tester/bases/history en TODAS las terminales.\n\n"
            f"Se ejecutaran en orden:\n  - {scripts[0].name}\n  - {scripts[1].name}\n\nContinuar?"
        ):
            return
        self.status_text.set("Limpiando datos historicos...")
        self._append_console("\n=== Limpieza de datos historicos ===\n", tag="warn")
        # Inicializa la barra de progreso para esta tarea
        self.active_task_text.set("Limpiando datos historicos")
        self.active_task_detail.set("0%")
        self._set_progress_color("accent")
        self._progress_running = True
        self._progress_total = len(scripts)
        self._progress_done = 0
        self._progress_target = 2.0
        try:
            self.progress_bar.stop()
            self.progress_bar.configure(mode="determinate", maximum=100)
            self.progress_var.set(0.0)
        except Exception:
            pass
        threading.Thread(target=self._run_clean_scripts, args=(scripts,), daemon=True).start()

    def _find_clean_scripts(self) -> list[Path]:
        candidates_dirs = [BASE_DIR / "scripts", BASE_DIR]
        if getattr(sys, "_MEIPASS", None):
            candidates_dirs.insert(0, Path(sys._MEIPASS) / "scripts")
        order = ("cleanOldTest.ps1", "cleanOlddata.ps1")
        for d in candidates_dirs:
            paths = [d / name for name in order]
            if all(p.exists() for p in paths):
                return paths
        return []

    def _run_clean_scripts(self, scripts: list[Path]) -> None:
        total = max(1, len(scripts))
        failures = 0
        for index, script in enumerate(scripts):
            self.output_queue.put(f"\n>>> Ejecutando {script.name}\n")
            # Al empezar el script: pequeño avance dentro de su slot
            slot_start = 100.0 * index / total
            self.after(0, lambda v=slot_start + 100.0 / total * 0.15: self._set_clean_progress(v))
            try:
                proc = subprocess.Popen(
                    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, encoding="utf-8", errors="replace",
                    creationflags=NO_WINDOW,
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    self.output_queue.put(line)
                proc.wait()
                if proc.returncode != 0:
                    failures += 1
                self.output_queue.put(f"\n>>> {script.name} termino con codigo {proc.returncode}\n")
            except Exception as exc:
                failures += 1
                self.output_queue.put(f"\nERROR ejecutando {script.name}: {exc}\n")
            # Al terminar el script: marca el slot como completo
            slot_end = 100.0 * (index + 1) / total
            self.after(0, lambda v=slot_end: self._set_clean_progress(v))
        self.output_queue.put("\n=== Limpieza terminada ===\n")
        self.after(0, self._finish_clean, failures)

    def _set_clean_progress(self, value: float) -> None:
        value = max(0.0, min(100.0, float(value)))
        self._progress_target = value
        try:
            self.progress_var.set(value)
        except Exception:
            pass
        self.active_task_detail.set(f"{int(round(value))}%")

    def _finish_clean(self, failures: int) -> None:
        self._progress_running = False
        if failures:
            self._set_progress_color("danger")
            self.active_task_text.set("Limpieza con errores")
            self.status_text.set(f"Limpieza terminada con {failures} script(s) fallido(s)")
            messagebox.showwarning(
                "Limpieza con errores",
                f"La limpieza termino con {failures} script(s) fallido(s).\nRevisa la consola en la pestaña Logs."
            )
        else:
            self._set_progress_color("accent")
            try:
                self.progress_var.set(100.0)
            except Exception:
                pass
            self.active_task_text.set("Limpieza completada")
            self.active_task_detail.set("100%")
            self.status_text.set("Limpieza terminada correctamente")
            messagebox.showinfo(
                "Limpieza completada",
                "Se eliminaron los datos historicos correctamente (tester, bases, history, .fxt, .tick)."
            )
        self._refresh_all()

    def _notify_telegram(self, message: str) -> None:
        if not self.telegram_enabled.get():
            return
        token_set = bool(env_value("TELEGRAM_BOT_TOKEN"))
        chat_set = bool(env_value("TELEGRAM_CHAT_ID"))
        if not token_set or not chat_set:
            self.output_queue.put(
                "[Telegram] No se envia: falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en .env\n"
            )
            return
        self.output_queue.put(f"[Telegram] Enviando: {message}\n")

        def on_result(error: str | None) -> None:
            if error:
                self.output_queue.put(f"[Telegram] ERROR: {error}\n")
            else:
                self.output_queue.put("[Telegram] Mensaje enviado correctamente.\n")

        telegram_notify.send_async(message, on_result=on_result)

    def _write_single_path(self, path: Path, value: str, comment: str) -> None:
        text = f"# {comment}\n{value.strip()}\n" if value.strip() else f"# {comment}\n"
        path.write_text(text, encoding="utf-8")

    def _refresh_all(self) -> None:
        self._refresh_experts()
        self._refresh_reports()
        self._refresh_ubs_results()
        self._refresh_ubs_history()
        self._refresh_ubs_seed_eval_summary()
        self._refresh_ubs_seeds()
        self._refresh_ubs_universe()
        self._refresh_ubs_comparison()
        self._refresh_ubs_continue_state()
        self._refresh_portfolio_count()
        self._refresh_last_log()
        self._refresh_multiterminal_tree()

    def _ubs_memory_path(self) -> Path:
        return BASE_DIR / "outputs" / "ubs_memory.sqlite"

    def _refresh_ubs_seed_eval_summary(self) -> None:
        if not hasattr(self, "ubs_seed_eval_summary"):
            return
        try:
            source_dir = self._ubs_generator_source_dir()
            seed_count = len(load_set_files(source_dir, None, recursive=True))
        except Exception:
            self.ubs_seed_eval_summary.set("Semillas: carpeta no valida")
            return

        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            self.ubs_seed_eval_summary.set(f"Semillas: {seed_count} | evaluadas 0 | pendientes {seed_count}")
            return
        try:
            conn = sqlite3.connect(memory_path, timeout=1.0)
            conn.row_factory = sqlite3.Row
            seed_table = conn.execute(
                "select name from sqlite_master where type='table' and name='seed_scores'"
            ).fetchone()
            if not seed_table:
                conn.close()
                self.ubs_seed_eval_summary.set(f"Semillas: {seed_count} | evaluadas 0 | pendientes {seed_count}")
                return
            active_counts = conn.execute(
                """
                select
                    count(*) as total,
                    sum(case when status in ('accepted', 'rejected') and score is not null then 1 else 0 end) as scored,
                    sum(case when status not in ('accepted', 'rejected') or score is null then 1 else 0 end) as pending
                from seed_scores
                where active=1
                """
            ).fetchone()
            inactive = int(conn.execute("select count(*) from seed_scores where active=0").fetchone()[0] or 0)
            conn.close()
        except sqlite3.Error as exc:
            self.ubs_seed_eval_summary.set(f"Semillas: error SQLite ({exc})")
            return

        scored = int(active_counts["scored"] or 0) if active_counts else 0
        pending = max(seed_count - scored, int(active_counts["pending"] or 0) if active_counts else seed_count)
        self.ubs_seed_eval_summary.set(
            f"Semillas: {seed_count} | evaluadas {scored} | pendientes {pending} | obsoletas {inactive}"
        )

    def _sqlite_table_exists(self, conn: sqlite3.Connection, table: str) -> bool:
        return bool(conn.execute("select name from sqlite_master where type='table' and name=?", (table,)).fetchone())

    def _ensure_ubs_seed_override_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            create table if not exists seed_overrides (
                seed_path text primary key,
                symbol text not null default '',
                period text not null default '',
                updated_at text not null
            )
            """
        )
        if self._sqlite_table_exists(conn, "seed_scores"):
            conn.execute(
                """
                update seed_scores
                set status='report_mismatch', accepted=null
                where status in ('accepted', 'rejected')
                  and (upper(symbol)='UNKNOWN' or upper(period)='UNKNOWN')
                """
            )
        conn.commit()

    def _current_ubs_seed_files(self) -> list[Path]:
        return sorted(load_set_files(self._ubs_generator_source_dir(), None, recursive=True), key=lambda path: path.name.lower())

    def _inferred_ubs_seed_fields(self, path: Path) -> tuple[str, str]:
        try:
            fields = infer_tester_fields_from_set(path)
        except Exception:
            fields = {}
        symbol = str(fields.get("Symbol") or "UNKNOWN").strip().upper()
        period = str(fields.get("Period") or "UNKNOWN").strip().upper()
        return symbol, period

    def _refresh_ubs_seeds(self) -> None:
        if not hasattr(self, "ubs_seeds_tree"):
            return
        tree = self.ubs_seeds_tree
        tree.delete(*tree.get_children(""))
        self.ubs_seed_paths.clear()

        try:
            seed_files = self._current_ubs_seed_files()
        except Exception as exc:
            self.ubs_seed_detail.set(f"Carpeta de seeds no valida: {exc}")
            return

        score_rows: dict[str, sqlite3.Row] = {}
        overrides: dict[str, tuple[str, str]] = {}
        inactive_rows: list[sqlite3.Row] = []
        memory_path = self._ubs_memory_path()
        if memory_path.exists():
            try:
                conn = sqlite3.connect(memory_path, timeout=1.0)
                conn.row_factory = sqlite3.Row
                self._ensure_ubs_seed_override_schema(conn)
                if self._sqlite_table_exists(conn, "seed_scores"):
                    rows = conn.execute("select * from seed_scores").fetchall()
                    score_rows = {str(row["seed_path"]): row for row in rows}
                    inactive_rows = [row for row in rows if not int(row["active"] or 0)]
                for row in conn.execute("select seed_path, symbol, period from seed_overrides").fetchall():
                    overrides[str(row["seed_path"])] = (
                        str(row["symbol"] or "").strip().upper(),
                        str(row["period"] or "").strip().upper(),
                    )
                conn.close()
            except sqlite3.Error as exc:
                self.ubs_seed_detail.set(f"Error SQLite semillas: {exc}")

        current_paths = {str(path) for path in seed_files}
        first_item = ""
        for path in seed_files:
            path_text = str(path)
            row = score_rows.get(path_text)
            inferred_symbol, inferred_period = self._inferred_ubs_seed_fields(path)
            override_symbol, override_period = overrides.get(path_text, ("", ""))
            symbol = override_symbol or (str(row["symbol"] or "").strip().upper() if row else inferred_symbol)
            period = override_period or (str(row["period"] or "").strip().upper() if row else inferred_period)
            status = str(row["status"] or "sin_evaluar") if row else "sin_evaluar"
            accepted = ""
            if row and row["accepted"] is not None:
                accepted = "si" if int(row["accepted"]) else "no"
            item = tree.insert(
                "",
                "end",
                values=(
                    self._format_ubs_status(status),
                    symbol,
                    period,
                    self._format_ubs_number(row["score"] if row else None),
                    accepted,
                    "si" if override_symbol or override_period else "no",
                    path.name,
                ),
                tags=(self._ubs_result_tag(status),),
            )
            self.ubs_seed_paths[item] = {"seed_path": path_text, "active": "1", "status": status}
            if not first_item:
                first_item = item

        for row in inactive_rows:
            path_text = str(row["seed_path"] or "")
            if not path_text or path_text in current_paths:
                continue
            status = str(row["status"] or "obsoleta")
            override_symbol, override_period = overrides.get(path_text, ("", ""))
            symbol = override_symbol or str(row["symbol"] or "").strip().upper()
            period = override_period or str(row["period"] or "").strip().upper()
            item = tree.insert(
                "",
                "end",
                values=(
                    "obsoleta",
                    symbol,
                    period,
                    self._format_ubs_number(row["score"]),
                    "",
                    "si" if override_symbol or override_period else "no",
                    Path(path_text).name,
                ),
                tags=("pending",),
            )
            self.ubs_seed_paths[item] = {"seed_path": path_text, "active": "0", "status": status}

        if first_item:
            tree.selection_set(first_item)
            tree.focus(first_item)
            self._on_ubs_seed_select()
        else:
            self.ubs_seed_detail.set("No hay semillas .set en la carpeta UBS")
            self.ubs_seed_override_symbol.set("")
            self.ubs_seed_override_period.set("")

    def _selected_ubs_seed_info(self) -> dict[str, str]:
        if not hasattr(self, "ubs_seeds_tree"):
            return {}
        selected = self.ubs_seeds_tree.selection()
        if not selected:
            return {}
        return self.ubs_seed_paths.get(selected[0], {})

    def _on_ubs_seed_select(self) -> None:
        info = self._selected_ubs_seed_info()
        if not info:
            return
        item = self.ubs_seeds_tree.selection()[0]
        values = self.ubs_seeds_tree.item(item, "values")
        seed_path = info.get("seed_path", "")
        symbol = str(values[1] if len(values) > 1 else "").strip().upper()
        period = str(values[2] if len(values) > 2 else "").strip().upper()
        self.ubs_seed_override_symbol.set("" if symbol == "UNKNOWN" else symbol)
        self.ubs_seed_override_period.set("" if period == "UNKNOWN" else period)
        self.ubs_seed_detail.set(f"{Path(seed_path).name} | estado: {values[0] if values else '-'}")

    def _open_selected_ubs_seed(self) -> None:
        info = self._selected_ubs_seed_info()
        seed_path = info.get("seed_path", "")
        if not seed_path:
            self._show_error("Sin seleccion", "Selecciona una semilla.")
            return
        self._open_local_file(Path(seed_path))

    def _save_ubs_seed_override(self) -> None:
        info = self._selected_ubs_seed_info()
        seed_path = info.get("seed_path", "")
        if not seed_path:
            self._show_error("Sin seleccion", "Selecciona una semilla para guardar Symbol/TF.")
            return
        symbol = self.ubs_seed_override_symbol.get().strip().upper()
        period = self.ubs_seed_override_period.get().strip().upper()
        valid_periods = {"M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"}
        if not symbol:
            self._show_error("Symbol requerido", "Indica la moneda o activo correcto para esta semilla.")
            return
        if period not in valid_periods:
            self._show_error("Timeframe invalido", f"El timeframe debe ser uno de: {', '.join(sorted(valid_periods))}.")
            return
        memory_path = self._ubs_memory_path()
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(memory_path, timeout=3.0)
            self._ensure_ubs_seed_override_schema(conn)
            now = datetime.now().isoformat(timespec="seconds")
            conn.execute(
                """
                insert into seed_overrides (seed_path, symbol, period, updated_at)
                values (?, ?, ?, ?)
                on conflict(seed_path) do update set
                    symbol=excluded.symbol,
                    period=excluded.period,
                    updated_at=excluded.updated_at
                """,
                (seed_path, symbol, period, now),
            )
            if self._sqlite_table_exists(conn, "seed_scores"):
                conn.execute(
                    """
                    update seed_scores
                    set symbol=?,
                        period=?,
                        report_path=case when status in ('accepted', 'rejected') then null else report_path end,
                        score=case when status in ('accepted', 'rejected') then null else score end,
                        accepted=case when status in ('accepted', 'rejected') then null else accepted end,
                        metrics_json=case when status in ('accepted', 'rejected') then null else metrics_json end,
                        status=case when status in ('accepted', 'rejected') then 'pending' else status end
                    where seed_path=?
                    """,
                    (symbol, period, seed_path),
                )
            conn.commit()
            conn.close()
        except sqlite3.Error as exc:
            self._show_error("Error guardando seed", str(exc))
            return
        self.status_text.set("Override de seed guardado")
        self._refresh_ubs_seed_eval_summary()
        self._refresh_ubs_seeds()

    def _ensure_ubs_memory_schema(self, conn: sqlite3.Connection) -> None:
        columns = {str(row["name"]) for row in conn.execute("pragma table_info(runs)")}
        if "hidden" not in columns:
            conn.execute("alter table runs add column hidden integer not null default 0")
            conn.commit()

    def _ubs_continuation_info(self) -> dict[str, object]:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return {"available": False, "message": "Continuar: sin memoria UBS"}
        try:
            conn = sqlite3.connect(memory_path, timeout=1.0)
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            run = conn.execute("select * from runs order by id desc limit 1").fetchone()
            if run is None:
                conn.close()
                return {"available": False, "message": "Continuar: no hay runs guardados"}
            generation_row = conn.execute(
                "select max(generation) as generation from candidates where run_id=?",
                (run["id"],),
            ).fetchone()
            latest_generation = int(generation_row["generation"] or 0)
            pending_row = conn.execute(
                """
                select min(generation) as generation
                from candidates
                where run_id=? and status='generated'
                """,
                (run["id"],),
            ).fetchone()
            pending_generation = int(pending_row["generation"] or 0)
            if pending_generation > 0:
                pending_count = int(conn.execute(
                    """
                    select count(*) as total
                    from candidates
                    where run_id=? and generation=? and status='generated'
                    """,
                    (run["id"], pending_generation),
                ).fetchone()["total"] or 0)
            else:
                pending_count = 0
            rows = conn.execute(
                "select set_path from candidates where run_id=? and generation=?",
                (run["id"], latest_generation),
            ).fetchall() if latest_generation > 0 else []
            conn.close()
        except sqlite3.Error as exc:
            return {"available": False, "message": f"Continuar: error SQLite ({exc})"}

        planned_generations = int(run["generations"] or 0)
        variants_per_seed = int(run["variants_per_seed"] or 0)
        max_seeds = int(run["max_seeds"] or 0)
        execute_backtests = bool(run["execute_backtests"])
        seed_count = len({str(Path(row["set_path"])) for row in rows if Path(row["set_path"]).exists()})
        if latest_generation <= 0 or seed_count <= 0:
            return {"available": False, "message": f"Continuar: run #{run['id']} sin seeds disponibles"}

        if execute_backtests and pending_generation > 0 and pending_count > 0:
            remaining_after_pending = max(0, planned_generations - pending_generation)
            return {
                "available": True,
                "message": (
                    f"Continuar: gen {pending_generation} generada sin backtest "
                    f"({pending_count} pendientes); luego faltan {remaining_after_pending} gen"
                ),
                "run_id": int(run["id"]),
                "latest_generation": latest_generation,
                "pending_generation": pending_generation,
                "pending_count": pending_count,
                "planned_generations": planned_generations,
                "remaining": remaining_after_pending,
                "seed_count": pending_count,
                "variants_per_seed": variants_per_seed,
                "max_seeds": max_seeds,
                "execute_backtests": execute_backtests,
            }

        remaining = max(0, planned_generations - latest_generation)
        if remaining <= 0:
            return {
                "available": False,
                "message": f"Continuar: deshabilitado, run #{run['id']} completo ({latest_generation}/{planned_generations})",
                "run_id": int(run["id"]),
                "latest_generation": latest_generation,
                "planned_generations": planned_generations,
                "remaining": 0,
                "seed_count": seed_count,
                "variants_per_seed": variants_per_seed,
                "max_seeds": max_seeds,
                "execute_backtests": execute_backtests,
            }
        return {
            "available": True,
            "message": f"Continuar: run #{run['id']} pendiente ({latest_generation}/{planned_generations}), faltan {remaining} gen",
            "run_id": int(run["id"]),
            "latest_generation": latest_generation,
            "pending_generation": 0,
            "pending_count": 0,
            "planned_generations": planned_generations,
            "remaining": remaining,
            "seed_count": seed_count,
            "variants_per_seed": variants_per_seed,
            "max_seeds": max_seeds,
            "execute_backtests": execute_backtests,
        }

    def _refresh_ubs_continue_state(self) -> None:
        info = self._ubs_continuation_info()
        available = bool(info.get("available"))
        self.ubs_continue_status.set(str(info.get("message") or "Continuar: no disponible"))
        if self.ubs_continue_button is not None:
            self.ubs_continue_button.set_disabled(not available)

    def _refresh_ubs_results(self) -> None:
        if hasattr(self, "ubs_results_tree"):
            for item in self.ubs_results_tree.get_children():
                self.ubs_results_tree.delete(item)
        self.ubs_result_paths.clear()

        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            self.ubs_results_summary.set("Sin resultados UBS")
            self.ubs_results_status.set(f"No existe memoria: {memory_path}")
            return

        try:
            conn = sqlite3.connect(memory_path, timeout=1.0)
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            latest_run = conn.execute(
                "select * from runs where hidden=0 order by id desc limit 1"
            ).fetchone()
            if latest_run is None:
                total_runs = conn.execute("select count(*) as total from runs").fetchone()["total"]
                self.ubs_results_summary.set("Sin resultados visibles")
                if total_runs:
                    self.ubs_results_status.set("Los resultados anteriores estan archivados; el agente conserva la memoria.")
                else:
                    self.ubs_results_status.set(f"Memoria: {memory_path}")
                conn.close()
                return

            counts = conn.execute(
                """
                select
                    count(*) as total,
                    sum(case when score is not null then 1 else 0 end) as scored,
                    sum(case when status = 'accepted' then 1 else 0 end) as accepted,
                    sum(case when status = 'rejected' then 1 else 0 end) as rejected,
                    sum(case when status = 'generated' then 1 else 0 end) as generated,
                    sum(case when status = 'no_report' then 1 else 0 end) as no_report,
                    sum(case when status = 'report_mismatch' then 1 else 0 end) as report_mismatch
                from candidates
                where run_id = ?
                """,
                (latest_run["id"],),
            ).fetchone()
            rows = conn.execute(
                """
                select *
                from candidates
                where run_id = ?
                order by
                    case
                        when status = 'accepted' then 0
                        when score is not null then 1
                        else 2
                    end,
                    score desc,
                    id desc
                limit 300
                """,
                (latest_run["id"],),
            ).fetchall()
            conn.close()
        except sqlite3.Error as exc:
            self.ubs_results_summary.set("No se pudieron leer resultados UBS")
            self.ubs_results_status.set(str(exc))
            return

        total = int(counts["total"] or 0)
        scored = int(counts["scored"] or 0)
        accepted = int(counts["accepted"] or 0)
        rejected = int(counts["rejected"] or 0)
        generated = int(counts["generated"] or 0)
        no_report = int(counts["no_report"] or 0)
        report_mismatch = int(counts["report_mismatch"] or 0)
        self.ubs_results_summary.set(
            f"Run #{latest_run['id']} | {latest_run['created_at']} | "
            f"candidatos {total} | puntuados {scored} | aceptados {accepted} | rechazados {rejected}"
        )
        extra = []
        if generated:
            extra.append(f"generados sin backtest {generated}")
        if no_report:
            extra.append(f"sin reporte {no_report}")
        if report_mismatch:
            extra.append(f"mismatch reporte {report_mismatch}")
        extra_text = f" | {', '.join(extra)}" if extra else ""
        backtests = "si" if latest_run["execute_backtests"] else "no"
        self.ubs_results_status.set(
            f"Output: {latest_run['output_dir']} | Backtests: {backtests}{extra_text}"
        )

        if not hasattr(self, "ubs_results_tree"):
            return
        for index, row in enumerate(rows):
            metrics = self._parse_ubs_metrics(row["metrics_json"])
            status = str(row["status"] or "")
            item = self.ubs_results_tree.insert(
                "",
                "end",
                values=(
                    row["run_id"],
                    row["generation"],
                    self._format_ubs_status(status),
                    row["target_symbol"] or row["symbol"],
                    row["period"],
                    self._format_ubs_number(row["score"]),
                    self._format_ubs_number(metrics.get("net_profit")),
                    self._format_ubs_number(metrics.get("profit_factor")),
                    self._format_ubs_number(metrics.get("drawdown_pct")),
                    self._format_ubs_int(metrics.get("trades")),
                    self._format_ubs_set_label(row),
                ),
                tags=(self._ubs_result_tag(status), "odd" if index % 2 else "even"),
            )
            self.ubs_result_paths[item] = {
                "id": str(row["id"] or ""),
                "run": str(row["run_id"] or ""),
                "generation": str(row["generation"] or ""),
                "status": status,
                "symbol": str(row["target_symbol"] or row["symbol"] or ""),
                "period": str(row["period"] or ""),
                "set": str(row["set_path"] or ""),
                "report": str(row["report_path"] or ""),
            }

    def _hide_latest_ubs_results(self) -> None:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            messagebox.showinfo("Agente UBS", "No hay memoria UBS para limpiar.")
            return
        if not messagebox.askyesno(
            "Limpiar vista",
            "Esto ocultara el ultimo run de la tabla, pero conservara la memoria para el agente.\n\nContinuar?",
        ):
            return
        try:
            conn = sqlite3.connect(memory_path, timeout=1.0)
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            latest_run = conn.execute("select id from runs where hidden=0 order by id desc limit 1").fetchone()
            if latest_run is None:
                conn.close()
                messagebox.showinfo("Agente UBS", "No hay resultados visibles para limpiar.")
                return
            conn.execute("update runs set hidden=1 where id=?", (latest_run["id"],))
            conn.commit()
            conn.close()
        except sqlite3.Error as exc:
            self._show_error("No se pudo limpiar la vista UBS", str(exc))
            return
        self.status_text.set("Resultados UBS archivados en memoria")
        self._refresh_ubs_results()
        self._refresh_ubs_history()
        self._refresh_ubs_comparison()

    def _refresh_ubs_history(self) -> None:
        if hasattr(self, "ubs_history_runs_tree"):
            for item in self.ubs_history_runs_tree.get_children():
                self.ubs_history_runs_tree.delete(item)
        if hasattr(self, "ubs_history_candidates_tree"):
            for item in self.ubs_history_candidates_tree.get_children():
                self.ubs_history_candidates_tree.delete(item)
        self.ubs_history_candidate_paths.clear()

        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            self.ubs_history_summary.set("Sin memoria SQLite UBS")
            self.ubs_history_candidate_summary.set(f"No existe: {memory_path}")
            return
        try:
            conn = sqlite3.connect(memory_path, timeout=1.0)
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            rows = conn.execute(
                """
                select
                    r.id, r.created_at, r.generations, r.variants_per_seed, r.max_seeds,
                    r.execute_backtests, r.hidden, r.output_dir,
                    count(c.id) as total,
                    sum(case when c.status = 'accepted' then 1 else 0 end) as accepted,
                    sum(case when c.status = 'rejected' then 1 else 0 end) as rejected
                from runs r
                left join candidates c on c.run_id = r.id
                group by r.id
                order by r.id desc
                """
            ).fetchall()
            conn.close()
        except sqlite3.Error as exc:
            self.ubs_history_summary.set("No se pudo leer historico UBS")
            self.ubs_history_candidate_summary.set(str(exc))
            return

        self.ubs_history_summary.set(f"Runs en SQLite: {len(rows)} | Memoria: {memory_path}")
        if not hasattr(self, "ubs_history_runs_tree"):
            return
        for row in rows:
            item = self.ubs_history_runs_tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(
                    row["id"],
                    row["created_at"],
                    row["generations"],
                    row["variants_per_seed"],
                    row["max_seeds"],
                    "si" if row["execute_backtests"] else "no",
                    "si" if row["hidden"] else "no",
                    int(row["total"] or 0),
                    int(row["accepted"] or 0),
                    int(row["rejected"] or 0),
                    row["output_dir"],
                ),
            )
        if rows:
            self.ubs_history_runs_tree.selection_set(str(rows[0]["id"]))
            self._refresh_ubs_history_candidates()
        else:
            self.ubs_history_candidate_summary.set("Sin runs registrados")

    def _selected_ubs_history_run_id(self) -> int | None:
        if not hasattr(self, "ubs_history_runs_tree"):
            return None
        selected = self.ubs_history_runs_tree.selection()
        if not selected:
            return None
        try:
            return int(selected[0])
        except ValueError:
            return None

    def _refresh_ubs_history_candidates(self) -> None:
        if hasattr(self, "ubs_history_candidates_tree"):
            for item in self.ubs_history_candidates_tree.get_children():
                self.ubs_history_candidates_tree.delete(item)
        self.ubs_history_candidate_paths.clear()
        run_id = self._selected_ubs_history_run_id()
        if run_id is None:
            self.ubs_history_candidate_summary.set("Selecciona un run")
            return
        memory_path = self._ubs_memory_path()
        try:
            conn = sqlite3.connect(memory_path, timeout=1.0)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                select *
                from candidates
                where run_id=?
                order by generation desc,
                    case
                        when status = 'accepted' then 0
                        when score is not null then 1
                        else 2
                    end,
                    score desc,
                    id desc
                limit 1000
                """,
                (run_id,),
            ).fetchall()
            conn.close()
        except sqlite3.Error as exc:
            self.ubs_history_candidate_summary.set(str(exc))
            return

        total = len(rows)
        accepted = sum(1 for row in rows if row["status"] == "accepted")
        rejected = sum(1 for row in rows if row["status"] == "rejected")
        self.ubs_history_candidate_summary.set(f"Run #{run_id}: {total} candidatos | aceptados {accepted} | rechazados {rejected}")
        if not hasattr(self, "ubs_history_candidates_tree"):
            return
        for row in rows:
            metrics = self._parse_ubs_metrics(row["metrics_json"])
            status = str(row["status"] or "")
            item = self.ubs_history_candidates_tree.insert(
                "",
                "end",
                values=(
                    row["id"],
                    row["generation"],
                    self._format_ubs_status(status),
                    row["target_symbol"] or row["symbol"],
                    row["period"],
                    self._format_ubs_number(row["score"]),
                    self._format_ubs_number(metrics.get("net_profit")),
                    self._format_ubs_number(metrics.get("profit_factor")),
                    self._format_ubs_number(metrics.get("drawdown_pct")),
                    self._format_ubs_int(metrics.get("trades")),
                    self._format_ubs_set_label(row),
                ),
                tags=(self._ubs_result_tag(status),),
            )
            self.ubs_history_candidate_paths[item] = {
                "set": str(row["set_path"] or ""),
                "seed": str(row["seed_path"] or ""),
                "report": str(row["report_path"] or ""),
            }

    def _load_ubs_asset_universe(self) -> tuple[list[tuple[str, str, list[str]]], dict[str, str]]:
        path = BASE_DIR / "assets" / "roboforex_assets.ini"
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        if path.exists():
            parser.read(path, encoding="utf-8-sig")
        aliases: dict[str, str] = {}
        if parser.has_section("CommonAliases"):
            aliases = {key.upper(): value.strip() for key, value in parser["CommonAliases"].items()}
        reverse_aliases: dict[str, list[str]] = {}
        for alias, target in aliases.items():
            reverse_aliases.setdefault(target.upper(), []).append(alias)

        assets: list[tuple[str, str, list[str]]] = []
        for section in parser.sections():
            if section == "CommonAliases":
                continue
            symbols = [item.strip() for item in parser[section].get("symbols", "").split(",") if item.strip()]
            for symbol in symbols:
                assets.append((section, symbol, sorted(reverse_aliases.get(symbol.upper(), []))))
        return assets, aliases

    def _canonical_ubs_symbol(self, symbol: str, aliases: dict[str, str]) -> str:
        normalized = str(symbol or "").upper()
        return aliases.get(normalized, normalized).upper()

    def _empty_ubs_stat(self) -> dict[str, object]:
        return {"scores": [], "weights": [], "tests": 0, "accepted": 0, "pending": 0, "best": None}

    def _tag_for_weight(self, value: float | None) -> str:
        if value is None:
            return "neutral"
        return "positive" if value >= 0 else "negative"

    def _refresh_ubs_universe(self) -> None:
        if hasattr(self, "ubs_universe_assets_tree"):
            for item in self.ubs_universe_assets_tree.get_children():
                self.ubs_universe_assets_tree.delete(item)
        if hasattr(self, "ubs_timeframes_tree"):
            for item in self.ubs_timeframes_tree.get_children():
                self.ubs_timeframes_tree.delete(item)

        assets, aliases = self._load_ubs_asset_universe()
        memory_path = self._ubs_memory_path()
        asset_stats: dict[str, dict[str, object]] = {}
        timeframe_stats: dict[str, dict[str, object]] = {}
        total_scored = 0
        total_pending = 0
        total_mismatch = 0
        total_seed_scored = 0
        total_seed_pending = 0

        if memory_path.exists():
            try:
                conn = sqlite3.connect(memory_path, timeout=1.0)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    select target_symbol, symbol, period, score, accepted, status
                    from candidates
                    """
                ).fetchall()
                seed_table = conn.execute(
                    "select name from sqlite_master where type='table' and name='seed_scores'"
                ).fetchone()
                seed_rows = []
                if seed_table:
                    seed_rows = conn.execute(
                        """
                        select symbol, period, score, accepted, status, active
                        from seed_scores
                        where active=1
                        """
                    ).fetchall()
                conn.close()
            except sqlite3.Error as exc:
                self.ubs_universe_summary.set(f"No se pudo leer memoria UBS: {exc}")
                self.ubs_timeframe_summary.set("Sin pesos por error SQLite")
                return

            for row in rows:
                status = str(row["status"] or "")
                if status == "report_mismatch":
                    total_mismatch += 1
                    continue
                canonical = self._canonical_ubs_symbol(row["target_symbol"] or row["symbol"], aliases)
                period = str(row["period"] or "UNKNOWN").upper()
                asset_stat = asset_stats.setdefault(canonical, self._empty_ubs_stat())
                tf_stat = timeframe_stats.setdefault(period, self._empty_ubs_stat())
                if status == "generated":
                    asset_stat["pending"] = int(asset_stat["pending"]) + 1
                    tf_stat["pending"] = int(tf_stat["pending"]) + 1
                    total_pending += 1
                if row["score"] is None or status not in {"accepted", "rejected"}:
                    continue
                score = float(row["score"])
                accepted = bool(row["accepted"])
                weight = score + (20.0 if accepted else 0.0)
                for stat in (asset_stat, tf_stat):
                    stat["scores"].append(score)
                    stat["weights"].append(weight)
                    stat["tests"] = int(stat["tests"]) + 1
                    stat["accepted"] = int(stat["accepted"]) + (1 if accepted else 0)
                    stat["best"] = score if stat["best"] is None else max(float(stat["best"]), score)
                total_scored += 1

            for row in seed_rows:
                status = str(row["status"] or "")
                canonical = self._canonical_ubs_symbol(row["symbol"], aliases)
                period = str(row["period"] or "UNKNOWN").upper()
                asset_stat = asset_stats.setdefault(canonical, self._empty_ubs_stat())
                tf_stat = timeframe_stats.setdefault(period, self._empty_ubs_stat())
                if status in {"pending", "no_report", "parse_error", "report_mismatch"}:
                    asset_stat["pending"] = int(asset_stat["pending"]) + 1
                    tf_stat["pending"] = int(tf_stat["pending"]) + 1
                    total_seed_pending += 1
                if row["score"] is None or status not in {"accepted", "rejected"}:
                    continue
                score = float(row["score"])
                accepted = bool(row["accepted"])
                weight = score + (20.0 if accepted else 0.0)
                for stat in (asset_stat, tf_stat):
                    stat["scores"].append(score)
                    stat["weights"].append(weight)
                    stat["tests"] = int(stat["tests"]) + 1
                    stat["accepted"] = int(stat["accepted"]) + (1 if accepted else 0)
                    stat["best"] = score if stat["best"] is None else max(float(stat["best"]), score)
                total_seed_scored += 1

        universe_symbols = {symbol.upper() for _, symbol, _ in assets}
        observed_only = sorted(symbol for symbol in asset_stats if symbol.upper() not in universe_symbols)
        all_assets = assets + [("Memoria", symbol, []) for symbol in observed_only]
        ranked_assets = []
        for group, symbol, symbol_aliases in all_assets:
            stat = asset_stats.get(symbol.upper(), self._empty_ubs_stat())
            weights = stat["weights"]
            scores = stat["scores"]
            weight_value = (sum(weights) / len(weights)) if weights else None
            avg_score = (sum(scores) / len(scores)) if scores else None
            ranked_assets.append((weight_value if weight_value is not None else -999999.0, group, symbol, symbol_aliases, stat, weight_value, avg_score))
        ranked_assets.sort(key=lambda item: (item[0], item[4]["pending"]), reverse=True)

        if hasattr(self, "ubs_universe_assets_tree"):
            for _, group, symbol, symbol_aliases, stat, weight_value, avg_score in ranked_assets:
                self.ubs_universe_assets_tree.insert(
                    "",
                    "end",
                    values=(
                        group,
                        symbol,
                        ", ".join(symbol_aliases),
                        self._format_ubs_number(weight_value),
                        self._format_ubs_number(avg_score),
                        self._format_ubs_number(stat["best"]),
                        int(stat["tests"]),
                        int(stat["accepted"]),
                        int(stat["pending"]),
                    ),
                    tags=(self._tag_for_weight(weight_value),),
                )

        timeframe_order = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]
        observed_timeframes = sorted(period for period in timeframe_stats if period not in timeframe_order)
        ordered_timeframes = timeframe_order + observed_timeframes
        tf_rows = []
        for period in ordered_timeframes:
            stat = timeframe_stats.get(period, self._empty_ubs_stat())
            weights = stat["weights"]
            scores = stat["scores"]
            weight_value = (sum(weights) / len(weights)) if weights else None
            avg_score = (sum(scores) / len(scores)) if scores else None
            tf_rows.append((weight_value if weight_value is not None else -999999.0, period, stat, weight_value, avg_score))
        tf_rows.sort(key=lambda item: item[0], reverse=True)

        if hasattr(self, "ubs_timeframes_tree"):
            for _, period, stat, weight_value, avg_score in tf_rows:
                self.ubs_timeframes_tree.insert(
                    "",
                    "end",
                    values=(
                        period,
                        self._format_ubs_number(weight_value),
                        self._format_ubs_number(avg_score),
                        self._format_ubs_number(stat["best"]),
                        int(stat["tests"]),
                        int(stat["accepted"]),
                        int(stat["pending"]),
                    ),
                    tags=(self._tag_for_weight(weight_value),),
                )

        self.ubs_universe_summary.set(
            f"Universo: {len(assets)} activos | puntuados validos: {total_scored} | "
            f"semillas puntuadas: {total_seed_scored} | pendientes sin backtest: {total_pending + total_seed_pending} | "
            f"mismatch ignorados: {total_mismatch}"
        )
        self.ubs_timeframe_summary.set(
            "PESO = promedio(score + bonus accepted). El agente prioriza TF buenos y explora M15/M30/H1/H4/D1 reemplazando claves de timeframe existentes."
        )

    def _refresh_ubs_comparison(self) -> None:
        if hasattr(self, "ubs_compare_sets_tree"):
            for item in self.ubs_compare_sets_tree.get_children():
                self.ubs_compare_sets_tree.delete(item)
        if hasattr(self, "ubs_compare_diff_tree"):
            for item in self.ubs_compare_diff_tree.get_children():
                self.ubs_compare_diff_tree.delete(item)
        self.ubs_compare_paths.clear()

        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            self.ubs_compare_summary.set("Sin memoria SQLite UBS")
            self.ubs_compare_detail.set(f"No existe: {memory_path}")
            return
        try:
            conn = sqlite3.connect(memory_path, timeout=1.0)
            conn.row_factory = sqlite3.Row
            run_options = self._ubs_compare_run_options(conn)
            selected_run_id = self._selected_ubs_compare_run_id(run_options)
            if selected_run_id <= 0:
                conn.close()
                self.ubs_compare_summary.set("Sin run visible")
                self.ubs_compare_detail.set("No hay runs UBS visibles en memoria.")
                return
            self._update_ubs_compare_run_combo(run_options, selected_run_id)
            counts = conn.execute(
                """
                select
                    count(*) as total,
                    sum(case when status = 'accepted' then 1 else 0 end) as accepted,
                    sum(case when status = 'rejected' then 1 else 0 end) as rejected
                from candidates
                where run_id = ? and status in ('accepted', 'rejected')
                """,
                (selected_run_id,),
            ).fetchone()
            rows = conn.execute(
                """
                select *
                from candidates
                where run_id = ? and status in ('accepted', 'rejected')
                order by
                    case when status = 'accepted' then 0 else 1 end,
                    score desc,
                    id desc
                """,
                (selected_run_id,),
            ).fetchall()
            conn.close()
        except sqlite3.Error as exc:
            self.ubs_compare_summary.set("No se pudo leer comparacion UBS")
            self.ubs_compare_detail.set(str(exc))
            return

        total = int(counts["total"] or 0) if counts else len(rows)
        accepted = int(counts["accepted"] or 0) if counts else sum(1 for row in rows if row["status"] == "accepted")
        rejected = int(counts["rejected"] or 0) if counts else sum(1 for row in rows if row["status"] == "rejected")
        self.ubs_compare_summary.set(
            f"Run #{selected_run_id}: resultados {total} | aceptados {accepted} | rechazados {rejected} | cargados {len(rows)}"
        )
        if not hasattr(self, "ubs_compare_sets_tree"):
            return
        for row in rows:
            metrics = self._parse_ubs_metrics(row["metrics_json"])
            status = str(row["status"] or "")
            item = self.ubs_compare_sets_tree.insert(
                "",
                "end",
                values=(
                    row["run_id"],
                    row["generation"],
                    self._format_ubs_status(status),
                    row["target_symbol"] or row["symbol"],
                    row["period"],
                    self._format_ubs_number(row["score"]),
                    self._format_ubs_number(metrics.get("net_profit")),
                    self._format_ubs_number(metrics.get("profit_factor")),
                    self._format_ubs_number(metrics.get("drawdown_pct")),
                    self._format_ubs_set_label(row),
                ),
                tags=(self._ubs_result_tag(status),),
            )
            self.ubs_compare_paths[item] = {
                "candidate_id": str(row["id"] or ""),
                "set": str(row["set_path"] or ""),
                "seed": str(row["seed_path"] or ""),
                "mutated": str(row["mutated_keys"] or ""),
            }
        if rows:
            first = self.ubs_compare_sets_tree.get_children()[0]
            self.ubs_compare_sets_tree.selection_set(first)
            self._refresh_ubs_comparison_diff()
        else:
            self.ubs_compare_detail.set("No hay resultados puntuados para el run visible.")

    def _ubs_compare_run_options(self, conn: sqlite3.Connection) -> list[tuple[int, str]]:
        rows = conn.execute(
            """
            select
                r.id,
                r.created_at,
                count(c.id) as total,
                sum(case when c.status = 'accepted' then 1 else 0 end) as accepted,
                sum(case when c.status = 'rejected' then 1 else 0 end) as rejected
            from runs r
            left join candidates c
                on c.run_id = r.id and c.status in ('accepted', 'rejected')
            where coalesce(r.hidden, 0) = 0
            group by r.id
            order by r.id desc
            """
        ).fetchall()
        options: list[tuple[int, str]] = []
        for row in rows:
            run_id = int(row["id"])
            created = str(row["created_at"] or "")[:16]
            total = int(row["total"] or 0)
            accepted = int(row["accepted"] or 0)
            rejected = int(row["rejected"] or 0)
            options.append((run_id, f"#{run_id} | {created} | {total} ({accepted}/{rejected})"))
        return options

    def _selected_ubs_compare_run_id(self, options: list[tuple[int, str]]) -> int:
        selected = self.ubs_compare_run_id.get().strip()
        match = re.search(r"#?(\d+)", selected)
        if match:
            run_id = int(match.group(1))
            if any(option_id == run_id for option_id, _label in options):
                return run_id
        return options[0][0] if options else 0

    def _update_ubs_compare_run_combo(self, options: list[tuple[int, str]], selected_run_id: int) -> None:
        if not hasattr(self, "ubs_compare_run_combo"):
            return
        labels = [label for _run_id, label in options]
        self.ubs_compare_run_combo.configure(values=labels)
        selected_label = next((label for run_id, label in options if run_id == selected_run_id), "")
        if selected_label and self.ubs_compare_run_id.get() != selected_label:
            self.ubs_compare_run_id.set(selected_label)

    def _refresh_ubs_comparison_diff(self) -> None:
        if hasattr(self, "ubs_compare_diff_tree"):
            for item in self.ubs_compare_diff_tree.get_children():
                self.ubs_compare_diff_tree.delete(item)
        paths = self._selected_ubs_compare_paths()
        if not paths:
            self.ubs_compare_detail.set("Selecciona un resultado para comparar contra su seed.")
            return
        seed_path = Path(paths.get("seed", "")).expanduser()
        set_path = Path(paths.get("set", "")).expanduser()
        if not seed_path.exists() or not set_path.exists():
            self.ubs_compare_detail.set("No existe el seed o el set aceptado en disco.")
            return
        seed_values = self._read_set_values_for_compare(seed_path)
        set_values = self._read_set_values_for_compare(set_path)
        changed = []
        for key in sorted(set(seed_values) | set(set_values)):
            seed_value = seed_values.get(key, "(faltante)")
            set_value = set_values.get(key, "(faltante)")
            if seed_value != set_value:
                changed.append((key, seed_value, set_value))
        mutated = [key for key in paths.get("mutated", "").split(";") if key]
        mutated_hint = f" | mutados por agente: {', '.join(mutated[:8])}" if mutated else ""
        self.ubs_compare_detail.set(
            f"{len(changed)} diferencias | Seed: {self._short_filename(seed_path.name)} | "
            f"Resultado: {self._short_filename(set_path.name)}{mutated_hint}"
        )
        if not hasattr(self, "ubs_compare_diff_tree"):
            return
        for key, seed_value, set_value in changed:
            self.ubs_compare_diff_tree.insert("", "end", values=(key, seed_value, set_value))

    def _ubs_compare_rows_for_report(self) -> tuple[int, list[sqlite3.Row]]:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return 0, []
        conn = sqlite3.connect(memory_path, timeout=1.0)
        conn.row_factory = sqlite3.Row
        try:
            run_options = self._ubs_compare_run_options(conn)
            run_id = self._selected_ubs_compare_run_id(run_options)
            if run_id <= 0:
                return 0, []
            rows = conn.execute(
                """
                select *
                from candidates
                where run_id = ? and status in ('accepted', 'rejected')
                order by
                    case when status = 'accepted' then 0 else 1 end,
                    score desc,
                    id desc
                """,
                (run_id,),
            ).fetchall()
            return run_id, rows
        finally:
            conn.close()

    def _set_diff_rows(self, seed_path: Path, set_path: Path) -> list[tuple[str, str, str]]:
        seed_values = self._read_set_values_for_compare(seed_path)
        set_values = self._read_set_values_for_compare(set_path)
        changed: list[tuple[str, str, str]] = []
        for key in sorted(set(seed_values) | set(set_values)):
            seed_value = seed_values.get(key, "(faltante)")
            set_value = set_values.get(key, "(faltante)")
            if seed_value != set_value:
                changed.append((key, seed_value, set_value))
        return changed

    def _generate_ubs_compare_report(self) -> None:
        try:
            run_id, rows = self._ubs_compare_rows_for_report()
        except sqlite3.Error as exc:
            self._show_error("No se pudo generar reporte UBS", str(exc))
            return
        if not rows:
            messagebox.showinfo("Reporte UBS", "No hay resultados puntuados para reportar.")
            return

        output_dir = BASE_DIR / "outputs" / "ubs_compare"
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / f"ubs_seed_compare_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"

        summary_rows: list[str] = []
        detail_blocks: list[str] = []
        total_changes = 0
        for index, row in enumerate(rows, start=1):
            metrics = self._parse_ubs_metrics(row["metrics_json"])
            seed_path = Path(row["seed_path"])
            set_path = Path(row["set_path"])
            if seed_path.exists() and set_path.exists():
                changes = self._set_diff_rows(seed_path, set_path)
                missing_note = ""
            else:
                changes = []
                missing_note = "Archivo seed o aceptado no encontrado"
            total_changes += len(changes)
            mutated = [key for key in str(row["mutated_keys"] or "").split(";") if key]
            summary_rows.append(
                "<tr>"
                f"<td>{index}</td>"
                f"<td>{html.escape(str(row['run_id']))}</td>"
                f"<td>{html.escape(str(row['generation']))}</td>"
                f"<td>{html.escape(self._format_ubs_status(str(row['status'] or '')))}</td>"
                f"<td>{html.escape(str(row['target_symbol'] or row['symbol']))}</td>"
                f"<td>{html.escape(str(row['period']))}</td>"
                f"<td>{html.escape(self._format_ubs_number(row['score']))}</td>"
                f"<td>{html.escape(self._format_ubs_number(metrics.get('net_profit')))}</td>"
                f"<td>{html.escape(self._format_ubs_number(metrics.get('profit_factor')))}</td>"
                f"<td>{html.escape(self._format_ubs_number(metrics.get('drawdown_pct')))}</td>"
                f"<td>{len(changes)}</td>"
                f"<td>{html.escape(set_path.name)}</td>"
                f"<td>{html.escape(seed_path.name)}</td>"
                "</tr>"
            )
            diff_rows = "\n".join(
                "<tr>"
                f"<td>{html.escape(key)}</td>"
                f"<td>{html.escape(seed_value)}</td>"
                f"<td>{html.escape(set_value)}</td>"
                "</tr>"
                for key, seed_value, set_value in changes
            )
            if not diff_rows:
                diff_rows = f"<tr><td colspan='3'>{html.escape(missing_note or 'Sin diferencias')}</td></tr>"
            detail_blocks.append(
                "<details>"
                f"<summary>#{index} {html.escape(self._format_ubs_status(str(row['status'] or '')))} | "
                f"{html.escape(str(row['target_symbol'] or row['symbol']))} "
                f"{html.escape(str(row['period']))} | score {html.escape(self._format_ubs_number(row['score']))} "
                f"| cambios {len(changes)} | {html.escape(set_path.name)}</summary>"
                f"<p><b>Seed:</b> {html.escape(str(seed_path))}<br>"
                f"<b>Set:</b> {html.escape(str(set_path))}<br>"
                f"<b>Mutados por agente:</b> {html.escape(', '.join(mutated) if mutated else '-')}</p>"
                "<table><thead><tr><th>Parametro</th><th>Seed</th><th>Set</th></tr></thead>"
                f"<tbody>{diff_rows}</tbody></table>"
                "</details>"
            )

        accepted = sum(1 for row in rows if row["status"] == "accepted")
        rejected = sum(1 for row in rows if row["status"] == "rejected")

        html_text = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>UBS Seed Compare</title>"
            "<style>"
            "body{font-family:Segoe UI,Arial,sans-serif;background:#0f172a;color:#e5e7eb;margin:24px;}"
            "h1{margin:0 0 8px;font-size:24px;} h2{margin-top:28px;}"
            ".meta{color:#a8b3c7;margin-bottom:18px;}"
            "table{border-collapse:collapse;width:100%;margin:12px 0;background:#111827;}"
            "th,td{border:1px solid #334155;padding:6px 8px;font-size:12px;vertical-align:top;}"
            "th{background:#243247;color:#dbeafe;} tr:nth-child(even){background:#172033;}"
            "details{border:1px solid #334155;border-radius:6px;padding:10px;margin:10px 0;background:#111827;}"
            "summary{cursor:pointer;font-weight:600;color:#86efac;} p{color:#cbd5e1;font-size:13px;}"
            "</style></head><body>"
            "<h1>UBS comparacion resultados contra seed</h1>"
            f"<div class='meta'>Generado: {html.escape(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))} | "
            f"run #{run_id} | resultados: {len(rows)} | aceptados: {accepted} | rechazados: {rejected} | "
            f"cambios totales: {total_changes}</div>"
            "<h2>Resumen</h2>"
            "<table><thead><tr>"
            "<th>#</th><th>Run</th><th>Gen</th><th>Estado</th><th>Symbol</th><th>TF</th><th>Score</th>"
            "<th>Net</th><th>PF</th><th>DD %</th><th>Cambios</th><th>Set</th><th>Seed</th>"
            "</tr></thead><tbody>"
            + "\n".join(summary_rows)
            + "</tbody></table><h2>Detalle por set</h2>"
            + "\n".join(detail_blocks)
            + "</body></html>"
        )
        report_path.write_text(html_text, encoding="utf-8")
        self.status_text.set(f"Reporte UBS generado: {report_path.name}")
        self._open_local_file(report_path)

    def _selected_ubs_compare_paths(self) -> dict[str, str] | None:
        if not hasattr(self, "ubs_compare_sets_tree"):
            return None
        selected = self.ubs_compare_sets_tree.selection()
        if not selected:
            return None
        return self.ubs_compare_paths.get(selected[0])

    def _read_set_values_for_compare(self, path: Path) -> dict[str, str]:
        text = ""
        for encoding in ("utf-8-sig", "utf-16", "cp1252"):
            try:
                text = path.read_text(encoding=encoding)
                break
            except UnicodeError:
                continue
        if not text:
            text = path.read_text(errors="replace")
        values: dict[str, str] = {}
        for line in text.splitlines():
            if "=" not in line or line.lstrip().startswith(";"):
                continue
            key, raw_value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            values[key] = raw_value.split("||", 1)[0].strip()
        return values

    def _selected_ubs_compare_path(self, kind: str) -> Path | None:
        paths = self._selected_ubs_compare_paths()
        if not paths:
            return None
        raw_path = paths.get(kind, "")
        return Path(raw_path).expanduser() if raw_path else None

    def _open_selected_ubs_compare_seed(self) -> None:
        path = self._selected_ubs_compare_path("seed")
        if path is None:
            messagebox.showinfo("Agente UBS", "Selecciona un resultado primero.")
            return
        self._open_local_file(path)

    def _open_selected_ubs_compare_set(self) -> None:
        path = self._selected_ubs_compare_path("set")
        if path is None:
            messagebox.showinfo("Agente UBS", "Selecciona un resultado primero.")
            return
        self._open_local_file(path)

    def _parse_ubs_metrics(self, raw: str | None) -> dict[str, object]:
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _format_ubs_number(self, value: object, decimals: int = 2) -> str:
        if value in (None, ""):
            return ""
        try:
            return f"{float(value):.{decimals}f}"
        except (TypeError, ValueError):
            return str(value)

    def _format_ubs_int(self, value: object) -> str:
        if value in (None, ""):
            return ""
        try:
            return str(int(float(value)))
        except (TypeError, ValueError):
            return str(value)

    def _format_ubs_status(self, status: str) -> str:
        labels = {
            "accepted": "aceptado",
            "rejected": "rechazado",
            "generated": "generado",
            "no_report": "sin reporte",
            "parse_error": "parse error",
            "report_mismatch": "mismatch reporte",
            "pending": "pendiente",
            "sin_evaluar": "sin evaluar",
        }
        return labels.get(status, status or "-")

    def _ubs_result_tag(self, status: str) -> str:
        if status == "accepted":
            return "accepted"
        if status in {"rejected", "parse_error", "report_mismatch"}:
            return "rejected"
        return "pending"

    def _selected_ubs_result_path(self, kind: str) -> Path | None:
        info = self._selected_ubs_result_info()
        if not info:
            return None
        raw_path = info.get(kind, "")
        return Path(raw_path).expanduser() if raw_path else None

    def _selected_ubs_result_info(self) -> dict[str, str]:
        if not hasattr(self, "ubs_results_tree"):
            return {}
        selected = self.ubs_results_tree.selection()
        if not selected:
            return {}
        return self.ubs_result_paths.get(selected[0], {})

    def _open_ubs_output_dir(self) -> None:
        output_dir = Path(self.ubs_generation_output.get().strip() or str(BASE_DIR / "outputs" / "ubs_agent")).expanduser()
        if not output_dir.exists():
            messagebox.showinfo("Agente UBS", f"No existe la carpeta:\n{output_dir}")
            return
        subprocess.Popen(["explorer", str(output_dir)])

    def _open_selected_ubs_set(self) -> None:
        path = self._selected_ubs_result_path("set")
        if path is None:
            messagebox.showinfo("Agente UBS", "Selecciona un resultado primero.")
            return
        self._open_local_file(path)

    def _open_selected_ubs_report(self) -> None:
        path = self._selected_ubs_result_path("report")
        if path is None:
            messagebox.showinfo("Agente UBS", "Ese resultado no tiene reporte asociado.")
            return
        self._open_local_file(path)

    def _retry_selected_ubs_mismatch(self) -> None:
        info = self._selected_ubs_result_info()
        if not info:
            messagebox.showinfo("Agente UBS", "Selecciona un resultado primero.")
            return
        if info.get("status") != "report_mismatch":
            messagebox.showinfo("Agente UBS", "Esta accion solo aplica a filas con estado mismatch reporte.")
            return
        candidate_id = info.get("id", "").strip()
        set_path = Path(info.get("set", "")).expanduser()
        if not candidate_id:
            messagebox.showinfo("Agente UBS", "La fila seleccionada no tiene candidate id.")
            return
        if not set_path.exists():
            messagebox.showinfo("Agente UBS", f"No existe el set:\n{set_path}")
            return
        try:
            args = [
                "--memory", str(self._ubs_memory_path()),
                "--template", self.template_path.get(),
                "--retry-candidate-id", candidate_id,
                "--delay", str(self.delay.get()),
            ]
            if self.multiterminal_enabled.get():
                args.extend(self._multiterminal_args(require_ubs=True))
            else:
                args.extend(["--expert", self._required_ubs_ex5_file()])
            args.extend(self._ubs_score_args())
            if not self.multiterminal_enabled.get():
                if self.mt5_path.get().strip():
                    args.extend(["--mt5-path", self.mt5_path.get()])
                if self.mt5_data_root.get().strip():
                    args.extend(["--data-dir", self.mt5_data_root.get()])
            if self.symbol_map_enabled.get() and self.symbol_map.get().strip():
                args.extend(["--symbol-map", self.symbol_map.get().strip()])
        except Exception as exc:
            self._show_error("No se pudo preparar retry mismatch", str(exc))
            return

        details = [
            "Accion: Reprobar mismatch UBS",
            f"Candidate: #{candidate_id}",
            f"Objetivo: {info.get('symbol', '')} {info.get('period', '')}",
            f"Set: {set_path.name}",
            "Backtests previstos: 1",
        ]
        details.extend(self._multiterminal_execution_details())
        if self._confirm_execution_start("Confirmar retry mismatch", 1, details):
            self._run_script("ubs_agent.py", args)

    def _retry_visible_ubs_run_mismatches(self) -> None:
        try:
            run_id = self._visible_ubs_run_id()
            if run_id <= 0:
                messagebox.showinfo("Agente UBS", "No hay run visible para reprobar.")
                return
            mismatch_count = self._count_ubs_run_mismatches(run_id)
            if mismatch_count <= 0:
                messagebox.showinfo("Agente UBS", f"Run #{run_id} no tiene mismatch pendientes.")
                return
            args = [
                "--memory", str(self._ubs_memory_path()),
                "--template", self.template_path.get(),
                "--retry-run-id", str(run_id),
                "--retry-mismatch-run",
                "--delay", str(self.delay.get()),
            ]
            if self.multiterminal_enabled.get():
                args.extend(self._multiterminal_args(require_ubs=True))
            else:
                args.extend(["--expert", self._required_ubs_ex5_file()])
            args.extend(self._ubs_score_args())
            if not self.multiterminal_enabled.get():
                if self.mt5_path.get().strip():
                    args.extend(["--mt5-path", self.mt5_path.get()])
                if self.mt5_data_root.get().strip():
                    args.extend(["--data-dir", self.mt5_data_root.get()])
            if self.symbol_map_enabled.get() and self.symbol_map.get().strip():
                args.extend(["--symbol-map", self.symbol_map.get().strip()])
        except Exception as exc:
            self._show_error("No se pudo preparar retry de run", str(exc))
            return

        details = [
            "Accion: Reprobar mismatches de run UBS",
            f"Run: #{run_id}",
            f"Backtests previstos: {mismatch_count}",
            "Al terminar actualiza esas mismas filas SQLite.",
        ]
        details.extend(self._multiterminal_execution_details())
        if self._confirm_execution_start("Confirmar retry run mismatch", mismatch_count, details):
            self._run_script("ubs_agent.py", args)

    def _visible_ubs_run_id(self) -> int:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return 0
        conn = sqlite3.connect(memory_path, timeout=1.0)
        try:
            row = conn.execute("select id from runs where hidden=0 order by id desc limit 1").fetchone()
            return int(row[0] or 0) if row else 0
        finally:
            conn.close()

    def _count_ubs_run_mismatches(self, run_id: int) -> int:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return 0
        conn = sqlite3.connect(memory_path, timeout=1.0)
        try:
            row = conn.execute(
                """
                select count(*) as total
                from candidates
                where run_id=? and status='report_mismatch'
                """,
                (run_id,),
            ).fetchone()
            return int(row[0] or 0) if row else 0
        finally:
            conn.close()

    def _open_local_file(self, path: Path) -> None:
        if not path.exists():
            messagebox.showinfo("Agente UBS", f"No existe el archivo:\n{path}")
            return
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except OSError:
            subprocess.Popen(["explorer", "/select,", str(path)])

    def _refresh_experts(self) -> None:
        for item in self.experts_tree.get_children() if hasattr(self, "experts_tree") else []:
            self.experts_tree.delete(item)
        experts: list[str] = []
        root = Path(self.experts_root.get()).expanduser() if self.experts_root.get().strip() else None
        if root and root.exists():
            try:
                experts = load_experts_from_dir(root)
            except OSError:
                experts = []
        if hasattr(self, "experts_tree"):
            for i, expert in enumerate(experts):
                tag = "odd" if i % 2 else "even"
                self.experts_tree.insert("", "end", values=(expert,), tags=(tag,))
        self.experts_count.set(f"{len(experts)}")

    def _refresh_reports(self) -> None:
        if hasattr(self, "reports_tree"):
            for item in self.reports_tree.get_children():
                self.reports_tree.delete(item)
        reports = sorted(REPORT_DIR.glob("*"), key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
        files = [path for path in reports if path.is_file() and path.suffix.lower() == ".htm"]
        from datetime import datetime as _dt
        if hasattr(self, "reports_tree"):
            for i, path in enumerate(files[:200]):
                size_kb = max(1, round(path.stat().st_size / 1024))
                date = _dt.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                tag = "odd" if i % 2 else "even"
                self.reports_tree.insert("", "end", values=(path.name, date, size_kb), tags=(tag,))
        self.reports_count.set(f"{len(files)}")

    def _refresh_last_log(self) -> None:
        candidates = [path for path in LOG_DIR.glob("*.log") if path.is_file()]
        if not candidates:
            self.last_log_text.set("Sin log")
            return
        latest = max(candidates, key=lambda path: path.stat().st_mtime)
        self.last_log_text.set(latest.name)

    def _compile_args(self) -> list[str]:
        source_dir, source_file = self._compile_source_selection()
        args = ["--source-dir", source_dir]
        if not self.recursive.get():
            args.extend(["--source-file", source_file])
        if self.metaeditor_path.get().strip():
            args.extend(["--metaeditor-path", self.metaeditor_path.get()])
        if self.mt5_path.get().strip():
            args.extend(["--mt5-path", self.mt5_path.get()])
        if self.recursive.get():
            args.append("--recursive")
        return args

    def _multiterminal_args(self, *, require_ubs: bool = False) -> list[str]:
        if not self.multiterminal_enabled.get():
            return []
        errors = self._validate_multiterminal_errors(require_ubs=require_ubs)
        if errors:
            details = "\n".join(f"- {item}" for item in errors[:12])
            if len(errors) > 12:
                details += f"\n- ... y {len(errors) - 12} mas"
            raise ValueError(f"Configuracion multiterminal invalida:\n{details}")
        self._write_ui_settings()
        return [
            "--multi-terminal",
            "--terminals-config",
            str(UI_SETTINGS_FILE),
            "--max-workers",
            str(self._multiterminal_worker_limit()),
        ]

    def _multiterminal_execution_details(self) -> list[str]:
        if not self.multiterminal_enabled.get():
            return ["Multiterminal: no"]
        active = len(self._active_multiterminal_profiles())
        workers = min(self._multiterminal_worker_limit(), active) if active else 0
        return [
            "Multiterminal: si",
            f"Terminales activas: {active}",
            f"Workers: {workers}",
        ]

    def _count_backtests(self) -> tuple[int, str]:
        if not self.recursive.get():
            _source_dir, source_file = self._compile_source_selection()
            return 1, source_file
        root = self.experts_root.get().strip()
        if not root and self.multiterminal_enabled.get():
            active = self._active_multiterminal_profiles()
            root = str(active[0].get("experts_root") or "") if active else ""
        if not root:
            raise ValueError("Indica la carpeta .ex5 o configura al menos una terminal activa con MQL5\\Experts.")
        experts = load_experts_from_dir(Path(root).expanduser(), recursive=True)
        return len(experts), root

    def _backtest_args(self) -> list[str]:
        args = ["--template", self.template_path.get(), "--delay", str(self.delay.get())]
        if self.symbol_suffix_enabled.get() and self.symbol_suffix.get().strip():
            args.extend(["--symbol-suffix", self.symbol_suffix.get().strip()])
        if self.symbol_map_enabled.get() and self.symbol_map.get().strip():
            args.extend(["--symbol-map", self.symbol_map.get().strip()])
        if not self.recursive.get():
            source_dir, source_file = self._compile_source_selection()
            args.extend(["--experts-dir", self._experts_dir_for_single_file(source_dir)])
            args.extend(["--expert", str(Path(source_file).with_suffix(".ex5"))])
        elif self.experts_root.get().strip():
            args.extend(["--experts-dir", self.experts_root.get()])
        if self.mt5_path.get().strip():
            args.extend(["--mt5-path", self.mt5_path.get()])
        if self.mt5_data_root.get().strip():
            args.extend(["--data-dir", self.mt5_data_root.get()])
        if self.recursive.get():
            args.append("--recursive")
        args.extend(self._multiterminal_args(require_ubs=False))
        return args

    def _count_compile_sources(self) -> tuple[int, str]:
        source_dir, source_file = self._compile_source_selection()
        if self.recursive.get():
            files = sorted(Path(source_dir).expanduser().glob("*.mq5"))
            return len([path for path in files if path.is_file()]), source_dir
        return 1, source_file

    def _count_ubs_tests(self) -> tuple[int, str]:
        if self.recursive.get():
            set_dir = self.set_files_root.get().strip()
            if not set_dir:
                raise ValueError("Indica la carpeta .set antes de ejecutar Tester UBS en modo recursivo.")
            files = load_set_files(Path(set_dir).expanduser(), None, recursive=True)
            return len(files), set_dir
        return 1, self._required_ubs_set_file()

    def _ubs_generator_source_dir(self) -> Path:
        set_dir = self.set_files_root.get().strip() or str(BASE_DIR / "sets" / "ubs_ready")
        source_dir = Path(set_dir).expanduser()
        if not source_dir.exists() or not source_dir.is_dir():
            raise ValueError(f"No existe la carpeta de seeds UBS: {source_dir}")
        return source_dir

    def _count_ubs_generations(self) -> tuple[int, str]:
        source_dir = self._ubs_generator_source_dir()
        files = load_set_files(source_dir, None, recursive=True)
        if not files:
            return 0, str(source_dir)
        return self._planned_ubs_generation_total(len(files)), str(source_dir)

    def _planned_ubs_generation_total(
        self,
        seed_files: int,
        *,
        generations: int | None = None,
        variants: int | None = None,
        max_seeds: int | None = None,
    ) -> int:
        generations = max(0, int(self.ubs_generation_count.get() if generations is None else generations))
        variants = max(0, int(self.ubs_variants_per_seed.get() if variants is None else variants))
        max_seeds = max(0, int(self.ubs_max_seeds.get() if max_seeds is None else max_seeds))
        seed_count = seed_files if max_seeds == 0 else min(seed_files, max_seeds)
        total = 0
        current = seed_count
        for _ in range(generations):
            produced = current * variants
            total += produced
            current = produced if max_seeds == 0 else min(produced, max_seeds)
        return total

    def _count_ubs_continuation_generations(self) -> tuple[int, str]:
        info = self._ubs_continuation_info()
        if not info.get("available"):
            raise ValueError(str(info.get("message") or "No hay iteracion UBS pendiente para continuar."))
        total = self._planned_ubs_generation_total(
            int(info["seed_count"]),
            generations=int(info["remaining"]),
            variants=int(info["variants_per_seed"]),
            max_seeds=int(info["max_seeds"]),
        )
        pending_count = int(info.get("pending_count") or 0)
        total += pending_count
        if pending_count:
            target = f"memoria run #{info['run_id']} gen {info['pending_generation']} sin backtest -> luego faltan {info['remaining']}"
        else:
            target = f"memoria run #{info['run_id']} gen {info['latest_generation']} -> faltan {info['remaining']}"
        return total, target

    def _score_float(self, variable: tk.StringVar, label: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
        raw = variable.get().strip().replace(",", ".")
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(f"{label} debe ser numerico.") from exc
        if minimum is not None and value < minimum:
            raise ValueError(f"{label} no puede ser menor que {minimum}.")
        if maximum is not None and value > maximum:
            raise ValueError(f"{label} no puede ser mayor que {maximum}.")
        return value

    def _ubs_score_args(self) -> list[str]:
        min_net_profit = self._score_float(self.ubs_pass_min_net_profit, "Profit neto min")
        min_profit_factor = self._score_float(self.ubs_pass_min_profit_factor, "Profit factor min", minimum=0)
        max_drawdown_pct = self._score_float(self.ubs_pass_max_drawdown_pct, "DD max %", minimum=0)
        min_recovery_factor = self._score_float(self.ubs_pass_min_recovery_factor, "Recovery min")
        min_trades = int(self.ubs_pass_min_trades.get())
        if min_trades < 0:
            raise ValueError("Trades min no puede ser menor que 0.")
        return [
            "--min-net-profit", str(min_net_profit),
            "--min-profit-factor", str(min_profit_factor),
            "--min-trades", str(min_trades),
            "--max-drawdown-pct", str(max_drawdown_pct),
            "--min-recovery-factor", str(min_recovery_factor),
        ]

    def _confirm_execution_start(self, title: str, total: int, details: list[str]) -> bool:
        if total <= 0:
            messagebox.showwarning(title, "No hay elementos para ejecutar.")
            return False

        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.configure(bg=COLORS["panel"])

        result = {"start": False}
        body = tk.Frame(dialog, bg=COLORS["panel"], padx=22, pady=18)
        body.grid(row=0, column=0, sticky="nsew")
        tk.Label(
            body,
            text=f"Se van a ejecutar {total} elemento(s) en total.",
            bg=COLORS["panel"], fg=COLORS["text"],
            font=("Segoe UI", 11, "bold"),
            anchor="w", justify="left",
        ).grid(row=0, column=0, sticky="ew")
        detail_text = "\n".join(details)
        tk.Label(
            body,
            text=detail_text,
            bg=COLORS["panel"], fg=COLORS["muted"],
            font=("Segoe UI", 9),
            anchor="w", justify="left",
            wraplength=520,
        ).grid(row=1, column=0, sticky="ew", pady=(10, 16))

        buttons = tk.Frame(body, bg=COLORS["panel"])
        buttons.grid(row=2, column=0, sticky="e")

        def start() -> None:
            result["start"] = True
            dialog.destroy()

        def cancel() -> None:
            dialog.destroy()

        ttk.Button(buttons, text="Cancelar", command=cancel).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(buttons, text="Empezar", style="Primary.TButton", command=start).grid(row=0, column=1)
        dialog.bind("<Escape>", lambda _event: cancel())
        dialog.bind("<Return>", lambda _event: start())
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - dialog.winfo_width()) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - dialog.winfo_height()) // 2)
        dialog.geometry(f"+{x}+{y}")
        dialog.wait_window()
        return result["start"]

    def _run_compile(self) -> None:
        try:
            args = self._compile_args()
            total, target = self._count_compile_sources()
        except Exception as exc:
            self._show_error("No se pudo preparar la compilacion", str(exc))
            return
        details = [
            f"Accion: Compilar .mq5",
            f"Modo: {'recursivo' if self.recursive.get() else 'archivo unico'}",
            f"Origen: {target}",
            f"Total compilaciones: {total}",
        ]
        if self._confirm_execution_start("Confirmar compilacion", total, details):
            self._run_script("compile_mq5.py", args)

    def _run_backtests(self) -> None:
        try:
            args = self._backtest_args()
            total, target = self._count_backtests()
        except Exception as exc:
            self._show_error("No se pudo preparar backtests", str(exc))
            return
        details = [
            "Accion: Ejecutar backtests",
            f"Modo: {'recursivo' if self.recursive.get() else 'archivo unico'}",
            f"Origen: {target}",
            f"Backtests: {total}",
        ]
        details.extend(self._multiterminal_execution_details())
        if self._confirm_execution_start("Confirmar backtests", total, details):
            self._run_script("run_tests.py", args)

    def _run_full_flow(self) -> None:
        try:
            args = self._full_flow_args()
            compile_total, target = self._count_compile_sources()
            total = compile_total * 2
        except Exception as exc:
            self._show_error("No se pudo preparar el flujo completo", str(exc))
            return
        details = [
            "Accion: Compilar y backtest",
            f"Modo: {'recursivo' if self.recursive.get() else 'archivo unico'}",
            f"Origen: {target}",
            f"Compilaciones previstas: {compile_total}",
            f"Backtests previstos: {compile_total}",
        ]
        if self._confirm_execution_start("Confirmar flujo completo", total, details):
            self._run_script("compile_and_backtest.py", args)

    def _experts_dir_for_single_file(self, source_dir: str) -> str:
        source_path = Path(source_dir).expanduser()
        parts = [part.lower() for part in source_path.parts]
        if "mql5" in parts and "experts" in parts:
            return str(source_path)

        data_root = self.mt5_data_root.get().strip()
        if data_root:
            return str(Path(data_root).expanduser() / "MQL5" / "Experts")

        if self.experts_root.get().strip():
            return self.experts_root.get()

        return str(source_path)

    def _ubs_tester_args(self) -> list[str]:
        args = [
            "--template", self.template_path.get(),
            "--delay", str(self.delay.get()),
            "--infer-tester-from-set",
        ]
        if self.multiterminal_enabled.get():
            args.extend(self._multiterminal_args(require_ubs=True))
        else:
            args.extend(["--expert", self._required_ubs_ex5_file()])
        if self.recursive.get():
            set_dir = self.set_files_root.get().strip()
            if not set_dir:
                raise ValueError("Indica la carpeta .set antes de ejecutar Tester UBS en modo recursivo.")
            args.extend(["--set-dir", set_dir])
            args.append("--recursive")
        else:
            set_file = self._required_ubs_set_file()
            args.extend(["--set-file", set_file])
        if self.symbol_suffix_enabled.get() and self.symbol_suffix.get().strip():
            args.extend(["--symbol-suffix", self.symbol_suffix.get().strip()])
        if self.symbol_map_enabled.get() and self.symbol_map.get().strip():
            args.extend(["--symbol-map", self.symbol_map.get().strip()])
        if self.experts_root.get().strip():
            args.extend(["--experts-dir", self.experts_root.get()])
        if self.mt5_path.get().strip():
            args.extend(["--mt5-path", self.mt5_path.get()])
        if self.mt5_data_root.get().strip():
            args.extend(["--data-dir", self.mt5_data_root.get()])
        return args

    def _run_ubs_tester(self) -> None:
        try:
            args = self._ubs_tester_args()
            total, target = self._count_ubs_tests()
            missing_symbol_sets = self._ubs_sets_without_inferred_symbol()
        except Exception as exc:
            self._show_error("No se pudo iniciar Tester UBS", str(exc))
            return
        details = [
            "Accion: Tester UBS",
            f"Modo: {'recursivo' if self.recursive.get() else 'set unico'}",
            f"Set(s): {target}",
            f"Total backtests: {total}",
        ]
        details.extend(self._multiterminal_execution_details())
        if missing_symbol_sets:
            self._warn_ubs_template_symbol_fallback(missing_symbol_sets)
        if self._confirm_execution_start("Confirmar Tester UBS", total, details):
            self._run_script("run_tests.py", args)

    def _count_ubs_seed_files(self) -> tuple[int, str]:
        source_dir = self._ubs_generator_source_dir()
        files = load_set_files(source_dir, None, recursive=True)
        return len(files), str(source_dir)

    def _ubs_seed_eval_args(self) -> list[str]:
        source_dir = self._ubs_generator_source_dir()
        output_dir = Path(self.ubs_generation_output.get().strip() or str(BASE_DIR / "outputs" / "ubs_agent"))
        args = [
            "--evaluate-seeds",
            "--source-dir", str(source_dir),
            "--output-dir", str(output_dir),
            "--memory", str(BASE_DIR / "outputs" / "ubs_memory.sqlite"),
            "--template", self.template_path.get(),
            "--delay", str(self.delay.get()),
        ]
        args.extend(self._ubs_score_args())
        if self.multiterminal_enabled.get():
            args.extend(self._multiterminal_args(require_ubs=True))
        else:
            args.extend(["--expert", self._required_ubs_ex5_file()])
            if self.mt5_path.get().strip():
                args.extend(["--mt5-path", self.mt5_path.get()])
            if self.mt5_data_root.get().strip():
                args.extend(["--data-dir", self.mt5_data_root.get()])
        if self.symbol_map_enabled.get() and self.symbol_map.get().strip():
            args.extend(["--symbol-map", self.symbol_map.get().strip()])
        return args

    def _run_ubs_seed_evaluation(self) -> None:
        try:
            args = self._ubs_seed_eval_args()
            total, target = self._count_ubs_seed_files()
        except Exception as exc:
            self._show_error("No se pudo preparar evaluacion de semillas", str(exc))
            return
        details = [
            "Accion: Evaluar semillas UBS",
            f"Carpeta seeds: {target}",
            f"Seeds detectadas: {total}",
            "Se ejecutan backtests solo para semillas nuevas, modificadas o sin score valido.",
            "Las semillas borradas quedan inactivas para los pesos.",
        ]
        details.extend(self._multiterminal_execution_details())
        if self._confirm_execution_start("Confirmar evaluacion de semillas", total, details):
            self.ubs_seed_eval_summary.set("Evaluando semillas UBS...")
            self._run_script("ubs_agent.py", args)

    def _ubs_generator_args(self, *, continue_last: bool = False) -> list[str]:
        source_dir = (
            Path(self.set_files_root.get().strip()).expanduser()
            if self.set_files_root.get().strip()
            else BASE_DIR / "sets" / "ubs_ready"
        )
        if not continue_last:
            source_dir = self._ubs_generator_source_dir()
        output_dir = Path(self.ubs_generation_output.get().strip() or str(BASE_DIR / "outputs" / "ubs_agent"))
        generations = int(self.ubs_generation_count.get())
        variants = int(self.ubs_variants_per_seed.get())
        max_seeds = int(self.ubs_max_seeds.get())
        continuation_info: dict[str, object] = {}
        if continue_last:
            continuation_info = self._ubs_continuation_info()
            if not continuation_info.get("available"):
                raise ValueError(str(continuation_info.get("message") or "No hay iteracion UBS pendiente para continuar."))
            generations = max(1, int(continuation_info["remaining"]))
            variants = int(continuation_info["variants_per_seed"])
            max_seeds = int(continuation_info["max_seeds"])
        if generations <= 0:
            raise ValueError("Generaciones UBS debe ser mayor que 0.")
        if variants <= 0:
            raise ValueError("Variantes por set debe ser mayor que 0.")
        if max_seeds < 0:
            raise ValueError("Max seeds/gen no puede ser negativo.")
        args = [
            "--source-dir", str(source_dir),
            "--output-dir", str(output_dir),
            "--memory", str(BASE_DIR / "outputs" / "ubs_memory.sqlite"),
            "--template", self.template_path.get(),
            "--generations", str(generations),
            "--variants-per-seed", str(variants),
            "--max-seeds", str(max_seeds),
            "--delay", str(self.delay.get()),
        ]
        if continue_last:
            args.append("--continue-last-run")
        args.extend(self._ubs_score_args())
        should_execute_backtests = (
            bool(continuation_info.get("execute_backtests"))
            if continue_last
            else self.ubs_agent_execute.get()
        )
        if should_execute_backtests:
            args.append("--execute-backtests")
            if self.multiterminal_enabled.get():
                args.extend(self._multiterminal_args(require_ubs=True))
            else:
                args.extend(["--expert", self._required_ubs_ex5_file()])
                if self.mt5_path.get().strip():
                    args.extend(["--mt5-path", self.mt5_path.get()])
                if self.mt5_data_root.get().strip():
                    args.extend(["--data-dir", self.mt5_data_root.get()])
            if self.symbol_map_enabled.get() and self.symbol_map.get().strip():
                args.extend(["--symbol-map", self.symbol_map.get().strip()])
        return args

    def _run_ubs_generator(self) -> None:
        self._run_ubs_agent(continue_last=False)

    def _run_ubs_continue(self) -> None:
        self._run_ubs_agent(continue_last=True)

    def _run_ubs_agent(self, *, continue_last: bool) -> None:
        try:
            args = self._ubs_generator_args(continue_last=continue_last)
            total, target = (
                self._count_ubs_continuation_generations()
                if continue_last
                else self._count_ubs_generations()
            )
            continuation_info = self._ubs_continuation_info() if continue_last else {}
        except Exception as exc:
            self._show_error("No se pudo iniciar Agente UBS", str(exc))
            return
        pending_count = int(continuation_info.get("pending_count") or 0)
        new_sets = max(0, total - pending_count)
        shown_generations = continuation_info.get("remaining", self.ubs_generation_count.get())
        shown_variants = continuation_info.get("variants_per_seed", self.ubs_variants_per_seed.get())
        shown_max_seeds = continuation_info.get("max_seeds", self.ubs_max_seeds.get())
        shown_backtests = bool(continuation_info.get("execute_backtests")) if continue_last else self.ubs_agent_execute.get()
        details = [
            f"Accion: {'Continuar iteracion UBS' if continue_last else 'Agente UBS'}",
            f"Seeds: {target}",
            f"Generaciones nuevas restantes: {shown_generations}",
            f"Variantes por set: {shown_variants}",
            f"Max seeds/gen: {shown_max_seeds}",
            f"Backtests: {'si' if shown_backtests else 'no'}",
            f"Pass: PF>={self.ubs_pass_min_profit_factor.get().strip()} | DD<={self.ubs_pass_max_drawdown_pct.get().strip()}% | Trades>={self.ubs_pass_min_trades.get()}",
            f"Pass: Profit neto>{self.ubs_pass_min_net_profit.get().strip()} | Recovery>={self.ubs_pass_min_recovery_factor.get().strip()}",
            f"Backtests pendientes existentes: {pending_count}",
            f"Sets nuevos previstos: {new_sets}",
        ]
        if shown_backtests:
            details.extend(self._multiterminal_execution_details())
        title = "Confirmar continuacion UBS" if continue_last else "Confirmar Agente UBS"
        if self._confirm_execution_start(title, total, details):
            self._run_script("ubs_agent.py", args)

    def _ubs_set_paths(self) -> list[Path]:
        if self.recursive.get():
            set_dir = self.set_files_root.get().strip()
            if not set_dir:
                raise ValueError("Indica la carpeta .set antes de ejecutar Tester UBS en modo recursivo.")
            return load_set_files(Path(set_dir).expanduser(), None, recursive=True)

        set_file = Path(self._required_ubs_set_file()).expanduser()
        if not set_file.exists():
            raise FileNotFoundError(f"No existe el set file: {set_file}")
        return [set_file]

    def _ubs_sets_without_inferred_symbol(self) -> list[Path]:
        missing: list[Path] = []
        for set_file in self._ubs_set_paths():
            inferred = infer_tester_fields_from_set(set_file)
            if not inferred.get("Symbol", "").strip():
                missing.append(set_file)
        return missing

    def _warn_ubs_template_symbol_fallback(self, set_files: list[Path]) -> None:
        symbol_var = self.tester_vars.get("Symbol")
        template_symbol = symbol_var.get().strip() if symbol_var else ""
        template_symbol = template_symbol or "(vacio)"
        shown = "\n".join(f"- {path.name}" for path in set_files[:12])
        if len(set_files) > 12:
            shown += f"\n- ... y {len(set_files) - 12} mas"
        messagebox.showwarning(
            "Symbol no inferido",
            "No pude inferir el Symbol desde uno o mas .set.\n\n"
            "Se usara el template como esta para esos tests.\n"
            f"Symbol actual del template: {template_symbol}\n\n"
            f"Sets afectados:\n{shown}",
        )

    def _required_ubs_ex5_file(self) -> str:
        ex5_file = self.ubs_ex5_file.get().strip()
        if not ex5_file:
            raise ValueError("Archivo .ex5 UBS es obligatorio para Tester UBS.")
        if Path(ex5_file).suffix.lower() != ".ex5":
            raise ValueError("Archivo .ex5 UBS debe ser un archivo .ex5.")
        return ex5_file

    def _required_ubs_set_file(self) -> str:
        set_file = self.ubs_set_file.get().strip()
        if not set_file:
            raise ValueError("Archivo .set UBS es obligatorio cuando Recursivo esta apagado.")
        if Path(set_file).suffix.lower() != ".set":
            raise ValueError("Archivo .set UBS debe ser un archivo .set.")
        return set_file

    def _full_flow_args(self) -> list[str]:
        source_dir, source_file = self._compile_source_selection()
        args = ["--source-dir", source_dir]
        if not self.recursive.get():
            args.extend(["--source-file", source_file])
        if self.metaeditor_path.get().strip():
            args.extend(["--metaeditor-path", self.metaeditor_path.get()])
        if self.mt5_path.get().strip():
            args.extend(["--mt5-path", self.mt5_path.get()])
        if self.mt5_data_root.get().strip():
            args.extend(["--data-dir", self.mt5_data_root.get()])
        if self.template_path.get().strip():
            args.extend(["--template", self.template_path.get()])
        if self.symbol_suffix_enabled.get() and self.symbol_suffix.get().strip():
            args.extend(["--symbol-suffix", self.symbol_suffix.get().strip()])
        if self.symbol_map_enabled.get() and self.symbol_map.get().strip():
            args.extend(["--symbol-map", self.symbol_map.get().strip()])
        args.extend(["--delay", str(self.delay.get())])
        if self.recursive.get():
            args.append("--recursive")
        return args

    def _compile_source_selection(self) -> tuple[str, str]:
        compile_root = self.compile_root.get().strip()
        compile_file = self.compile_file.get().strip()
        if self.recursive.get():
            if not compile_root:
                raise ValueError("Carpeta .mq5 es obligatoria cuando Recursivo esta activado.")
            return compile_root, compile_file
        if not compile_file:
            raise ValueError(
                "Archivo .mq5 es obligatorio cuando Recursivo esta apagado. "
                "Selecciona un archivo concreto o activa Recursivo."
            )

        source_file = Path(compile_file).expanduser()
        if source_file.is_absolute():
            return str(source_file.parent), str(source_file)
        if not compile_root:
            raise ValueError("Carpeta .mq5 es obligatoria si Archivo .mq5 no es una ruta completa.")
        return compile_root, compile_file

    def _run_script(self, script_name: str, args: list[str]) -> None:
        if self.process and self.process.poll() is None:
            messagebox.showwarning("Proceso activo", "Ya hay un proceso en ejecucion.")
            return
        if self._should_block_for_running_mt5(script_name, args):
            return
        try:
            self._save_template()
            command = self._script_command(script_name, args)
            self._append_console(f"\n> {self._format_command(command)}\n", tag="debug")
            self.status_text.set(f"Ejecutando {script_name}")
            self.running_text.set("Proceso activo")
            self.active_task_text.set(self._script_label(script_name))
            self.active_task_detail.set("0%")
            self.engine_status_text.set("Engine Running")
            if hasattr(self, "term_status_text"):
                self.term_status_text.set(f"Running: {script_name}")
                self.term_status_icon.configure(fg="#ffb95f")
                self.idle_label.configure(text="RUN")
            self._progress_total = 0
            self._progress_done = 0
            self._progress_target = 4.0
            self._progress_running = True
            self._set_progress_color("accent")
            try:
                self.progress_bar.stop()
                self.progress_bar.configure(mode="determinate", maximum=100)
                self.progress_var.set(0.0)
            except Exception:
                pass
            self.stop_requested = False
            self.process = subprocess.Popen(
                command,
                cwd=BASE_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                creationflags=NO_WINDOW,
            )
        except Exception as exc:
            self.running_text.set("Sin proceso activo")
            self.status_text.set("No se pudo iniciar el proceso")
            self._show_error("No se pudo iniciar", str(exc), traceback.format_exc())
            return

        self.reader_thread = threading.Thread(target=self._read_process_output, daemon=True)
        self.reader_thread.start()

    def _should_block_for_running_mt5(self, script_name: str, args: list[str] | None = None) -> bool:
        if script_name not in {"run_tests.py", "compile_and_backtest.py", "ubs_agent.py"}:
            return False
        args = args or []
        if "--multi-terminal" in args:
            return False
        ubs_runs_backtests = (
            self.ubs_agent_execute.get()
            or "--execute-backtests" in args
            or "--evaluate-seeds" in args
            or "--retry-candidate-id" in args
            or "--retry-mismatch-run" in args
            or "--retry-mismatch-generation" in args
        )
        if script_name == "ubs_agent.py" and not ubs_runs_backtests:
            return False

        mt5_path = Path(self.mt5_path.get()).expanduser()
        running = find_matching_running_terminals(mt5_path)
        if not running:
            return False

        process_lines = "\n".join(f"PID {process['pid']}: {process['path']}" for process in running)
        messagebox.showerror(
            "MT5 ya esta abierto",
            "RoboForex MT5 ya esta abierto.\n\n"
            f"{process_lines}\n\n"
            "Cierra MT5 completamente y vuelve a ejecutar el backtest.",
        )
        self.status_text.set("Backtest cancelado: MT5 ya esta abierto")
        return True

    def _read_process_output(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.output_queue.put(line)
        code = self.process.wait()
        self.output_queue.put(("DONE", code))

    def _drain_output_queue(self) -> None:
        try:
            while True:
                item = self.output_queue.get_nowait()
                if isinstance(item, tuple) and item[0] == "DONE":
                    code = item[1]
                    self.running_text.set("Sin proceso activo")
                    self.engine_status_text.set("Engine Ready")
                    self._progress_running = False
                    try:
                        self.progress_bar.stop()
                    except Exception:
                        pass
                    current_pct = int(round(float(self.progress_var.get())))
                    if self.stop_requested:
                        self._set_progress_color("danger")
                        self.status_text.set("Proceso detenido")
                        self._append_console(f"\nProceso detenido por el usuario. Codigo: {code}\n", tag="error")
                        self.active_task_text.set("Detenido")
                        self.active_task_detail.set(f"Detenido en {current_pct}%")
                        self.stop_requested = False
                        self._refresh_all()
                        continue

                    if code == 0:
                        self._set_progress_color("accent")
                        self._progress_target = 100.0
                        try:
                            self.progress_var.set(100.0)
                        except Exception:
                            pass
                        self.active_task_text.set("Finalizado")
                        self.active_task_detail.set("100%")
                    else:
                        self._set_progress_color("danger")
                        self.active_task_text.set("Error")
                        self.active_task_detail.set(f"Fallo en {current_pct}%")
                    self.status_text.set(f"Proceso terminado con codigo {code}")
                    tag = "info" if code == 0 else "error"
                    self._append_console(f"\nProceso terminado con codigo {code}\n", tag=tag)
                    if hasattr(self, "term_status_text"):
                        self.term_status_text.set(f"Process finished with code {code}")
                        self.term_status_icon.configure(fg=COLORS["log_info"] if code == 0 else COLORS["log_error"])
                        self.idle_label.configure(text="IDLE")
                    self._refresh_all()
                    if code == 0:
                        self._notify_telegram("MT5 Autotester: proceso finalizado correctamente.")
                        messagebox.showinfo("Proceso terminado", "El proceso termino correctamente.")
                    else:
                        self._notify_telegram(f"MT5 Autotester: proceso terminado con error (codigo {code}).")
                        self._show_error(
                            "Proceso terminado con error",
                            f"El proceso termino con codigo {code}.",
                            self._console_tail(),
                        )
                else:
                    line = str(item)
                    self._append_console(line, tag=self._tag_for_line(line))
                    self._update_progress_from_line(line)
        except queue.Empty:
            pass
        self.after(120, self._drain_output_queue)

    def _stop_process(self) -> None:
        if not self.process or self.process.poll() is not None:
            self.status_text.set("No hay proceso activo")
            return
        self.stop_requested = True
        self.status_text.set("Deteniendo proceso")
        self._append_console("\nDeteniendo proceso y subprocesos...\n")
        try:
            subprocess.run(
                ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=NO_WINDOW,
            )
        except Exception:
            self.process.terminate()

    def _load_log_file(self, path: Path) -> None:
        if not path.exists():
            messagebox.showinfo("Log", f"No existe {path.name}")
            return
        self._clear_console()
        self._append_console(path.read_text(encoding="utf-8", errors="replace"))

    def _clear_console(self) -> None:
        self.console.delete("1.0", "end")

    def _append_console(self, text: str, tag: str | None = None) -> None:
        if tag:
            self.console.insert("end", text, tag)
        else:
            self.console.insert("end", text)
        self.console.see("end")

    def _update_progress_from_line(self, line: str) -> None:
        import re
        low = line.lower()
        # Total de tareas (varias variantes que emiten los scripts)
        m = re.search(r"expert advisors:\s*(\d+)", low)
        if not m:
            m = re.search(r"\.ex5 disponibles[^:]*:\s*(\d+)", low)
        if not m:
            m = re.search(r"backtests en cola:\s*(\d+)", low)
        if m:
            try:
                total = int(m.group(1))
                if total > 0:
                    self._progress_total = total
                    self._progress_done = 0
                    self._progress_target = 6.0
                    self.active_task_detail.set("0%")
            except ValueError:
                pass
            return
        # Inicio de un sub-paso: avanza un poquito hacia el siguiente checkpoint
        if low.startswith("config:") or "comando:" in low or "reporte esperado:" in low:
            total = self._progress_total or 1
            base = 100.0 * self._progress_done / total
            sub = 100.0 / total * 0.35  # ~35% del slot al detectar inicio del backtest
            self._progress_target = min(99.0, base + sub)
            return
        # Fin de un backtest/compilación → siguiente slot completo
        if "mt5 termino con codigo" in low or "compilado" in low or "compilation successful" in low:
            self._progress_done += 1
            total = self._progress_total or max(self._progress_done, 1)
            pct = min(99.0, 100.0 * self._progress_done / total)
            self._progress_target = pct
            self.active_task_detail.set(f"{int(pct)}%")
            return
        # Reportes encontrados/copiados: marca fin del slot
        if "reportes encontrados" in low or "copiado a reports" in low:
            total = self._progress_total or 1
            base = 100.0 * self._progress_done / total
            sub = 100.0 / total * 0.85
            self._progress_target = max(self._progress_target, min(99.0, base + sub))
            return
        # Frases de fin que indican casi-fin
        if "todos los backtests han terminado" in low or "dry-run terminado" in low:
            self._progress_target = 99.0

    def _set_progress_color(self, kind: str) -> None:
        try:
            style = ttk.Style(self)
            if kind == "danger":
                color = COLORS["danger"]
            else:
                color = COLORS["accent"]
            style.configure("Horizontal.TProgressbar", background=color,
                            lightcolor=color, darkcolor=color)
        except Exception:
            pass

    def _animate_progress(self) -> None:
        try:
            current = float(self.progress_var.get())
            target = float(self._progress_target)
            changed = False
            if abs(target - current) > 0.05:
                step = (target - current) * 0.15
                if self._progress_running and 0 < step < 0.35:
                    step = 0.35
                new_val = current + step
                if (step > 0 and new_val > target) or (step < 0 and new_val < target):
                    new_val = target
                new_val = max(0.0, min(100.0, new_val))
                self.progress_var.set(new_val)
                current = new_val
                changed = True
            elif self._progress_running and current < self._progress_target - 0.5:
                self.progress_var.set(current + 0.3)
                current += 0.3
                changed = True
            if changed and self._progress_running:
                self.active_task_detail.set(f"{int(round(current))}%")
        except Exception:
            pass
        self.after(60, self._animate_progress)

    def _tag_for_line(self, line: str) -> str | None:
        low = line.lower()
        if "[telegram]" in low:
            return "telegram"
        if "error" in low or "fallo" in low or "exception" in low or "traceback" in low:
            return "error"
        if "terminado" in low or "ok" in low or "exito" in low or "completed" in low or "correctamente" in low:
            return "info"
        return None

    def _script_label(self, script_name: str) -> str:
        labels = {
            "compile_mq5.py": "Compilando .mq5",
            "run_tests.py": "Ejecutando backtests",
            "compile_and_backtest.py": "Compilando y backtesteando",
            "ubs_generate_sets.py": "Generando sets UBS",
            "ubs_agent.py": "Agente UBS",
        }
        return labels.get(script_name, script_name)

    def _format_command(self, command: list[str]) -> str:
        return " ".join(f'"{part}"' if " " in part else part for part in command)

    def _script_command(self, script_name: str, args: list[str]) -> list[str]:
        if getattr(sys, "frozen", False):
            exe_path = BASE_DIR / Path(script_name).with_suffix(".exe")
            return [str(exe_path), *args]
        return [sys.executable, str(BASE_DIR / script_name), *args]

    def _console_tail(self, max_chars: int = 4000) -> str:
        text = self.console.get("1.0", "end").strip()
        if len(text) <= max_chars:
            return text
        return text[-max_chars:]

    def _show_error(self, title: str, message: str, details: str = "") -> None:
        full_message = message.strip()
        if details.strip():
            full_message = f"{full_message}\n\nDetalles:\n{details.strip()}"
        messagebox.showerror(title, full_message)


def main() -> int:
    app = MT5AutotesterUI()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
