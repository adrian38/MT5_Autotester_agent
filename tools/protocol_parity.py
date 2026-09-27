"""Comprueba el protocolo guiado contra el manager hermano, si está montado."""
from __future__ import annotations

from pathlib import Path

from tools.source_files import ROOT

FILES = ("guided_batches.py", "guided_controller.py")
MANAGER = ROOT.parents[1] / "MT5_Autotester_agent_manager" / "mt5_manager"


def normalized_bytes(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def mismatches(manager: Path = MANAGER) -> list[str]:
    if not manager.is_dir():
        return []
    return [name for name in FILES
            if normalized_bytes(ROOT / "manager_node_runtime" / name)
            != normalized_bytes(manager / name)]


def main() -> int:
    if not MANAGER.is_dir():
        print(f"[OMITIDO] manager hermano no montado en {MANAGER}")
        return 0
    failed = mismatches()
    for name in failed:
        print(f"protocolo divergente: {name}")
    print(f"{len(FILES)} ficheros de protocolo, {len(failed)} divergencias")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
