"""Puerta unica de verificacion segura del agente AXI.

Por defecto corre lo barato: las auditorias mecanicas y el grupo de guardas.
El resto de pruebas vive repartido en grupos por area (``tools.test_groups``) y
se pide por nombre; la suite completa solo con ``--full``, porque son ~950
pruebas y en el trabajo normal no aportan sobre el grupo del area tocada.

    python -m tools.verify_project                      # auditorias + guardas
    python -m tools.verify_project --group final_tick   # y ese grupo
    python -m tools.verify_project --full               # y la suite entera
    python -m tools.verify_project --list-groups
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from tools.test_groups import GUARD_GROUP, group_names, modules_for, unittest_targets

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class CommandSpec:
    name: str
    args: tuple[str, ...]


@dataclass(frozen=True)
class Plan:
    """Lo que se ha pedido verificar."""

    groups: tuple[str, ...] = field(default=())
    include_full_suite: bool = False
    list_groups: bool = False


def _audit_specs() -> list[CommandSpec]:
    python = sys.executable
    return [
        CommandSpec("nombres", (python, "-m", "tools.undefined_names")),
        CommandSpec("funciones", (python, "-m", "tools.function_length")),
        CommandSpec("ficheros", (python, "-m", "tools.file_length")),
        CommandSpec("contexto", (python, "-m", "tools.ai_context_index")),
        CommandSpec("protocolo", (python, "-m", "tools.protocol_parity")),
        CommandSpec("grupos", (python, "-m", "tools.test_groups")),
    ]


def group_spec(group: str) -> CommandSpec:
    """Un grupo del reparto como una sola llamada a unittest."""
    return CommandSpec(
        f"grupo {group}",
        (sys.executable, "-m", "unittest", *unittest_targets(group)),
    )


def command_specs(
    *,
    groups: tuple[str, ...] = (),
    include_full_suite: bool = False,
) -> list[CommandSpec]:
    """Plan de verificacion: auditorias, guardas, grupos pedidos y, si se pide, todo.

    El grupo de guardas va siempre y no se repite aunque lo pidan con --group.
    """
    specs = _audit_specs()
    specs.append(group_spec(GUARD_GROUP))
    specs.extend(group_spec(group) for group in groups if group != GUARD_GROUP)
    if include_full_suite:
        specs.append(CommandSpec(
            "suite AXI", (sys.executable, "-m", "unittest", "discover", "-s", "tests"),
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


USAGE = (
    "uso: python -m tools.verify_project [--full] [--group NOMBRE]... [--list-groups]\n"
    "     --quick se mantiene como alias del modo por defecto.\n"
    "     grupos: " + ", ".join(group_names())
)


def parse_args(argv: list[str]) -> Plan:
    groups: list[str] = []
    include_full_suite = False
    list_groups = False
    pending_group = False
    for argument in argv:
        if pending_group:
            modules_for(argument)  # valida el nombre antes de lanzar nada
            groups.append(argument)
            pending_group = False
            continue
        if argument == "--group":
            pending_group = True
        elif argument.startswith("--group="):
            name = argument.split("=", 1)[1]
            modules_for(name)
            groups.append(name)
        elif argument == "--full":
            include_full_suite = True
        elif argument == "--list-groups":
            list_groups = True
        elif argument == "--quick":
            # El modo por defecto ya es el rapido; se acepta por compatibilidad
            # con las notas y los scripts que lo pasaban.
            pass
        else:
            raise ValueError(USAGE)
    if pending_group:
        raise ValueError(USAGE)
    return Plan(tuple(groups), include_full_suite, list_groups)


def print_groups() -> None:
    for group in group_names():
        print(f"{group}: {len(modules_for(group))} modulo(s)")


def main(argv: list[str] | None = None) -> int:
    try:
        plan = parse_args(list(sys.argv[1:] if argv is None else argv))
    except ValueError as error:
        print(error)
        return 2
    if plan.list_groups:
        print_groups()
        return 0
    before = repository_state()
    specs = command_specs(
        groups=plan.groups, include_full_suite=plan.include_full_suite,
    )
    failed = [spec.name for spec in specs if not run_spec(spec)]
    changed = repository_state() != before
    if changed:
        print("[CAMBIO CONCURRENTE] HEAD o estado tracked cambio durante la verificacion")
    if failed:
        print("[FALLO] " + ", ".join(failed))
    if failed or changed:
        return 1
    scope = "contrato completo" if plan.include_full_suite else "contrato verificado"
    print(f"\n[OK] {scope} del agente AXI")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
