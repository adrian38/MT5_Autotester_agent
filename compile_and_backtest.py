import argparse
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
COMPILE_ROOT_FILE = BASE_DIR / "compile_root.txt"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compila .mq5 y luego ejecuta backtests sobre los .ex5 generados.")
    parser.add_argument("--mt5-path", help="Ruta completa a terminal64.exe.")
    parser.add_argument("--data-dir", help="Carpeta de datos del terminal MT5.")
    parser.add_argument("--metaeditor-path", help="Ruta completa a MetaEditor64.exe.")
    parser.add_argument("--source-dir", help="Carpeta raiz de .mq5/.ex5. Si no se indica, lee compile_root.txt.")
    parser.add_argument("--source-file", help="Nombre o ruta de un .mq5 concreto dentro de --source-dir.")
    parser.add_argument("--template", help="Archivo .ini general para backtest.")
    parser.add_argument("--symbol-suffix", default="", help="Sufijo a agregar al simbolo del template.")
    parser.add_argument("--symbol-map", default="", help="Correspondencias de simbolos, por ejemplo XTIUSD=USOIL.")
    parser.add_argument("--delay", type=int, default=5, help="Pausa en segundos entre tests.")
    parser.add_argument("--recursive", action="store_true", help="Procesar todos los .mq5/.ex5 de la carpeta indicada.")
    parser.add_argument("--skip-running-check", action="store_true", help="No comprobar si MT5 ya esta abierto.")
    parser.add_argument("--dry-run", action="store_true", help="Muestra comandos y genera .ini, pero no compila ni ejecuta MT5.")
    return parser.parse_args()


def load_compile_root() -> Path | None:
    if not COMPILE_ROOT_FILE.exists():
        return None

    for line in COMPILE_ROOT_FILE.read_text(encoding="utf-8-sig").splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        return Path(item).expanduser()
    return None


def run_step(name: str, command: list[str]) -> int:
    print("")
    print(f"=== {name} ===")
    print(" ".join(f'"{part}"' if " " in part else part for part in command))
    sys.stdout.flush()
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW,
    )
    assert process.stdout is not None
    for line in process.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
    process.wait()
    print(f"{name} termino con codigo: {process.returncode}")
    return process.returncode


def count_ex5(source_dir: Path, recursive: bool) -> int:
    return len(list(source_dir.glob("*.ex5")))


def child_command(script_name: str) -> list[str]:
    if getattr(sys, "frozen", False):
        exe_path = BASE_DIR / Path(script_name).with_suffix(".exe")
        return [str(exe_path)]
    return [sys.executable, str(BASE_DIR / script_name)]


def main() -> int:
    args = parse_args()
    source_dir = Path(args.source_dir).expanduser() if args.source_dir else load_compile_root()
    if not source_dir:
        print(f"ERROR: indica --source-dir o escribe una ruta activa en {COMPILE_ROOT_FILE}")
        return 1

    compile_command = child_command("compile_mq5.py") + ["--source-dir", str(source_dir)]
    if args.mt5_path:
        compile_command.extend(["--mt5-path", args.mt5_path])
    if args.metaeditor_path:
        compile_command.extend(["--metaeditor-path", args.metaeditor_path])
    if args.source_file:
        compile_command.extend(["--source-file", args.source_file])
    if args.recursive:
        compile_command.append("--recursive")
    if args.dry_run:
        compile_command.append("--dry-run")

    compile_code = run_step("Compilacion", compile_command)
    if compile_code != 0:
        print("ERROR: la compilacion fallo; no se ejecutan backtests.")
        return compile_code

    if not args.dry_run:
        ex5_count = count_ex5(source_dir, args.recursive)
        print("")
        print(f".ex5 disponibles despues de compilar: {ex5_count}")
        if ex5_count == 0:
            print("")
            print("ERROR: no se encontro ningun .ex5 para backtestear.")
            print(f"  Carpeta buscada: {source_dir}")
            print(f"  Modo recursivo: {'si' if args.recursive else 'no'}")
            print("  Revisa que la ruta sea correcta y que la compilacion haya generado .ex5.")
            print("  Detalle en logs\\last_compile.log.")
            return 1

    backtest_command = child_command("run_tests.py") + ["--experts-dir", str(source_dir)]
    if args.mt5_path:
        backtest_command.extend(["--mt5-path", args.mt5_path])
    if args.data_dir:
        backtest_command.extend(["--data-dir", args.data_dir])
    backtest_command.extend(["--delay", str(args.delay)])
    if args.source_file:
        backtest_command.extend(["--expert", str(Path(args.source_file).with_suffix(".ex5"))])
    if args.template:
        backtest_command.extend(["--template", args.template])
    if args.symbol_suffix:
        backtest_command.extend(["--symbol-suffix", args.symbol_suffix])
    if args.symbol_map:
        backtest_command.extend(["--symbol-map", args.symbol_map])
    if args.skip_running_check:
        backtest_command.append("--skip-running-check")
    if args.recursive:
        backtest_command.append("--recursive")
    if args.dry_run:
        backtest_command.append("--dry-run")

    return run_step("Backtests", backtest_command)


if __name__ == "__main__":
    sys.exit(main())
