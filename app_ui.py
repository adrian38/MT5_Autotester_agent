import configparser
import os
import queue
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
        saved_theme = saved_general.get("theme", "light").strip().lower()
        self.theme_mode = tk.StringVar(value="dark" if saved_theme == "dark" else "light")
        self._apply_theme_palette()

        self.mt5_path = tk.StringVar(value=saved_paths.get("mt5_path", str(terminal_path_from_env() or find_mt5_path(None))))
        self.mt5_data_root = tk.StringVar(value=saved_paths.get("mt5_data_root", ""))
        self.metaeditor_path = tk.StringVar(
            value=saved_paths.get("metaeditor_path", str(metaeditor_path_from_env() or find_metaeditor_path(None, None)))
        )
        self.compile_root = tk.StringVar(value=saved_paths.get("compile_root", str(load_compile_root() or "")))
        self.compile_file = tk.StringVar(value=saved_paths.get("compile_file", ""))
        self.experts_root = tk.StringVar(value=saved_paths.get("experts_root", str(load_experts_root() or "")))
        self.ubs_ex5_file = tk.StringVar(value=saved_paths.get("ubs_ex5_file", ""))
        self.set_files_root = tk.StringVar(value=saved_paths.get("set_files_root", ""))
        self.ubs_set_file = tk.StringVar(value=saved_paths.get("ubs_set_file", ""))
        self.template_path = tk.StringVar(value=saved_paths.get("template_path", str(TEMPLATE_FILE)))
        self.portfolio_input = tk.StringVar(value=saved_paths.get("portfolio_input", str(REPORT_DIR)))
        self.portfolio_output = tk.StringVar(
            value=saved_paths.get("portfolio_output", str(BASE_DIR / "outputs" / "ALL_STRATEGIES.xlsx"))
        )
        self.portfolio_threshold = tk.StringVar(value=saved_general.get("portfolio_threshold", "50"))
        self.recursive = tk.BooleanVar(value=saved_general.get("recursive", "0") in {"1", "true", "yes", "on"})
        self.delay = tk.IntVar(value=self._saved_int(saved_general.get("delay"), 5))
        self.symbol_suffix_enabled = tk.BooleanVar(value=saved_general.get("symbol_suffix_enabled", "0") in {"1", "true", "yes", "on"})
        self.symbol_suffix = tk.StringVar(value=saved_general.get("symbol_suffix", ""))
        self.symbol_map_enabled = tk.BooleanVar(value=saved_general.get("symbol_map_enabled", "0") in {"1", "true", "yes", "on"})
        self.symbol_map = tk.StringVar(value=saved_general.get("symbol_map", ""))
        _tg_default = "1" if (env_value("TELEGRAM_BOT_TOKEN") and env_value("TELEGRAM_CHAT_ID")) else "0"
        self.telegram_enabled = tk.BooleanVar(value=saved_general.get("telegram_enabled", _tg_default) in {"1", "true", "yes", "on"})

        self.tester_vars: dict[str, tk.StringVar] = {}
        self.status_text = tk.StringVar(value="Listo")
        self.running_text = tk.StringVar(value="Sin proceso activo")
        self.experts_count = tk.StringVar(value="0")
        self.reports_count = tk.StringVar(value="0")
        self.portfolio_count = tk.StringVar(value="Reports encontrados: 0")
        self.portfolio_status = tk.StringVar(value="Selecciona una carpeta de reportes y genera el Excel.")
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

    def _build_ui(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        self._build_sidebar()

        content_holder = ttk.Frame(self, padding=(24, 16, 24, 12))
        content_holder.grid(row=0, column=1, rowspan=2, sticky="nsew")
        content_holder.columnconfigure(0, weight=1)
        content_holder.rowconfigure(0, weight=1)

        for key in ("panel", "portfolio", "configuracion", "archivos", "logs"):
            frame = ttk.Frame(content_holder, padding=0)
            frame.grid(row=0, column=0, sticky="nsew")
            self.section_frames[key] = frame

        self._build_dashboard(self.section_frames["panel"])
        self._build_portfolio(self.section_frames["portfolio"])
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

        self._action_card(
            actions_card, 1, 0,
            icon="< >",
            title="Compilar .mq5",
            description="Regenera los .ex5 a partir de los .mq5 del directorio fuente.",
            command=self._run_compile,
        )
        self._action_card(
            actions_card, 1, 1,
            icon="▶",
            title="Ejecutar backtests",
            description="Lanza la cola configurada contra los datos historicos.",
            command=lambda: self._run_script("run_tests.py", self._backtest_args()),
        )
        self._action_card(
            actions_card, 2, 0,
            icon="UBS",
            title="Tester UBS",
            description="Testea un solo bot usando todos los set files configurados.",
            command=self._run_ubs_tester,
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
        compile_and.grid(row=3, column=0, columnspan=2, sticky="ew", padx=20, pady=(8, 8))

        stop_btn = RoundedButton(
            actions_card,
            text="✕  DETENER PROCESO",
            bg=COLORS["danger"], hover_bg="#8a0d0d",
            font=("Segoe UI", 10, "bold"),
            radius=12, padx=16, pady=12,
            parent_bg=COLORS["panel"],
            command=self._stop_process,
        )
        stop_btn.grid(row=4, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 18))

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
            "portfolio_input": self.portfolio_input.get().strip(),
            "portfolio_output": self.portfolio_output.get().strip(),
        }
        parser["General"] = {
            "recursive": "1" if self.recursive.get() else "0",
            "delay": str(self.delay.get()),
            "symbol_suffix_enabled": "1" if self.symbol_suffix_enabled.get() else "0",
            "symbol_suffix": self.symbol_suffix.get().strip(),
            "symbol_map_enabled": "1" if self.symbol_map_enabled.get() else "0",
            "symbol_map": self.symbol_map.get().strip(),
            "telegram_enabled": "1" if self.telegram_enabled.get() else "0",
            "portfolio_threshold": self.portfolio_threshold.get().strip(),
            "theme": self.theme_mode.get(),
        }
        with UI_SETTINGS_FILE.open("w", encoding="utf-8", newline="\n") as file:
            parser.write(file, space_around_delimiters=False)

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
        self._refresh_portfolio_count()
        self._refresh_last_log()

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
            files = sorted(Path(set_dir).expanduser().glob("*.set"))
            return len([path for path in files if path.is_file()]), set_dir
        return 1, self._required_ubs_set_file()

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
        expert = self._required_ubs_ex5_file()
        args = [
            "--template", self.template_path.get(),
            "--delay", str(self.delay.get()),
            "--expert", expert,
            "--infer-tester-from-set",
        ]
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
        if self._should_block_for_running_mt5(script_name):
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

    def _should_block_for_running_mt5(self, script_name: str) -> bool:
        if script_name not in {"run_tests.py", "compile_and_backtest.py"}:
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
