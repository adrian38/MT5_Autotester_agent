import configparser
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
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from compile_mq5 import find_metaeditor_path, load_compile_root
from mt5_env import ENV_FILE, env_value, metaeditor_path_from_env, terminal_path_from_env
import telegram_notify
from run_tests import (
    EXPERTS_ROOT_FILE,
    REPORT_DIR,
    TEMPLATE_FILE,
    find_matching_running_terminals,
    find_mt5_path,
    infer_tester_fields_from_set,
    load_set_files,
    load_experts_from_dir,
    load_experts_root,
    looks_like_ubs_expert_file,
)
from ubs.set_utils import read_set_with_encoding
from ui.dashboard_logic import DashboardLogicMixin
from ui.dashboard_view import DashboardViewMixin
from ui.files_logic import FilesLogicMixin
from ui.files_view import FilesViewMixin
from ui.multiterminal_logic import MultiterminalLogicMixin
from ui.multiterminal_view import MultiterminalViewMixin
from ui.portfolio_logic import PortfolioLogicMixin
from ui.portfolio_view import PortfolioViewMixin
from ui.ubs_params_logic import UBSParamsLogicMixin
from ui.ubs_params_view import UBSParamsViewMixin
from ui.settings_logic import SettingsLogicMixin
from ui.settings_view import SettingsViewMixin
from ui.ubs_agent_logic import UBSAgentLogicMixin
from ui.ubs_agent_view import UBSAgentViewMixin
from ui.ubs_results_logic import UBSResultsLogicMixin
from ui.ubs_results_view import UBSResultsViewMixin
from ui.ubs_universe_logic import UBSUniverseLogicMixin
from ui.ubs_universe_view import UBSUniverseViewMixin
from ui.ubs_seeds_logic import UBSSeedsLogicMixin
from ui.ubs_seeds_view import UBSSeedsViewMixin
from ubs.universe import disabled_symbols_path, load_disabled_symbols, save_disabled_symbols


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


class ToolTip:
    """Simple hover tooltip for any Tkinter widget."""

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self._widget = widget
        self._text = text
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, event: tk.Event | None = None) -> None:
        if self._tip or not self._text:
            return
        x = self._widget.winfo_rootx() + 20
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._tip = tk.Toplevel(self._widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(
            self._tip,
            text=self._text,
            justify="left",
            background="#1e293b",
            foreground="#e2e8f0",
            relief="flat",
            font=("Segoe UI", 9),
            wraplength=340,
            padx=8,
            pady=5,
        )
        lbl.pack()

    def _hide(self, event: tk.Event | None = None) -> None:
        if self._tip:
            self._tip.destroy()
            self._tip = None


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


class MT5AutotesterUI(
    DashboardViewMixin,
    DashboardLogicMixin,
    FilesViewMixin,
    FilesLogicMixin,
    MultiterminalViewMixin,
    MultiterminalLogicMixin,
    PortfolioViewMixin,
    PortfolioLogicMixin,
    SettingsViewMixin,
    SettingsLogicMixin,
    UBSAgentViewMixin,
    UBSAgentLogicMixin,
    UBSParamsViewMixin,
    UBSParamsLogicMixin,
    UBSResultsViewMixin,
    UBSResultsLogicMixin,
    UBSSeedsViewMixin,
    UBSSeedsLogicMixin,
    UBSUniverseViewMixin,
    UBSUniverseLogicMixin,
    tk.Tk,
):
    def __init__(self) -> None:
        super().__init__()
        self.colors = COLORS
        self._rounded_button_cls = RoundedButton
        self._rounded_card_cls = RoundedCard
        self._toggle_switch_cls = ToggleSwitch
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
        self.ubs_seed_pass_min_net_profit = tk.StringVar(value=saved_general.get("ubs_seed_pass_min_net_profit", "0"))
        self.ubs_seed_pass_min_profit_factor = tk.StringVar(value=saved_general.get("ubs_seed_pass_min_profit_factor", "1.20"))
        self.ubs_seed_pass_min_trades = tk.IntVar(value=self._saved_int(saved_general.get("ubs_seed_pass_min_trades"), 50))
        self.ubs_seed_pass_max_drawdown_pct = tk.StringVar(value=saved_general.get("ubs_seed_pass_max_drawdown_pct", "25"))
        self.ubs_seed_pass_min_recovery_factor = tk.StringVar(value=saved_general.get("ubs_seed_pass_min_recovery_factor", "1.0"))
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
        self.ubs_weights_locked = tk.BooleanVar(value=False)
        self.ubs_params_file_label = tk.StringVar(value="Sin archivo cargado")
        self.ubs_params_desc_var = tk.StringVar(value="Selecciona un parámetro para ver su descripción")
        self.ubs_params_modified: bool = False
        self.ubs_params_data: list[dict] = []
        self.ubs_params_current_path: Path | None = None
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
        self.ubs_seed_checked: set[str] = set()
        self.ubs_universe_paths: dict[str, dict[str, str]] = {}
        self.ubs_universe_checked: set[str] = set()
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
        style.configure("TCombobox", fieldbackground=COLORS["entry_bg"], foreground=COLORS["text"],
                        background=COLORS["entry_bg"], insertcolor=COLORS["text"],
                        bordercolor=COLORS["border"], arrowcolor=COLORS["text"], padding=7)
        style.map("TCombobox",
                  fieldbackground=[("readonly", COLORS["entry_bg"]), ("disabled", COLORS["panel"])],
                  foreground=[("readonly", COLORS["text"]), ("disabled", COLORS["muted"])],
                  selectbackground=[("readonly", COLORS["entry_bg"])],
                  selectforeground=[("readonly", COLORS["text"])])
        style.configure("Treeview", background=COLORS["tree_bg"], fieldbackground=COLORS["tree_bg"],
                        foreground=COLORS["text"], rowheight=26, borderwidth=0)
        style.map("Treeview", background=[("selected", COLORS["accent"])], foreground=[("selected", "#ffffff")])
        style.configure("Treeview.Heading", background=COLORS["panel_alt"], foreground=COLORS["muted"], font=("Segoe UI", 8, "bold"), padding=(6, 4))
        style.configure("TCheckbutton", background=COLORS["panel"], foreground=COLORS["text"])
        style.configure("Panel.TCheckbutton", background=COLORS["panel"], foreground=COLORS["text"])
        style.configure("TRadiobutton", background=COLORS["panel"], foreground=COLORS["text"])
        style.configure("Panel.TRadiobutton", background=COLORS["panel"], foreground=COLORS["text"])
        style.map("TRadiobutton",
                  background=[("active", COLORS["panel"]), ("!active", COLORS["panel"])],
                  foreground=[("active", COLORS["text"]), ("!active", COLORS["text"])])
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

    def _checkbox_text(self, checked: bool) -> str:
        return "[x]" if checked else "[ ]"

    def _disabled_symbols_path(self) -> Path:
        return disabled_symbols_path(BASE_DIR)

    def _load_disabled_ubs_symbols(self) -> set[str]:
        return load_disabled_symbols(self._disabled_symbols_path())

    def _save_disabled_ubs_symbols(self, symbols: set[str]) -> None:
        save_disabled_symbols(self._disabled_symbols_path(), symbols)

    def _tree_item_from_event(self, tree: ttk.Treeview, event: tk.Event) -> tuple[str, str]:
        return tree.identify_row(event.y), tree.identify_column(event.x)

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

        for key in ("panel", "agente_ubs", "ubs_seeds", "ubs_resultados", "ubs_historico", "ubs_universo", "ubs_comparar", "ubs_params", "portfolio", "multiterminal", "configuracion", "archivos", "logs"):
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
        self._build_ubs_params(self.section_frames["ubs_params"])
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
            ("ubs_params", "UBS  Parámetros"),
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

    def _save_ubs_agent_clicked(self) -> None:
        try:
            self._write_ui_settings()
        except Exception as exc:
            self._show_error("No se pudo guardar Agente UBS", str(exc))
            return
        self.status_text.set("Configuracion Agente UBS guardada")
        messagebox.showinfo("Agente UBS", "La configuracion del Agente UBS se guardo correctamente.")

    def _save_seed_criteria_clicked(self) -> None:
        try:
            self._ubs_seed_score_args()
            self._write_ui_settings()
        except Exception as exc:
            self._show_error("No se pudieron guardar criterios Seeds", str(exc))
            return
        self.status_text.set("Criterios Seeds guardados")
        self._refresh_ubs_seeds_panel()

    def _apply_seed_criteria_clicked(self) -> None:
        try:
            self._write_ui_settings()
            args = [
                "--rescore-seeds-only",
                "--source-dir", str(self._ubs_generator_source_dir()),
                "--memory", str(BASE_DIR / "outputs" / "ubs_memory.sqlite"),
            ]
            args.extend(self._ubs_seed_score_args())
            if self.symbol_map_enabled.get() and self.symbol_map.get().strip():
                args.extend(["--symbol-map", self.symbol_map.get().strip()])
        except Exception as exc:
            self._show_error("No se pudieron aplicar criterios Seeds", str(exc))
            return
        self._run_script("ubs_agent.py", args)

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
            "ubs_seed_pass_min_net_profit": self.ubs_seed_pass_min_net_profit.get().strip(),
            "ubs_seed_pass_min_profit_factor": self.ubs_seed_pass_min_profit_factor.get().strip(),
            "ubs_seed_pass_min_trades": str(self.ubs_seed_pass_min_trades.get()),
            "ubs_seed_pass_max_drawdown_pct": self.ubs_seed_pass_max_drawdown_pct.get().strip(),
            "ubs_seed_pass_min_recovery_factor": self.ubs_seed_pass_min_recovery_factor.get().strip(),
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

    def _safe_refresh(self, label: str, callback) -> None:
        try:
            callback()
        except Exception as exc:
            self.status_text.set(f"Actualizar fallo: {label}")
            if hasattr(self, "output_console"):
                self._append_console(f"\n[Actualizar] {label}: {exc}\n", tag="error")

    def _refresh_all(self) -> None:
        for label, callback in (
            ("experts", self._refresh_experts),
            ("reports", self._refresh_reports),
            ("ubs_results", self._refresh_ubs_results),
            ("ubs_history", self._refresh_ubs_history),
            ("ubs_seed_summary", self._refresh_ubs_seed_eval_summary),
            ("ubs_seeds", self._refresh_ubs_seeds),
            ("ubs_universe", self._refresh_ubs_universe),
            ("ubs_comparison", self._refresh_ubs_comparison),
            ("ubs_continue", self._refresh_ubs_continue_state),
            ("portfolio", self._refresh_portfolio_count),
            ("last_log", self._refresh_last_log),
            ("multiterminal", self._refresh_multiterminal_tree),
        ):
            self._safe_refresh(label, callback)

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
            "no_trades": "sin operaciones",
            "disabled_symbol": "deshabilitado",
            "parse_error": "parse error",
            "report_mismatch": "mismatch reporte",
            "pending": "pendiente",
            "sin_evaluar": "sin evaluar",
        }
        return labels.get(status, status or "-")

    def _ubs_result_tag(self, status: str) -> str:
        if status == "accepted":
            return "accepted"
        if status in {"rejected", "parse_error", "report_mismatch", "no_trades"}:
            return "rejected"
        if status == "disabled_symbol":
            return "pending"
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

    def _score_args_from_vars(
        self,
        *,
        min_net_profit_var: tk.StringVar,
        min_profit_factor_var: tk.StringVar,
        min_trades_var: tk.IntVar,
        max_drawdown_pct_var: tk.StringVar,
        min_recovery_factor_var: tk.StringVar,
        context: str,
    ) -> list[str]:
        min_net_profit = self._score_float(min_net_profit_var, f"{context} profit neto min")
        min_profit_factor = self._score_float(min_profit_factor_var, f"{context} profit factor min", minimum=0)
        max_drawdown_pct = self._score_float(max_drawdown_pct_var, f"{context} DD max %", minimum=0)
        min_recovery_factor = self._score_float(min_recovery_factor_var, f"{context} recovery min")
        min_trades = int(min_trades_var.get())
        if min_trades < 0:
            raise ValueError(f"{context} trades min no puede ser menor que 0.")
        return [
            "--min-net-profit", str(min_net_profit),
            "--min-profit-factor", str(min_profit_factor),
            "--min-trades", str(min_trades),
            "--max-drawdown-pct", str(max_drawdown_pct),
            "--min-recovery-factor", str(min_recovery_factor),
        ]

    def _ubs_score_args(self) -> list[str]:
        return self._score_args_from_vars(
            min_net_profit_var=self.ubs_pass_min_net_profit,
            min_profit_factor_var=self.ubs_pass_min_profit_factor,
            min_trades_var=self.ubs_pass_min_trades,
            max_drawdown_pct_var=self.ubs_pass_max_drawdown_pct,
            min_recovery_factor_var=self.ubs_pass_min_recovery_factor,
            context="Agente UBS",
        )

    def _ubs_seed_score_args(self) -> list[str]:
        return self._score_args_from_vars(
            min_net_profit_var=self.ubs_seed_pass_min_net_profit,
            min_profit_factor_var=self.ubs_seed_pass_min_profit_factor,
            min_trades_var=self.ubs_seed_pass_min_trades,
            max_drawdown_pct_var=self.ubs_seed_pass_max_drawdown_pct,
            min_recovery_factor_var=self.ubs_seed_pass_min_recovery_factor,
            context="Seeds UBS",
        )

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


