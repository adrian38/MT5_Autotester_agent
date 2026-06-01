import os
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import winreg
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


APP_NAME = "MT5 Autotester"
REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\MT5 Autotester"

COLORS = {
    "bg": "#f8f9ff",
    "panel": "#ffffff",
    "border": "#c5c6cd",
    "text": "#0b1c30",
    "muted": "#45474c",
    "primary": "#091426",
    "accent": "#006c49",
    "accent_hover": "#005236",
    "danger": "#ba1a1a",
}


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def run_powershell(script: str) -> None:
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def user_shell_folder(name: str, fallback: Path) -> Path:
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return fallback
    return Path(os.path.expandvars(value))


def create_shortcut(path: Path, target: Path, working_dir: Path, description: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    script = f"""
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut({ps_quote(str(path))})
    $Shortcut.TargetPath = {ps_quote(str(target))}
    $Shortcut.WorkingDirectory = {ps_quote(str(working_dir))}
    $Shortcut.Description = {ps_quote(description)}
    $Shortcut.Save()
    """
    run_powershell(script)


def register_uninstaller(install_dir: Path, version: str) -> None:
    uninstall_script = install_dir / "uninstall.ps1"
    uninstall_command = f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{uninstall_script}"'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_KEY) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, version)
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(install_dir))
        winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, uninstall_command)


class InstallerUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"Instalar {APP_NAME}")
        self.geometry("560x360")
        self.resizable(False, False)
        self.configure(bg=COLORS["bg"])
        self._apply_icon()

        default_dir = str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_NAME)
        self.install_dir = tk.StringVar(value=default_dir)
        self.status_text = tk.StringVar(value="Selecciona la carpeta de instalacion y pulsa Instalar.")
        self.progress_value = tk.DoubleVar(value=0)
        self._installing = False
        self._exe_path: Path | None = None

        self._configure_style()
        self._build_ui()

    def _apply_icon(self) -> None:
        candidates = [
            resource_path("app_icon.ico"),
            resource_path("assets/app_icon.ico"),
            resource_path("app_icon.png"),
            resource_path("assets/app_icon.png"),
        ]
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

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI", 10))
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Panel.TFrame", background=COLORS["panel"])
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
        style.configure("Title.TLabel", background=COLORS["bg"], foreground=COLORS["primary"], font=("Segoe UI", 16, "bold"))
        style.configure("Subtitle.TLabel", background=COLORS["bg"], foreground=COLORS["muted"], font=("Segoe UI", 9))
        style.configure("Section.TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI", 10, "bold"))
        style.configure("TButton", padding=(12, 8), borderwidth=0, font=("Segoe UI", 10, "bold"))
        style.configure("Primary.TButton", background=COLORS["accent"], foreground="#ffffff", padding=(14, 9))
        style.map("Primary.TButton", background=[("active", COLORS["accent_hover"]), ("disabled", "#8ba59c")])
        style.configure("Secondary.TButton", background="#e5eeff", foreground=COLORS["text"], padding=(12, 8))
        style.configure("TEntry", fieldbackground="#ffffff", bordercolor=COLORS["border"], padding=6)
        style.configure("Install.Horizontal.TProgressbar",
                        background=COLORS["accent"],
                        troughcolor="#dce9ff",
                        bordercolor="#dce9ff",
                        lightcolor=COLORS["accent"],
                        darkcolor=COLORS["accent"],
                        thickness=14)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        outer = ttk.Frame(self, padding=(28, 22, 28, 18))
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)

        ttk.Label(outer, text=f"Instalar {APP_NAME}", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(outer, text="Compila estrategias y lanza backtests automaticos en MetaTrader 5.",
                  style="Subtitle.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 18))

        ttk.Label(outer, text="Carpeta de instalacion", style="Section.TLabel").grid(row=2, column=0, sticky="w")
        path_row = ttk.Frame(outer)
        path_row.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        path_row.columnconfigure(0, weight=1)
        self.path_entry = ttk.Entry(path_row, textvariable=self.install_dir)
        self.path_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(path_row, text="Examinar...", style="Secondary.TButton",
                   command=self._browse).grid(row=0, column=1, padx=(8, 0))

        progress_frame = ttk.Frame(outer)
        progress_frame.grid(row=4, column=0, sticky="ew", pady=(28, 6))
        progress_frame.columnconfigure(0, weight=1)
        self.progress_bar = ttk.Progressbar(progress_frame, mode="determinate", maximum=100,
                                             variable=self.progress_value,
                                             style="Install.Horizontal.TProgressbar")
        self.progress_bar.grid(row=0, column=0, sticky="ew")
        self.percent_label = ttk.Label(progress_frame, text="0%", style="Subtitle.TLabel")
        self.percent_label.grid(row=0, column=1, sticky="e", padx=(10, 0))

        ttk.Label(outer, textvariable=self.status_text, style="Subtitle.TLabel").grid(row=5, column=0, sticky="w", pady=(2, 18))

        actions = ttk.Frame(outer)
        actions.grid(row=6, column=0, sticky="e")
        self.cancel_btn = ttk.Button(actions, text="Cancelar", style="Secondary.TButton", command=self._on_cancel)
        self.cancel_btn.grid(row=0, column=0, padx=(0, 8))
        self.install_btn = ttk.Button(actions, text="Instalar", style="Primary.TButton", command=self._start_install)
        self.install_btn.grid(row=0, column=1)

    def _browse(self) -> None:
        path = filedialog.askdirectory(initialdir=self.install_dir.get() or str(Path.home()),
                                       title="Elige la carpeta de instalacion")
        if path:
            full = Path(path)
            # Si el usuario elige una carpeta vacia, anexa el nombre de la app
            if full.name.lower() != APP_NAME.lower():
                full = full / APP_NAME
            self.install_dir.set(str(full))

    def _set_progress(self, value: float, message: str | None = None) -> None:
        value = max(0.0, min(100.0, float(value)))
        self.progress_value.set(value)
        self.percent_label.configure(text=f"{int(round(value))}%")
        if message is not None:
            self.status_text.set(message)

    def _on_cancel(self) -> None:
        if self._installing:
            if not messagebox.askyesno("Cancelar", "Hay una instalacion en curso. Cerrar de todos modos?"):
                return
        self.destroy()

    def _start_install(self) -> None:
        if self._installing:
            return
        target = Path(self.install_dir.get().strip()).expanduser()
        if not target.drive and not target.is_absolute():
            messagebox.showerror("Ruta invalida", "Indica una ruta absoluta para instalar.")
            return
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Ruta invalida", f"No se puede crear la carpeta:\n{target}\n\n{exc}")
            return
        if not os.access(str(target), os.W_OK):
            messagebox.showerror("Permisos", f"No tienes permisos de escritura en:\n{target}")
            return

        self._installing = True
        self.install_btn.state(["disabled"])
        self.path_entry.state(["disabled"])
        self._set_progress(0, "Preparando instalacion...")
        threading.Thread(target=self._install_thread, args=(target,), daemon=True).start()

    def _install_thread(self, install_dir: Path) -> None:
        try:
            payload = resource_path("MT5AutotesterPayload.zip")
            if not payload.exists():
                raise FileNotFoundError(f"No encuentro el payload del instalador: {payload}")

            self.after(0, lambda: self._set_progress(5, "Extrayendo archivos..."))

            temp_dir = Path(tempfile.mkdtemp(prefix="MT5AutotesterInstall_"))
            try:
                with zipfile.ZipFile(payload) as archive:
                    members = archive.infolist()
                    total = max(1, len(members))
                    for index, member in enumerate(members, start=1):
                        archive.extract(member, temp_dir)
                        # Extracción ocupa del 5% al 70%
                        pct = 5 + (index / total) * 65
                        self.after(0, lambda v=pct, n=member.filename:
                                   self._set_progress(v, f"Extrayendo: {n[:60]}"))

                install_dir.mkdir(parents=True, exist_ok=True)
                items = list(temp_dir.iterdir())
                total_items = max(1, len(items))
                for index, item in enumerate(items, start=1):
                    destination = install_dir / item.name
                    if item.is_dir():
                        shutil.copytree(item, destination, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, destination)
                    # Copia ocupa del 70% al 90%
                    pct = 70 + (index / total_items) * 20
                    self.after(0, lambda v=pct, n=item.name:
                               self._set_progress(v, f"Copiando: {n}"))
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

            self.after(0, lambda: self._set_progress(92, "Creando accesos directos..."))

            exe_path = install_dir / "MT5Autotester.exe"
            version_file = install_dir / "VERSION.txt"
            version = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else "1.0.0"

            desktop = user_shell_folder("Desktop", Path(os.environ["USERPROFILE"]) / "Desktop")
            start_menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / APP_NAME
            start_menu.mkdir(parents=True, exist_ok=True)

            create_shortcut(desktop / f"{APP_NAME}.lnk", exe_path, install_dir, f"Abrir {APP_NAME}")
            create_shortcut(start_menu / f"{APP_NAME}.lnk", exe_path, install_dir, f"Abrir {APP_NAME}")
            create_shortcut(
                start_menu / f"Desinstalar {APP_NAME}.lnk",
                Path("powershell.exe"),
                install_dir,
                f"Desinstalar {APP_NAME}",
            )

            uninstall_shortcut = start_menu / f"Desinstalar {APP_NAME}.lnk"
            uninstall_script = install_dir / "uninstall.ps1"
            uninstall_args = f'-NoProfile -ExecutionPolicy Bypass -File "{uninstall_script}"'
            script = f"""
            $Shell = New-Object -ComObject WScript.Shell
            $Shortcut = $Shell.CreateShortcut({ps_quote(str(uninstall_shortcut))})
            $Shortcut.TargetPath = "powershell.exe"
            $Shortcut.Arguments = {ps_quote(uninstall_args)}
            $Shortcut.WorkingDirectory = {ps_quote(str(install_dir))}
            $Shortcut.Description = {ps_quote(f"Desinstalar {APP_NAME}")}
            $Shortcut.Save()
            """
            run_powershell(script)

            self.after(0, lambda: self._set_progress(97, "Registrando desinstalador..."))
            register_uninstaller(install_dir, version)

            self.after(0, lambda: self._on_done(exe_path))
        except Exception as exc:
            self.after(0, lambda e=exc: self._on_error(e))

    def _on_done(self, exe_path: Path) -> None:
        self._exe_path = exe_path
        self._installing = False
        self._set_progress(100, f"{APP_NAME} se instalo correctamente.")
        self.install_btn.state(["!disabled"])
        self.install_btn.configure(text="Abrir aplicacion", command=self._launch_and_close)
        self.cancel_btn.configure(text="Cerrar")

    def _on_error(self, exc: Exception) -> None:
        self._installing = False
        self._set_progress(0, "La instalacion fallo.")
        self.install_btn.state(["!disabled"])
        self.path_entry.state(["!disabled"])
        messagebox.showerror(APP_NAME, f"No se pudo instalar {APP_NAME}.\n\n{exc}")

    def _launch_and_close(self) -> None:
        if self._exe_path and self._exe_path.exists():
            try:
                subprocess.Popen([str(self._exe_path)], cwd=str(self._exe_path.parent))
            except OSError as exc:
                messagebox.showerror(APP_NAME, f"No se pudo abrir la aplicacion:\n{exc}")
                return
        self.destroy()


def main() -> int:
    app = InstallerUI()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
