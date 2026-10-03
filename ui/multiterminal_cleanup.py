"""Limpieza de los ficheros temporales del tester en cada terminal."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


TESTER_ROOT_TEMP_SUFFIXES = {".gif", ".htm", ".html", ".png", ".set", ".xml"}


@dataclass(frozen=True)
class TesterCleanupPlan:
    data_dirs: tuple[Path, ...]
    files: tuple[Path, ...]
    removable_dirs: tuple[Path, ...]
    total_bytes: int


def _normalized_path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _unique_data_dirs(data_dirs: list[Path]) -> list[Path]:
    unique_data_dirs: list[Path] = []
    seen_data_dirs: set[str] = set()
    for raw_data_dir in data_dirs:
        data_dir = Path(raw_data_dir).expanduser().resolve(strict=False)
        key = _normalized_path_key(data_dir)
        if key in seen_data_dirs:
            continue
        seen_data_dirs.add(key)
        unique_data_dirs.append(data_dir)
    return unique_data_dirs


def _collect_tester_root(tester_dir: Path, add_file) -> set[str]:
    """Disposable artifacts left directly in Tester/, plus the .set names seen."""
    root_set_names: set[str] = set()
    try:
        root_entries = list(tester_dir.iterdir())
    except OSError:
        root_entries = []
    for path in root_entries:
        if not path.is_file() or path.suffix.lower() not in TESTER_ROOT_TEMP_SUFFIXES:
            continue
        add_file(path, tester_dir)
        if path.suffix.lower() == ".set":
            root_set_names.add(path.name.casefold())
    return root_set_names


def _collect_disposable_dirs(tester_dir: Path, add_file, removable_dirs: dict[str, Path]) -> None:
    for folder_name in ("cache", "logs"):
        disposable_dir = tester_dir / folder_name
        if not disposable_dir.is_dir() or not _path_within(disposable_dir, tester_dir):
            continue
        for walk_root, dir_names, file_names in os.walk(disposable_dir, followlinks=False):
            walk_path = Path(walk_root)
            for file_name in file_names:
                add_file(walk_path / file_name, disposable_dir)
            for dir_name in dir_names:
                candidate = walk_path / dir_name
                if not candidate.is_symlink() and _path_within(candidate, disposable_dir):
                    removable_dirs.setdefault(_normalized_path_key(candidate), candidate)
        removable_dirs.setdefault(_normalized_path_key(disposable_dir), disposable_dir)


def _collect_tester_profiles(data_dir: Path, root_set_names: set[str], add_file) -> None:
    profiles_dir = data_dir / "MQL5" / "Profiles" / "Tester"
    if not root_set_names or not profiles_dir.is_dir():
        return
    try:
        profile_entries = list(profiles_dir.iterdir())
    except OSError:
        profile_entries = []
    for path in profile_entries:
        if path.is_file() and path.suffix.lower() == ".set" and path.name.casefold() in root_set_names:
            add_file(path, profiles_dir)


def build_tester_cleanup_plan(data_dirs: list[Path]) -> TesterCleanupPlan:
    """Collect only disposable artifacts created by MT5 or this runner."""
    unique_data_dirs = _unique_data_dirs(data_dirs)
    files: dict[str, Path] = {}
    removable_dirs: dict[str, Path] = {}

    def add_file(path: Path, allowed_root: Path) -> None:
        if not _path_within(path, allowed_root) or not path.is_file():
            return
        files.setdefault(_normalized_path_key(path), path)

    for data_dir in unique_data_dirs:
        tester_dir = data_dir / "Tester"
        if not tester_dir.is_dir():
            continue
        root_set_names = _collect_tester_root(tester_dir, add_file)
        _collect_disposable_dirs(tester_dir, add_file, removable_dirs)
        _collect_tester_profiles(data_dir, root_set_names, add_file)

    total_bytes = 0
    for path in files.values():
        try:
            total_bytes += path.stat().st_size
        except OSError:
            pass
    dirs_deepest_first = sorted(
        removable_dirs.values(),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    return TesterCleanupPlan(
        data_dirs=tuple(unique_data_dirs),
        files=tuple(files.values()),
        removable_dirs=tuple(dirs_deepest_first),
        total_bytes=total_bytes,
    )


def execute_tester_cleanup(plan: TesterCleanupPlan) -> tuple[int, int, list[str]]:
    deleted_files = 0
    freed_bytes = 0
    failures: list[str] = []
    for path in plan.files:
        try:
            size = path.stat().st_size
            path.unlink()
            deleted_files += 1
            freed_bytes += size
        except FileNotFoundError:
            continue
        except OSError as exc:
            failures.append(f"{path}: {exc}")
    for path in plan.removable_dirs:
        try:
            path.rmdir()
        except FileNotFoundError:
            continue
        except OSError:
            # It may contain a protected junction/symlink or a file that could
            # not be removed. Leaving the directory is the safe outcome.
            continue
    return deleted_files, freed_bytes, failures


def _format_cleanup_bytes(value: int) -> str:
    amount = float(max(value, 0))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024.0 or unit == "TB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024.0
    return f"{amount:.1f} TB"
