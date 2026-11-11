"""La raiz es una lista cerrada, y cada carpeta tiene un dueno.

La raiz habia juntado 33 modulos `ubs_agent_*.py`, once `run_tests_*.py`, cinco
`.bat`, dos plantillas y el documento de requisitos, todos «porque era donde se
veian mejor». Nadie los puso ahi con mala intencion: sin lista, la raiz es el
sitio por defecto de cualquier cosa, y una vez dentro no vuelve a salir.

Esta guarda fija cuatro cosas:

- Que no aparezca en la raiz nada que no busque algo ahi por nombre fijo. El
  mensaje dice el nombre y la carpeta que le toca.
- Que las dos tablas de `AGENTS.md` digan exactamente lo mismo que estas
  listas. El documento es lo que se lee para orientarse; si discrepan, miente
  sin que nadie se entere.
- Que las carpetas de codigo declaradas existan: una carpeta documentada que no
  existe es una promesa vacia.
- Que `tests/` no vuelva a acumular `test_*.py` suelto.

No se anade un nombre a estas listas para que el test pase. Se elige la carpeta
a la que pertenece el fichero.
"""
from __future__ import annotations

import re
import unittest

from tests import PROJECT_ROOT

AGENTS = PROJECT_ROOT / "AGENTS.md"
ROOT_HEADING = "## Dónde va cada cosa: la raíz es una lista cerrada"
DIRS_HEADING = "Y cada carpeta tiene un dueño:"

#: Lo unico que puede vivir en la raiz. El motivo de cada uno esta en la
#: primera tabla de `AGENTS.md`.
ROOT_FILES = frozenset({
    "pyproject.toml",
    "requirements.txt",
    ".gitignore",
    ".gitattributes",
    ".mcp.json",
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "app_ui.py",
    "run_tests.py",
    "compile_mq5.py",
    "compile_and_backtest.py",
    "ubs_agent.py",
    "manager_node_lifecycle.py",
    "experts_list.txt",
    "experts_root.txt",
    "compile_root.txt",
    "tester_template.ini",
    "ui_settings.ini",
    "manager_node.json",
    ".env",
})

#: Los `.env` por maquina (`.env.central-db` en AXI) son secretos ignorados y
#: tienen que estar donde `common/mt5_env.py` los lee.
ROOT_FILE_PATTERNS = (re.compile(r"^\.env\."),)

#: Carpetas de codigo y documentacion con dueno. Cada una esta en la segunda
#: tabla de `AGENTS.md`, y todas tienen que existir.
SOURCE_DIRS = frozenset({
    "backtest",
    "common",
    "ubs",
    "ui",
    "manager_node_runtime",
    "portfolio_manager",
    "parsers",
    "tests",
    "tools",
    "ai_context",
    "config",
    "docs",
    "assets",
    "scripts",
})

#: Generado por la aplicacion y ignorado por Git. No se documenta como
#: responsabilidad de nadie y no tiene por que existir en cada equipo: un
#: checkout recien clonado no tiene `reports/` hasta el primer backtest.
DATA_DIRS = frozenset({
    "configs", "logs", "outputs", "reports", "runtime", "sets", "live_sets",
    "sets_1.7", "experts", "portfolio_logs", "tmp",
})

#: Carpetas de herramienta. No las escribe nadie a mano.
TOOLING_DIRS = frozenset({
    ".git", ".github", ".claude", ".codex", ".venv", "venv", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".idea", ".vscode",
    "node_modules", "build", "dist", "build_installer", "dist_installer",
})

#: A donde va lo que no es manifiesto, por si el mensaje tiene que decirlo.
DESTINATIONS = (
    (re.compile(r"\.(bak|orig|log|tmp)$"), "runtime/ (desechable)"),
    (re.compile(r"\.example\.(json|ini)$|^\.env\.example$"), "config/ (plantilla)"),
    (re.compile(r"\.(bat|ps1|sh|cmd)$"), "scripts/"),
    (re.compile(r"^test_.*\.py$"), "tests/<subsistema>/"),
    (re.compile(r"^ubs_.*\.py$"), "ubs/"),
    (re.compile(r"^run_tests_.*\.py$"), "backtest/"),
    (re.compile(r"\.py$"), "su paquete de dominio; ver la tabla de AGENTS.md"),
    (re.compile(r"\.md$"), "docs/ o ai_context/ (con su linea en el indice)"),
    (re.compile(r"\.(xlsx|csv|htm|html)$"), "outputs/ o reports/"),
)


def destination(name: str) -> str:
    for pattern, folder in DESTINATIONS:
        if pattern.search(name):
            return folder
    return "una carpeta con dueno; ver la tabla de AGENTS.md"


def allowed_root_file(name: str) -> bool:
    return name in ROOT_FILES or any(p.search(name) for p in ROOT_FILE_PATTERNS)


def documented(heading: str, until: str | None = None) -> set[str]:
    """Los nombres entrecomillados de la primera columna de una tabla.

    Las dos tablas viven en la misma seccion, asi que la de la raiz necesita
    `until`: sin el, leeria tambien los nombres de las carpetas y la
    comparacion no distinguiria una tabla de la otra.
    """
    text = AGENTS.read_text(encoding="utf-8")
    start = text.index(heading) + len(heading)
    end = text.index(until, start) if until else text.find("\n## ", start)
    names: set[str] = set()
    for line in text[start: end if end != -1 else len(text)].splitlines():
        if not line.startswith("|") or line.startswith("| ---"):
            continue
        names.update(re.findall(r"`([^`]+)`", line.split("|")[1]))
    return {name.rstrip("/") for name in names}


class RootIsAClosedListTest(unittest.TestCase):
    def test_no_file_lives_in_the_root_that_is_not_declared(self) -> None:
        intruders = {
            path.name: destination(path.name)
            for path in PROJECT_ROOT.iterdir()
            if path.is_file() and not allowed_root_file(path.name)
        }
        self.assertFalse(
            intruders,
            "La raiz es una lista cerrada. Esto pertenece a otra carpeta:\n"
            + "\n".join(f"  {name} -> {folder}" for name, folder in sorted(intruders.items())),
        )

    def test_every_top_level_directory_has_a_declared_owner(self) -> None:
        undeclared = sorted(
            path.name for path in PROJECT_ROOT.iterdir()
            if path.is_dir() and path.name not in SOURCE_DIRS | DATA_DIRS | TOOLING_DIRS
        )
        self.assertFalse(
            undeclared,
            "Carpeta de primer nivel sin dueno declarado. Si es del proyecto, "
            "entra en SOURCE_DIRS y en la tabla de AGENTS.md; si la genera la "
            f"aplicacion, en DATA_DIRS y en .gitignore: {undeclared}",
        )

    def test_the_declared_source_directories_exist(self) -> None:
        missing = sorted(name for name in SOURCE_DIRS if not (PROJECT_ROOT / name).is_dir())
        self.assertFalse(missing, f"Carpeta declarada que no existe: {missing}")

    def test_agents_md_documents_exactly_the_root_list(self) -> None:
        """La tabla y la lista no pueden discrepar: el documento es lo que se lee."""
        table = documented(ROOT_HEADING, until=DIRS_HEADING)
        self.assertTrue(table, "no se encontro la tabla de la raiz en AGENTS.md")
        self.assertEqual(
            ROOT_FILES, table,
            "la tabla de la raiz de AGENTS.md y ROOT_FILES no coinciden",
        )

    def test_agents_md_documents_exactly_the_owned_directories(self) -> None:
        table = documented(DIRS_HEADING)
        self.assertTrue(table, "no se encontro la tabla de carpetas en AGENTS.md")
        self.assertEqual(SOURCE_DIRS, table)

    def test_the_tests_root_keeps_no_loose_test_module(self) -> None:
        """El reparto de `tests/` se deshace solo si nadie lo vigila."""
        loose = sorted(path.name for path in (PROJECT_ROOT / "tests").glob("test_*.py"))
        self.assertFalse(
            loose,
            "Las pruebas van en su subsistema, no en la raiz de tests/: "
            f"{loose}",
        )


if __name__ == "__main__":
    unittest.main()
