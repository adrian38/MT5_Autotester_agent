from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .excel import build_workbook
from .dd_excel import (
    build_dd_threshold_workbook,
    build_drawdown_workbook,
    build_portfolio_drawdown_workbook,
    build_portfolio_valley_drawdown_workbook,
    build_top_portfolio_valleys_workbook,
)
from .mt5_report import StrategyReport, parse_report


ProgressCallback = Callable[[str], None]


def find_report_files(input_dir: Path) -> list[Path]:
    return [
        path
        for path in sorted(input_dir.iterdir())
        if path.is_file() and path.suffix.lower() in {".htm", ".html"}
    ]


def generate_workbook(
    input_dir: Path,
    output_path: Path,
    progress: ProgressCallback | None = None,
) -> list[StrategyReport]:
    input_dir = input_dir.expanduser().resolve()
    output_path = output_path.expanduser().resolve()

    if not input_dir.exists() or not input_dir.is_dir():
        raise ValueError(f"Input folder does not exist: {input_dir}")

    report_files = find_report_files(input_dir)
    if not report_files:
        raise ValueError(f"No .htm/.html reports found in: {input_dir}")

    reports: list[StrategyReport] = []
    total = len(report_files)
    for index, path in enumerate(report_files, start=1):
        if progress:
            progress(f"Parsing {index}/{total}: {path.name}")
        reports.append(parse_report(path))

    if progress:
        progress("Building Excel workbook...")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    build_workbook(reports, output_path)

    if progress:
        progress(f"Created {output_path} with {len(reports)} strategies")
    return reports


def generate_drawdown_workbook(
    input_dir: Path,
    output_path: Path,
    progress: ProgressCallback | None = None,
) -> list[StrategyReport]:
    input_dir = input_dir.expanduser().resolve()
    output_path = output_path.expanduser().resolve()

    if not input_dir.exists() or not input_dir.is_dir():
        raise ValueError(f"Input folder does not exist: {input_dir}")

    report_files = find_report_files(input_dir)
    if not report_files:
        raise ValueError(f"No .htm/.html reports found in: {input_dir}")

    reports: list[StrategyReport] = []
    total = len(report_files)
    for index, path in enumerate(report_files, start=1):
        if progress:
            progress(f"Parsing DD {index}/{total}: {path.name}")
        reports.append(parse_report(path))

    if progress:
        progress("Building drawdown workbook...")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    build_drawdown_workbook(reports, output_path)

    if progress:
        progress(f"Created {output_path} with {len(reports)} drawdown sheets")
    return reports


def generate_portfolio_drawdown_workbook(
    input_dir: Path,
    output_path: Path,
    progress: ProgressCallback | None = None,
) -> list[StrategyReport]:
    input_dir = input_dir.expanduser().resolve()
    output_path = output_path.expanduser().resolve()

    if not input_dir.exists() or not input_dir.is_dir():
        raise ValueError(f"Input folder does not exist: {input_dir}")

    report_files = find_report_files(input_dir)
    if not report_files:
        raise ValueError(f"No .htm/.html reports found in: {input_dir}")

    reports: list[StrategyReport] = []
    total = len(report_files)
    for index, path in enumerate(report_files, start=1):
        if progress:
            progress(f"Parsing portfolio DD {index}/{total}: {path.name}")
        reports.append(parse_report(path))

    if progress:
        progress("Building portfolio drawdown workbook...")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    build_portfolio_drawdown_workbook(reports, output_path)

    if progress:
        progress(f"Created {output_path} with portfolio DD breakdown")
    return reports


def generate_portfolio_valley_drawdown_workbook(
    input_dir: Path,
    output_path: Path,
    progress: ProgressCallback | None = None,
) -> list[StrategyReport]:
    input_dir = input_dir.expanduser().resolve()
    output_path = output_path.expanduser().resolve()

    if not input_dir.exists() or not input_dir.is_dir():
        raise ValueError(f"Input folder does not exist: {input_dir}")

    report_files = find_report_files(input_dir)
    if not report_files:
        raise ValueError(f"No .htm/.html reports found in: {input_dir}")

    reports: list[StrategyReport] = []
    total = len(report_files)
    for index, path in enumerate(report_files, start=1):
        if progress:
            progress(f"Parsing portfolio valley DD {index}/{total}: {path.name}")
        reports.append(parse_report(path))

    if progress:
        progress("Building portfolio valley drawdown workbook...")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    build_portfolio_valley_drawdown_workbook(reports, output_path)

    if progress:
        progress(f"Created {output_path} with portfolio valley DD")
    return reports


def generate_top_portfolio_valleys_workbook(
    input_dir: Path,
    output_path: Path,
    progress: ProgressCallback | None = None,
) -> list[StrategyReport]:
    input_dir = input_dir.expanduser().resolve()
    output_path = output_path.expanduser().resolve()

    if not input_dir.exists() or not input_dir.is_dir():
        raise ValueError(f"Input folder does not exist: {input_dir}")

    report_files = find_report_files(input_dir)
    if not report_files:
        raise ValueError(f"No .htm/.html reports found in: {input_dir}")

    reports: list[StrategyReport] = []
    total = len(report_files)
    for index, path in enumerate(report_files, start=1):
        if progress:
            progress(f"Parsing top portfolio valleys {index}/{total}: {path.name}")
        reports.append(parse_report(path))

    if progress:
        progress("Building top portfolio valleys workbook...")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    build_top_portfolio_valleys_workbook(reports, output_path)

    if progress:
        progress(f"Created {output_path} with top portfolio valleys")
    return reports


def generate_dd_threshold_workbook(
    input_dir: Path,
    output_path: Path,
    threshold: float,
    progress: ProgressCallback | None = None,
) -> list[StrategyReport]:
    input_dir = input_dir.expanduser().resolve()
    output_path = output_path.expanduser().resolve()

    if threshold < 0:
        raise ValueError("Threshold must be positive. Example: 50 for a -50 daily peak.")
    if not input_dir.exists() or not input_dir.is_dir():
        raise ValueError(f"Input folder does not exist: {input_dir}")

    report_files = find_report_files(input_dir)
    if not report_files:
        raise ValueError(f"No .htm/.html reports found in: {input_dir}")

    reports: list[StrategyReport] = []
    total = len(report_files)
    for index, path in enumerate(report_files, start=1):
        if progress:
            progress(f"Parsing threshold DD {index}/{total}: {path.name}")
        reports.append(parse_report(path))

    if progress:
        progress("Building DD threshold workbook...")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    build_dd_threshold_workbook(reports, output_path, threshold)

    if progress:
        progress(f"Created {output_path} with DD threshold {threshold}")
    return reports
