"""Puerta única de verificación segura del agente ICTrading."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class CommandSpec:
    name: str
    args: tuple[str, ...]


def command_specs(*, include_full_suite: bool = True) -> list[CommandSpec]:
    python = sys.executable
    specs = [
        CommandSpec("nombres", (python, "-m", "tools.undefined_names")),
        CommandSpec("funciones", (python, "-m", "tools.function_length")),
        CommandSpec("ficheros", (python, "-m", "tools.file_length")),
        CommandSpec("contexto", (python, "-m", "tools.ai_context_index")),
        CommandSpec("protocolo", (python, "-m", "tools.protocol_parity")),
        CommandSpec("guardas", (
            python, "-m", "unittest", "tests.test_project_rules",
            "tests.test_function_length", "tests.test_file_length",
            "tests.test_ai_context_index", "tests.test_undefined_names",
            "tests.test_verify_project",
        )),
    ]
    if include_full_suite:
        specs.append(CommandSpec(
            "suite IC", (python, "-m", "unittest", "discover", "-s", "tests"),
        ))
    return specs


def repository_state() -> tuple[str, str]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=no"],
        cwd=ROOT, check=True, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    ).stdout
    return head, status


def run_spec(spec: CommandSpec) -> bool:
    print(f"\n[VERIFICA] {spec.name}", flush=True)
    return subprocess.run(spec.args, cwd=ROOT, check=False).returncode == 0


def parse_args(argv: list[str]) -> bool:
    if not argv:
        return True
    if argv == ["--quick"]:
        return False
    raise ValueError("uso: python -m tools.verify_project [--quick]")


def main(argv: list[str] | None = None) -> int:
    try:
        full = parse_args(list(sys.argv[1:] if argv is None else argv))
    except ValueError as error:
        print(error)
        return 2
    before = repository_state()
    failed = [spec.name for spec in command_specs(include_full_suite=full)
              if not run_spec(spec)]
    changed = repository_state() != before
    if changed:
        print("[CAMBIO CONCURRENTE] HEAD o estado tracked cambió durante la verificación")
    if failed:
        print("[FALLO] " + ", ".join(failed))
    if failed or changed:
        return 1
    print("\n[OK] contrato completo del agente ICTrading verificado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
