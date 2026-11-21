import shutil
import tempfile
import unittest
from pathlib import Path

from ubs.universe import policy_with_universe_identities, save_disabled_symbols
from ui.ubs_seeds_eval import UBSSeedsEvalMixin


AXI_UNIVERSE_INI = """[Indices]
symbols = GER40.sa, USTECH.sa, US500.sa
"""

AXI_SYMBOL_MAP = "DE40=GER40,USTEC=USTECH,SPX500=US500"


class _PlanStub(UBSSeedsEvalMixin):
    """Lo minimo que `_ubs_seed_eval_plan` le pide a la aplicacion."""

    def __init__(self, policy_path: Path, universe_path: Path, fields: dict) -> None:
        self._policy_path = policy_path
        self._universe_path = universe_path
        self._fields = fields

    def _ubs_memory_path(self) -> Path:
        return self._policy_path.parent / "memoria-que-no-existe.sqlite"

    def _ubs_symbol_policy_with_identities(self) -> tuple[set, set]:
        return policy_with_universe_identities(self._policy_path, self._universe_path)

    def _effective_ubs_symbol_map_text(self) -> str:
        return AXI_SYMBOL_MAP

    def _inferred_ubs_seed_fields(self, path: Path) -> tuple[str, str]:
        return self._fields[path.name]


class SeedEvalPlanPolicyTests(unittest.TestCase):
    """El dialogo previo cuenta lo mismo que luego ejecuta el agente."""

    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.universe = self.directory / "assets.ini"
        self.universe.write_text(AXI_UNIVERSE_INI, encoding="utf-8")
        self.policy = self.directory / "ubs_disabled_symbols_AXI_STANDARD.json"

    def _seed(self, name: str) -> Path:
        path = self.directory / name
        path.write_text("Symbol=X\n", encoding="utf-8")
        return path

    def _plan(self, seeds: list[Path], fields: dict) -> dict:
        stub = _PlanStub(self.policy, self.universe, fields)
        return stub._ubs_seed_eval_plan(seeds)

    def test_seed_is_counted_disabled_although_policy_carries_the_suffix(self) -> None:
        # La politica dice `GER40.SA` y el mapa deja `GER40`: sin las
        # identidades del universo el dialogo prometia una pasada que el
        # agente luego no hacia.
        save_disabled_symbols(self.policy, {"GER40.SA"}, set())
        seed = self._seed("ORB_Master__DAX_M15_a.set")

        plan = self._plan([seed], {seed.name: ("DE40", "M15")})

        self.assertEqual(plan["disabled"], 1)
        self.assertEqual(plan["pending"], 0)

    def test_seed_permission_survives_the_identity(self) -> None:
        # El ensanche vale para las dos listas: con SEEDS activo la seed corre.
        save_disabled_symbols(self.policy, {"USTECH.SA"}, {"USTECH.SA"})
        seed = self._seed("Till__UBS-USTEC-H1.set")

        plan = self._plan([seed], {seed.name: ("USTEC", "H1")})

        self.assertEqual(plan["disabled"], 0)
        self.assertEqual(plan["pending"], 1)

    def test_enabled_symbol_stays_pending(self) -> None:
        save_disabled_symbols(self.policy, {"GER40.SA"}, set())
        seed = self._seed("Till__UBS-SPX500-H1.set")

        plan = self._plan([seed], {seed.name: ("SPX500", "H1")})

        self.assertEqual(plan["disabled"], 0)
        self.assertEqual(plan["pending"], 1)


if __name__ == "__main__":
    unittest.main()
