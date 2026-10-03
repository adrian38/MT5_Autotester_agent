"""Rutas, constantes y paleta que comparten la ventana y sus pantallas."""
from __future__ import annotations

import subprocess
import sys
import tkinter as tk
from pathlib import Path

from ubs.account import normalize_broker

BASE_DIR = Path(__file__).resolve().parents[1]

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
COMPILE_ROOT_FILE = BASE_DIR / "compile_root.txt"
UI_SETTINGS_FILE = BASE_DIR / "ui_settings.ini"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TRUE_VALUES = {"1", "true", "yes", "on", "si"}
LOCAL_FILE_FALLBACK_ROOTS = ("reports", "outputs", "sets", "configs", "logs")
OUTPUT_QUEUE_MAX_ITEMS = 5_000
OUTPUT_DRAIN_MAX_ITEMS = 200
OUTPUT_DRAIN_TIME_BUDGET_SECONDS = 0.02
OUTPUT_DRAIN_IDLE_INTERVAL_MS = 120
OUTPUT_DRAIN_BUSY_INTERVAL_MS = 1
CONSOLE_MAX_LINES = 10_000


def _display_margin_profile(raw_profile: str | None, broker: str) -> str:
    value = str(raw_profile or broker or "ROBOFOREX").strip().lower()
    if value in {"ttp", "thetradingpit", "tradingpit", "the_trading_pit"}:
        return "TTP"
    if value in {"axi", "axi trading", "axitrading", "axi_select", "axiselect"}:
        return "AXI"
    if value in {"ictrading", "ic trading", "ic", "icmarkets", "ic markets"}:
        return "ICTRADING"
    broker_value = normalize_broker(broker or value or "ROBOFOREX")
    if broker_value in {"AXI", "ICTRADING"}:
        return broker_value
    return "ROBOFOREX"


def _display_portfolio_type(raw_value: str | None) -> str:
    value = str(raw_value or "Moderado").strip()
    return {
        "Conservative": "Conservador",
        "Balanced": "Moderado",
        "Aggressive": "Agresivo",
        "Conservador": "Conservador",
        "Equilibrado": "Moderado",
        "Moderado": "Moderado",
        "Agresivo": "Agresivo",
    }.get(value, value)


def resolve_existing_local_file(path: Path, base_dir: Path = BASE_DIR) -> Path:
    if path.exists():
        return path
    candidates: list[Path] = []
    parts = path.parts
    lower_parts = [part.lower() for part in parts]
    for marker in LOCAL_FILE_FALLBACK_ROOTS:
        if marker in lower_parts:
            index = lower_parts.index(marker)
            if index < len(parts) - 1:
                candidates.append(base_dir.joinpath(*parts[index:]))
            break
    if path.name:
        candidates.extend(base_dir / folder / path.name for folder in LOCAL_FILE_FALLBACK_ROOTS)
    seen: set[str] = set()
    for candidate in candidates:
        candidate_key = str(candidate).lower()
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return path


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
