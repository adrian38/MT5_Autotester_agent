import configparser
import queue
import subprocess
import sys
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from manager_node_lifecycle import EmbeddedManagerNode, relaunch_application
from ui.dashboard_logic import DashboardLogicMixin
from ui.dashboard_view import DashboardViewMixin
from ui.files_logic import FilesLogicMixin
from ui.files_view import FilesViewMixin
from ui.multiterminal_logic import MultiterminalLogicMixin
from ui.multiterminal_view import MultiterminalViewMixin
from ui.portfolio_logic import PortfolioLogicMixin
from ui.portfolio_view import PortfolioViewMixin
from ui.ubs_portfolio_logic import UBSPortfolioLogicMixin
from ui.ubs_portfolio_view import UBSPortfolioViewMixin
from ui.ubs_monthly_portfolio_logic import UBSMonthlyPortfolioLogicMixin
from ui.ubs_monthly_portfolio_view import UBSMonthlyPortfolioViewMixin
from ui.ubs_search_logic import UBSSearchLogicMixin
from ui.ubs_search_view import UBSSearchViewMixin
from ui.ubs_params_logic import UBSParamsLogicMixin
from ui.ubs_params_view import UBSParamsViewMixin
from ui.settings_logic import SettingsLogicMixin
from ui.settings_view import SettingsViewMixin
from ui.startup_progress import StartupProgress, current as startup_progress_current
from ui.ubs_agent_logic import UBSAgentLogicMixin
from ui.ubs_agent_view import UBSAgentViewMixin
from ui.ubs_results_logic import UBSResultsLogicMixin
from ui.ubs_results_view import UBSResultsViewMixin
from ui.ubs_final_tick_logic import UBSFinalTickLogicMixin
from ui.ubs_final_tick_view import UBSFinalTickViewMixin
from ui.ubs_final_tick_6m_logic import UBSFinalTick6MLogicMixin
from ui.ubs_final_tick_6m_view import UBSFinalTick6MViewMixin
from ui.ubs_regression_logic import UBSRegressionLogicMixin
from ui.ubs_regression_view import UBSRegressionViewMixin
from ui.ubs_robustness_logic import UBSRobustnessLogicMixin
from ui.ubs_robustness_view import UBSRobustnessViewMixin
from ui.ubs_universe_logic import UBSUniverseLogicMixin
from ui.ubs_universe_view import UBSUniverseViewMixin
from ui.ubs_seeds_logic import UBSSeedsLogicMixin
from ui.ubs_seeds_view import UBSSeedsViewMixin

# Reexportados para no cambiar a los consumidores de `app_ui`.
from ui.app_base import (  # noqa: F401
    BASE_DIR,
    COLORS,
    COMPILE_ROOT_FILE,
    CONSOLE_MAX_LINES,
    DARK_COLORS,
    LIGHT_COLORS,
    LOCAL_FILE_FALLBACK_ROOTS,
    NO_WINDOW,
    OUTPUT_DRAIN_BUSY_INTERVAL_MS,
    OUTPUT_DRAIN_IDLE_INTERVAL_MS,
    OUTPUT_DRAIN_MAX_ITEMS,
    OUTPUT_DRAIN_TIME_BUDGET_SECONDS,
    OUTPUT_QUEUE_MAX_ITEMS,
    TRUE_VALUES,
    UI_SETTINGS_FILE,
    _display_margin_profile,
    _display_portfolio_type,
    _widget_bg,
    resolve_existing_local_file,
)
from ui.app_widgets import (  # noqa: F401
    RoundedButton,
    RoundedCard,
    ToggleSwitch,
    ToolTip,
    _ButtonImageCache,
    _CornerImageCache,
)
from ui.app_chrome import AppChromeMixin
from ui.app_notify import AppNotifyMixin
from ui.app_process import AppProcessMixin
from ui.app_state import AppStateMixin
from ui.app_state_portfolio import AppPortfolioStateMixin


class MT5AutotesterUI(
    AppStateMixin,
    AppPortfolioStateMixin,
    AppChromeMixin,
    AppNotifyMixin,
    AppProcessMixin,
    DashboardViewMixin,
    DashboardLogicMixin,
    FilesViewMixin,
    FilesLogicMixin,
    MultiterminalViewMixin,
    MultiterminalLogicMixin,
    PortfolioViewMixin,
    PortfolioLogicMixin,
    UBSPortfolioViewMixin,
    UBSPortfolioLogicMixin,
    UBSMonthlyPortfolioViewMixin,
    UBSMonthlyPortfolioLogicMixin,
    UBSSearchViewMixin,
    UBSSearchLogicMixin,
    SettingsViewMixin,
    SettingsLogicMixin,
    UBSAgentViewMixin,
    UBSAgentLogicMixin,
    UBSParamsViewMixin,
    UBSParamsLogicMixin,
    UBSResultsViewMixin,
    UBSResultsLogicMixin,
    UBSRobustnessViewMixin,
    UBSRobustnessLogicMixin,
    UBSFinalTickViewMixin,
    UBSFinalTickLogicMixin,
    UBSFinalTick6MViewMixin,
    UBSFinalTick6MLogicMixin,
    UBSRegressionViewMixin,
    UBSRegressionLogicMixin,
    UBSSeedsViewMixin,
    UBSSeedsLogicMixin,
    UBSUniverseViewMixin,
    UBSUniverseLogicMixin,
    tk.Tk,
):
    def __init__(self) -> None:
        super().__init__()
        self._startup_progress = StartupProgress(
            (
                "Ajustes y rutas",
                "Migracion legacy",
                "Interfaz",
                "Refresco inicial",
                "Nodo manager",
            )
        )
        self._startup_progress.advance("Ajustes y rutas")
        self.colors = COLORS
        self._rounded_button_cls = RoundedButton
        self._rounded_card_cls = RoundedCard
        self._toggle_switch_cls = ToggleSwitch
        self._tooltip_cls = ToolTip
        self.title("MT5 Autotester")
        self.geometry("1320x800")
        self.minsize(1180, 700)
        self.configure(bg=COLORS["bg"])
        self._apply_window_icon()

        self.process: subprocess.Popen[str] | None = None
        self.reader_thread: threading.Thread | None = None
        self.output_queue: queue.Queue[str | tuple[str, int]] = queue.Queue(
            maxsize=OUTPUT_QUEUE_MAX_ITEMS
        )
        self.stop_requested = False
        self._restart_requested = False

        ui_settings = self._read_ui_settings()
        self._manager_node = EmbeddedManagerNode(BASE_DIR, ui_settings)
        saved_paths = ui_settings["Paths"] if ui_settings.has_section("Paths") else {}
        saved_general = ui_settings["General"] if ui_settings.has_section("General") else {}
        saved_multi = ui_settings["Multiterminal"] if ui_settings.has_section("Multiterminal") else {}
        saved_theme = saved_general.get("theme", "light").strip().lower()
        self.theme_mode = tk.StringVar(value="dark" if saved_theme == "dark" else "light")
        self._apply_theme_palette()

        self._init_path_vars(saved_paths, saved_general)
        self._init_generation_vars(saved_general)
        self._init_score_thresholds(saved_general)
        self._init_robustness_thresholds(saved_general)
        self._init_stage_dates(saved_general)
        self._init_regression_thresholds(saved_general)
        self._init_final_tick_thresholds(saved_general)
        self._init_symbol_vars(saved_general)
        self._init_notification_vars(saved_general)
        self._init_multiterminal_vars(ui_settings, saved_multi)
        self._init_status_vars()
        self._init_portfolio_inputs(saved_general)
        self._init_portfolio_filters(saved_general)
        self._init_portfolio_state()
        self._init_monthly_portfolio_inputs(saved_general)
        self._init_monthly_portfolio_filters(saved_general)
        self._init_monthly_portfolio_margin(saved_general)
        self._init_monthly_portfolio_state()
        self._init_screen_texts()
        self._init_dashboard_state()
        self._init_table_state()

        self._startup_progress.advance("Interfaz")
        self._sync_ubs_account_paths()
        self._configure_style()
        self._build_ui()
        try:
            self._load_template()
        except Exception as exc:
            self.status_text.set("Template tester no cargado")
            self._startup_progress.note(f"AVISO: template tester no cargado ({exc})")
        self._startup_progress.advance("Refresco inicial")
        self._refresh_all()
        self._startup_progress.advance("Nodo manager")
        self._manager_node.start()
        if self._manager_node.controller is not None:
            # Reads only the subprocess handle, never Tk variables from HTTP.
            self._manager_node.controller.ui_busy = lambda: bool(self.process and self.process.poll() is None)
        self.protocol("WM_DELETE_WINDOW", self._on_app_close)
        self.after(60, self._animate_progress)
        self.after(OUTPUT_DRAIN_IDLE_INTERVAL_MS, self._drain_output_queue)
        self.after(100, self._poll_manager_restart)
        self._startup_progress.done()

    def _poll_manager_restart(self) -> None:
        if self._manager_node.consume_restart_request():
            self._restart_requested = True
            self._manager_node.stop(stop_job=False)
            self.destroy()
            return
        self.after(100, self._poll_manager_restart)

    def _on_app_close(self) -> None:
        if self._manager_node.job_running:
            close_confirmed = messagebox.askyesno(
                "Generacion remota activa",
                "Hay una generacion iniciada desde MT5 Autotester Manager.\n\n"
                "Si cierras la aplicacion, esa generacion se detendra. "
                "¿Quieres cerrar de todos modos?",
            )
            if not close_confirmed:
                return
        self._manager_node.stop(stop_job=True)
        self.destroy()

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
            ("ubs_robustness", self._refresh_ubs_robustness),
            ("ubs_final_tick", self._refresh_ubs_final_tick),
            ("ubs_final_tick_6m", self._refresh_ubs_final_tick_6m),
            ("ubs_regression", self._refresh_ubs_regression),
            ("ubs_history", self._refresh_ubs_history),
            ("ubs_seed_summary", self._refresh_ubs_seed_eval_summary),
            ("ubs_seeds", self._refresh_ubs_seeds),
            ("ubs_universe", self._refresh_ubs_universe),
            ("ubs_comparison", self._refresh_ubs_comparison),
            ("ubs_continue", self._refresh_ubs_continue_state),
            ("portfolio", self._refresh_portfolio_count),
            ("ubs_portfolios", self._refresh_ubs_portfolios),
            ("ubs_monthly_portfolios", self._refresh_ubs_monthly_portfolios),
            ("last_log", self._refresh_last_log),
            ("multiterminal", self._refresh_multiterminal_tree),
        ):
            self._safe_refresh(label, callback)


def main() -> int:
    try:
        app = MT5AutotesterUI()
    except BaseException as exc:
        # Un fallo aqui deja la ventana a medias y sin traza visible: la barra
        # marca en que fase murio antes de propagar el error completo.
        progress = startup_progress_current()
        if progress is not None:
            progress.fail(exc)
        raise
    try:
        app.mainloop()
    finally:
        app._manager_node.stop(stop_job=True)
    if app._restart_requested:
        relaunch_application(Path(__file__).resolve(), BASE_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
