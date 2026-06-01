import argparse
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from mt5_env import MT5_METAEDITOR_ENV, MT5_TERMINAL_ENV, metaeditor_path_from_env, terminal_path_from_env


BASE_DIR = Path(__file__).resolve().parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
LOG_DIR = BASE_DIR / "logs"
COMPILE_ROOT_FILE = BASE_DIR / "compile_root.txt"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

DEFAULT_METAEDITOR_PATHS = (
    Path(r"C:\Program Files\RoboForex MT5 Terminal\MetaEditor64.exe"),
    Path(r"C:\Program Files\MetaTrader 5\MetaEditor64.exe"),
    Path(r"C:\Program Files (x86)\MetaTrader 5\MetaEditor64.exe"),
)


class CompileLogger:
    def __init__(self) -> None:
        LOG_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = LOG_DIR / f"compile_{stamp}.log"
        self.last_log_path = LOG_DIR / "last_compile.log"
        self.last_log_path.write_text("", encoding="utf-8")

    def write(self, message: str = "") -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {message}" if message else ""
        print(message)
        with self.log_path.open("a", encoding="utf-8") as file:
            file.write(f"{line}\n")
        with self.last_log_path.open("a", encoding="utf-8") as file:
            file.write(f"{line}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compila archivos .mq5 a .ex5 usando MetaEditor64.exe.")
    parser.add_argument("--metaeditor-path", help="Ruta completa a MetaEditor64.exe.")
    parser.add_argument("--mt5-path", help="Ruta a terminal64.exe; se usa para deducir MetaEditor64.exe.")
    parser.add_argument("--source-dir", help="Carpeta raiz donde buscar .mq5. Si no se indica, lee compile_root.txt.")
    parser.add_argument("--source-file", help="Nombre o ruta de un .mq5 concreto dentro de --source-dir.")
    parser.add_argument("--recursive", action="store_true", help="Procesar todos los .mq5 de la carpeta indicada.")
    parser.add_argument("--dry-run", action="store_true", help="Muestra comandos, pero no compila.")
    return parser.parse_args()


def find_metaeditor_path(metaeditor_path: str | None, mt5_path: str | None) -> Path:
    if metaeditor_path:
        return Path(metaeditor_path).expanduser()

    if mt5_path:
        candidate = Path(mt5_path).expanduser().with_name("MetaEditor64.exe")
        return candidate

    env_metaeditor_path = metaeditor_path_from_env()
    if env_metaeditor_path:
        return env_metaeditor_path

    env_mt5_path = terminal_path_from_env()
    if env_mt5_path:
        return env_mt5_path.with_name("MetaEditor64.exe")

    for candidate in DEFAULT_METAEDITOR_PATHS:
        if candidate.exists():
            return candidate

    from_path = shutil.which("MetaEditor64.exe")
    if from_path:
        return Path(from_path)

    return DEFAULT_METAEDITOR_PATHS[0]


def load_compile_root() -> Path | None:
    if not COMPILE_ROOT_FILE.exists():
        return None

    for line in COMPILE_ROOT_FILE.read_text(encoding="utf-8-sig").splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        return Path(item).expanduser()
    return None


def find_sources(source_dir: Path, recursive: bool, source_file: str | None = None) -> list[Path]:
    if not source_dir.exists():
        raise FileNotFoundError(f"No existe la carpeta de fuentes .mq5: {source_dir}")
    if not source_dir.is_dir():
        raise NotADirectoryError(f"No es una carpeta: {source_dir}")

    if source_file:
        candidate = Path(source_file).expanduser()
        if not candidate.is_absolute():
            candidate = source_dir / candidate
        if candidate.suffix.lower() != ".mq5":
            candidate = candidate.with_suffix(".mq5")
        if not candidate.exists():
            raise FileNotFoundError(f"No existe el archivo .mq5 indicado: {candidate}")
        if not candidate.is_file():
            raise FileNotFoundError(f"No es un archivo .mq5: {candidate}")
        return [candidate]

    return sorted(source_dir.glob("*.mq5"))


def quote_command(command: list[str]) -> str:
    return " ".join(f'"{part}"' if " " in part else part for part in command)


def quote(value: Path | str) -> str:
    return f'"{value}"'


def find_mql5_root(source_path: Path) -> Path | None:
    parts = [part.lower() for part in source_path.parts]
    if "mql5" not in parts:
        return None
    index = parts.index("mql5")
    return Path(*source_path.parts[: index + 1])


def read_compile_log(compile_log: Path) -> str:
    for encoding in ("utf-16", "utf-8-sig", "utf-8"):
        try:
            text = compile_log.read_text(encoding=encoding)
            if text.strip():
                return text
        except UnicodeError:
            continue
    return compile_log.read_text(errors="ignore")


def compile_source(metaeditor_path: Path, source_path: Path, logger: CompileLogger, dry_run: bool) -> bool:
    compile_log = LOG_DIR / f"{source_path.stem}_compile.log"
    output_path = source_path.with_suffix(".ex5")
    old_mtime = output_path.stat().st_mtime if output_path.exists() else None
    mql5_root = find_mql5_root(source_path)
    command = f"{quote(metaeditor_path)} /compile:{quote(source_path)} /log:{quote(compile_log)}"
    if mql5_root:
        command = f"{quote(metaeditor_path)} /compile:{quote(source_path)} /inc:{quote(mql5_root)} /log:{quote(compile_log)}"

    logger.write("")
    logger.write(f"Fuente: {source_path}")
    logger.write(f"Salida esperada: {output_path}")
    if mql5_root:
        logger.write(f"Include MQL5: {mql5_root}")
    logger.write(f"Comando: {command}")

    if dry_run:
        return True

    start = time.time()
    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        shell=True,
        creationflags=NO_WINDOW,
    )
    elapsed = time.time() - start

    logger.write(f"MetaEditor termino con codigo: {process.returncode}")
    logger.write(f"Duracion: {elapsed:.1f} segundos")

    if process.stdout.strip():
        logger.write("STDOUT:")
        logger.write(process.stdout.strip())
    if process.stderr.strip():
        logger.write("STDERR:")
        logger.write(process.stderr.strip())

    if compile_log.exists():
        logger.write(f"Log compilacion: {compile_log}")
        log_text = read_compile_log(compile_log)
        for line in log_text.splitlines()[-80:]:
            logger.write(f"  {line}")
    else:
        logger.write("Aviso: MetaEditor no genero log de compilacion.")

    if not output_path.exists():
        logger.write("ERROR: No se genero el .ex5.")
        return False

    new_mtime = output_path.stat().st_mtime
    if old_mtime is not None and new_mtime <= old_mtime:
        logger.write("ERROR: El .ex5 existe, pero no parece haberse actualizado.")
        return False

    logger.write(f"OK: generado {output_path} ({output_path.stat().st_size} bytes)")
    if process.returncode != 0:
        logger.write(f"Aviso: MetaEditor devolvio codigo {process.returncode}, pero el .ex5 fue generado.")
    return True


def main() -> int:
    args = parse_args()
    logger = CompileLogger()

    metaeditor_path = find_metaeditor_path(args.metaeditor_path, args.mt5_path)
    source_dir = Path(args.source_dir).expanduser() if args.source_dir else load_compile_root()

    logger.write(f"Log: {logger.log_path}")
    logger.write(f"Ultimo log: {logger.last_log_path}")
    logger.write(f"Modo: {'DRY-RUN' if args.dry_run else 'REAL'}")
    logger.write(f"MetaEditor: {metaeditor_path}")
    logger.write(f"Recursivo: {args.recursive}")
    if args.source_file:
        logger.write(f"Archivo concreto: {args.source_file}")

    if not source_dir:
        logger.write(f"ERROR: indica --source-dir o escribe una ruta activa en {COMPILE_ROOT_FILE}")
        return 1

    logger.write(f"Raiz fuentes: {source_dir}")

    if not metaeditor_path.exists() and not args.dry_run:
        logger.write(f"ERROR: no encuentro MetaEditor64.exe en {metaeditor_path}")
        logger.write(
            f"Usa --metaeditor-path, --mt5-path, {MT5_METAEDITOR_ENV[0]} o {MT5_TERMINAL_ENV[0]}."
        )
        return 1

    try:
        sources = find_sources(source_dir, args.recursive, args.source_file)
    except (FileNotFoundError, NotADirectoryError) as exc:
        logger.write(f"ERROR: {exc}")
        return 1
    logger.write(f"Archivos .mq5 encontrados: {len(sources)}")

    if not sources:
        logger.write("")
        logger.write("ERROR: no se encontraron archivos .mq5 para compilar.")
        logger.write(f"  Carpeta buscada: {source_dir}")
        logger.write(f"  Modo recursivo: {'si' if args.recursive else 'no'}")
        if args.source_file:
            logger.write(f"  Filtro --source-file: {args.source_file}")
        logger.write("  Revisa que la ruta sea correcta y que existan archivos .mq5.")
        try:
            subdirs = [d for d in source_dir.iterdir() if d.is_dir()][:10]
            if subdirs:
                logger.write(f"  Subcarpetas detectadas en la raiz ({len(subdirs)}):")
                for d in subdirs:
                    logger.write(f"    - {d.name}")
        except OSError:
            pass
        return 1

    failures = 0
    for source_path in sources:
        if not compile_source(metaeditor_path, source_path, logger, args.dry_run):
            failures += 1

    logger.write("")
    if args.dry_run:
        logger.write("Dry-run terminado. No se compilo ningun archivo.")
    elif failures:
        logger.write(f"Terminado con {failures} compilacion(es) fallida(s).")
    else:
        logger.write("Todas las compilaciones terminaron correctamente.")

    logger.write(f"Log guardado en: {logger.log_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
