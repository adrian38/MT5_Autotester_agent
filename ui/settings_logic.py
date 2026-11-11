from __future__ import annotations

import configparser
import sys
from pathlib import Path
from tkinter import filedialog, messagebox

from common import telegram_notify
from common.mt5_env import ENV_FILE
from run_tests import EXPERTS_ROOT_FILE, TESTER_DEFAULTS
from ubs.account import (
    BROKERS,
    default_symbol_map_for_broker,
    normalize_broker,
    symbol_map_setting_key,
)
from ui.settings_cleanup import SettingsCleanupMixin
from ui.settings_general import SettingsGeneralMixin


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent

COMPILE_ROOT_FILE = BASE_DIR / "compile_root.txt"
UI_SETTINGS_FILE = BASE_DIR / "ui_settings.ini"
TEMPLATE_FIELD_DEFAULTS = {
    "Symbol": "XAUUSD",
    "Period": "M30",
    "Model": "1",
    "FromDate": "2020.01.01",
    "ToDate": "2026.05.22",
    **TESTER_DEFAULTS,
}


class SettingsLogicMixin(SettingsGeneralMixin, SettingsCleanupMixin):
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
        existing_tester: dict[str, str] = {}
        if template.exists():
            existing = configparser.ConfigParser(interpolation=None)
            existing.optionxform = str
            existing.read(template, encoding="utf-8-sig")
            if existing.has_section("Tester"):
                existing_tester = {key: value for key, value in existing["Tester"].items()}
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser["Tester"] = {"Expert": ""}
        for key, variable in self.tester_vars.items():
            value = variable.get().strip()
            if not value:
                value = existing_tester.get(key, "").strip()
            if not value:
                value = TEMPLATE_FIELD_DEFAULTS.get(key, "")
            parser["Tester"][key] = value
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

    def _telegram_values(self) -> tuple[str, str]:
        token = self.telegram_bot_token.get().strip()
        chat_id = self.telegram_chat_id.get().strip()
        if not token:
            raise ValueError("Indica TELEGRAM_BOT_TOKEN.")
        if not chat_id:
            raise ValueError("Indica TELEGRAM_CHAT_ID.")
        return token, chat_id

    def _save_telegram_settings(self) -> None:
        self._update_env_vars(
            {
                "TELEGRAM_BOT_TOKEN": self.telegram_bot_token.get().strip(),
                "TELEGRAM_CHAT_ID": self.telegram_chat_id.get().strip(),
            }
        )
        self._write_ui_settings()
        self.status_text.set("Telegram guardado")

    def _save_telegram_clicked(self) -> None:
        try:
            self._save_telegram_settings()
        except Exception as exc:
            self._show_error("No se pudo guardar Telegram", str(exc))
            return
        messagebox.showinfo("Telegram guardado", f"Credenciales guardadas en:\n{ENV_FILE}")

    def _test_telegram_clicked(self) -> None:
        try:
            token, chat_id = self._telegram_values()
        except Exception as exc:
            self._show_error("No se pudo probar Telegram", str(exc))
            return
        self.status_text.set("Probando Telegram...")
        self._append_console("\n[Telegram] Enviando mensaje de prueba...\n", tag="telegram")

        def on_result(error: str | None) -> None:
            def finish() -> None:
                if error:
                    self.status_text.set("Telegram: error")
                    self._append_console(f"[Telegram] {error}\n", tag="error")
                    self._show_error("Prueba Telegram fallida", error)
                    return
                self.status_text.set("Telegram probado correctamente")
                self._append_console("[Telegram] Mensaje de prueba enviado correctamente.\n", tag="telegram")
                messagebox.showinfo("Telegram", "Mensaje de prueba enviado correctamente.")

            self.after(0, finish)

        telegram_notify.send_async(
            "MT5 Autotester: mensaje de prueba de Telegram.",
            token=token,
            chat_id=chat_id,
            on_result=on_result,
        )

    def _browse_file(self, variable) -> None:
        path = filedialog.askopenfilename(initialdir=str(BASE_DIR))
        if path:
            variable.set(path)

    def _browse_template_file(self, variable) -> None:
        current = Path(variable.get()).expanduser() if variable.get().strip() else BASE_DIR
        initial_dir = current.parent if current.parent.exists() else BASE_DIR
        path = filedialog.askopenfilename(
            initialdir=str(initial_dir),
            filetypes=(("INI files", "*.ini"), ("Todos", "*.*")),
        )
        if path:
            variable.set(path)
            self._load_template_clicked(show_success=False)

    def _browse_dir(self, variable) -> None:
        path = filedialog.askdirectory(initialdir=str(BASE_DIR))
        if path:
            variable.set(path)

    def _browse_mq5_file(self, variable) -> None:
        initial_dir = self.compile_root.get().strip() or str(BASE_DIR)
        path = filedialog.askopenfilename(
            initialdir=initial_dir,
            filetypes=(("MQL5 source", "*.mq5"), ("Todos", "*.*")),
        )
        if path:
            variable.set(str(Path(path)))

    def _browse_ex5_file(self, variable) -> None:
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

    def _browse_profile_ex5_file(self, variable) -> None:
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

    def _browse_set_file(self, variable) -> None:
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
        self._write_ui_settings()
        self.status_text.set("Rutas guardadas")
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
        from common import mt5_env
        mt5_env._PROJECT_ENV = None

    def _write_general_settings(self, parser) -> None:
        """Seccion General completa, con el mapa de simbolos por broker."""
        symbol_maps = getattr(self, "_ubs_symbol_maps_by_broker", {})
        if symbol_maps is None:
            symbol_maps = {}
        active_symbol_broker = normalize_broker(getattr(self, "_ubs_symbol_map_active_broker", self.ubs_broker.get()))
        symbol_maps[active_symbol_broker] = self.symbol_map.get().strip()
        for broker in BROKERS:
            symbol_maps.setdefault(broker, default_symbol_map_for_broker(broker))
        self._ubs_symbol_maps_by_broker = symbol_maps
        parser["General"] = {
            **self._general_agent_settings(symbol_maps),
            **self._general_stage_settings(symbol_maps),
            **self._general_symbol_settings(symbol_maps),
            **self._general_portfolio_settings(symbol_maps),
            **self._general_monthly_settings(symbol_maps),
        }
        for broker in BROKERS:
            parser["General"][symbol_map_setting_key(broker)] = symbol_maps.get(
                broker,
                default_symbol_map_for_broker(broker),
            )

    def _write_multiterminal_settings(self, parser, saved_multiterminal_tuning):
        """Seccion Multiterminal y un bloque por perfil de terminal."""
        parser["Multiterminal"] = {
            "enabled": "1" if self.multiterminal_enabled.get() else "0",
            "broker": self._active_multiterminal_broker() if hasattr(self, "_active_multiterminal_broker") else "ROBOFOREX",
            "workers": str(self._multiterminal_worker_limit()),
            "terminal_cooldown": saved_multiterminal_tuning["terminal_cooldown"],
            "tester_kick_after": saved_multiterminal_tuning["tester_kick_after"],
            "tester_stall_after": saved_multiterminal_tuning["tester_stall_after"],
            "tester_max_runtime": saved_multiterminal_tuning["tester_max_runtime"],
        }
        for index, profile in enumerate(self.multiterminal_profiles, start=1):
            parser[f"Terminal.{index}"] = {
                "enabled": "1" if bool(profile.get("enabled")) else "0",
                "broker": str(profile.get("broker") or "ROBOFOREX").strip().upper(),
                "name": str(profile.get("name") or f"Terminal {index}").strip(),
                "mt5_path": str(profile.get("mt5_path") or "").strip(),
                "data_dir": str(profile.get("data_dir") or "").strip(),
                "experts_root": str(profile.get("experts_root") or "").strip(),
                "ubs_ex5_file": str(profile.get("ubs_ex5_file") or "").strip(),
                "portable": "1" if bool(profile.get("portable")) else "0",
            }

    def _write_ui_settings(self) -> None:
        self._save_current_multiterminal_editor()
        saved_multiterminal_tuning = {
            "terminal_cooldown": "1",
            "tester_kick_after": "30",
            "tester_stall_after": "300",
            "tester_max_runtime": "1800",
        }
        if UI_SETTINGS_FILE.exists():
            existing = configparser.ConfigParser(interpolation=None)
            existing.optionxform = str
            existing.read(UI_SETTINGS_FILE, encoding="utf-8-sig")
            if existing.has_section("Multiterminal"):
                for key, default in saved_multiterminal_tuning.items():
                    saved_multiterminal_tuning[key] = existing["Multiterminal"].get(key, default).strip() or default
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
        self._write_general_settings(parser)
        self._write_multiterminal_settings(parser, saved_multiterminal_tuning)
        with UI_SETTINGS_FILE.open("w", encoding="utf-8", newline="\n") as file:
            parser.write(file, space_around_delimiters=False)
        self._update_multiterminal_summary()

    def _write_single_path(self, path: Path, value: str, comment: str) -> None:
        text = f"# {comment}\n{value.strip()}\n" if value.strip() else f"# {comment}\n"
