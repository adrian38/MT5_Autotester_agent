"""Comprueba que cada nota técnica figure en ``ai_context/main.md``."""
from pathlib import Path

CONTEXT = Path(__file__).resolve().parents[1] / "ai_context"
INDEX = CONTEXT / "main.md"


def notes() -> list[Path]:
    return sorted(path for path in CONTEXT.glob("*.md") if path.name != INDEX.name)


def missing() -> list[Path]:
    index = INDEX.read_text(encoding="utf-8")
    return [path for path in notes() if path.name not in index]


def main() -> int:
    absent = missing()
    for path in absent:
        print(f"sin indexar: {path.name}")
    print(f"{len(notes())} notas, {len(absent)} sin indexar")
    return 1 if absent else 0


if __name__ == "__main__":
    raise SystemExit(main())
