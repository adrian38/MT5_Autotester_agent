import os
import sys
from pathlib import Path


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = app_base_dir()
ENV_FILE = BASE_DIR / ".env"

MT5_TERMINAL_ENV = ("MT5_TERMINAL_PATH", "MT5_PATH")
MT5_METAEDITOR_ENV = ("MT5_METAEDITOR_PATH", "METAEDITOR_PATH")

_PROJECT_ENV: dict[str, str] | None = None


def _load_project_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_FILE.exists():
        return values

    for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        item = line.strip()
        if not item or item.startswith("#") or "=" not in item:
            continue
        name, value = item.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name:
            values[name] = value
    return values


def project_env() -> dict[str, str]:
    global _PROJECT_ENV
    if _PROJECT_ENV is None:
        _PROJECT_ENV = _load_project_env()
    return _PROJECT_ENV


def env_value(*names: str) -> str | None:
    file_values = project_env()
    for name in names:
        value = os.environ.get(name) or file_values.get(name)
        if value:
            return value
    return None


def env_path(*names: str) -> Path | None:
    value = env_value(*names)
    if not value:
        return None
    return Path(os.path.expandvars(value)).expanduser()


def terminal_path_from_env() -> Path | None:
    return env_path(*MT5_TERMINAL_ENV)


def metaeditor_path_from_env() -> Path | None:
    return env_path(*MT5_METAEDITOR_ENV)
