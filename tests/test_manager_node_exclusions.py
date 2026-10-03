from __future__ import annotations

import contextlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from manager_node_runtime.portfolio_save import (
    exclude_portfolio_members_payload,
    requalify_portfolio_member_payload,
    save_portfolio_payload,
)
from tests.manager_node_portfolio_fixtures import CANDIDATE_STAGES, portfolio_payload


class ManagerNodeExclusionVerdictTests(unittest.TestCase):
    """El motivo de la exclusion decide si se escribe un veredicto de etapa.

    Esta es la copia que se ejecuta de verdad: el manager envia `reason_code` y
    aqui se llama a `ubs.manual_status`, la misma primitiva que el FAIL manual de
    la aplicacion. La regla equivalente del manager esta en
    `mt5_manager/candidate_verdict.py`; si divergen, la pantalla promete un
    cambio de estados que este proceso no hace.
    """

    def _memory_with_candidate(self, project: Path) -> Path:
        memory = project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"
        memory.parent.mkdir()
        memory.touch()
        saved = save_portfolio_payload(
            memory, portfolio_payload("request-verdict")
        )
        with contextlib.closing(sqlite3.connect(memory)) as conn:
            conn.executescript(CANDIDATE_STAGES)
            conn.commit()
        return memory, int(saved["portfolio_id"])

    def _stages(self, memory: Path) -> dict[str, object]:
        with contextlib.closing(sqlite3.connect(memory)) as conn:
            return {
                table: conn.execute(f"select status from {table} where candidate_id=1").fetchall()
                for table in (
                    "candidate_robustness", "candidate_final_tick", "candidate_final_tick_6m",
                )
            }

    def _exclude(self, project: Path, memory: Path, portfolio_id: int, reason_code: str) -> dict:
        return exclude_portfolio_members_payload(
            project,
            "ICTRADING",
            memory,
            {
                "scope": "full_history",
                "portfolio_id": portfolio_id,
                "set_paths": ["same.set"],
                "reason_code": reason_code,
            },
        )

    def test_a_degradation_exclusion_rejects_robustness_and_drops_the_later_stages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            memory, portfolio_id = self._memory_with_candidate(project)

            result = self._exclude(project, memory, portfolio_id, "degradation")

            self.assertTrue(result["verdict_applied"])
            self.assertEqual(result["reason_code"], "degradation")
            stages = self._stages(memory)
            self.assertEqual(stages["candidate_robustness"], [("rejected",)])
            self.assertEqual(stages["candidate_final_tick"], [])
            self.assertEqual(stages["candidate_final_tick_6m"], [])
            with contextlib.closing(sqlite3.connect(memory)) as conn:
                row = conn.execute(
                    "select reason_code,restore_json from portfolio_quarantine"
                ).fetchone()
            self.assertEqual(row[0], "degradation")
            # Sin el respaldo, reintegrar dejaria al candidato fuera para siempre.
            self.assertIn("candidate_final_tick_6m", row[1])

    def test_an_ohlc_mismatch_exclusion_rejects_only_the_six_month_final_tick(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            memory, portfolio_id = self._memory_with_candidate(project)

            result = self._exclude(project, memory, portfolio_id, "ohlc_mismatch")

            self.assertTrue(result["verdict_applied"])
            stages = self._stages(memory)
            self.assertEqual(stages["candidate_final_tick_6m"], [("rejected",)])
            self.assertEqual(stages["candidate_robustness"], [("accepted",)])
            self.assertEqual(stages["candidate_final_tick"], [("accepted",)])

    def test_a_manual_exclusion_leaves_every_stage_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            memory, portfolio_id = self._memory_with_candidate(project)
            before = self._stages(memory)

            result = self._exclude(project, memory, portfolio_id, "manual")

            self.assertFalse(result["verdict_applied"])
            self.assertEqual(self._stages(memory), before)
            with contextlib.closing(sqlite3.connect(memory)) as conn:
                row = conn.execute(
                    "select reason_code,restore_json from portfolio_quarantine"
                ).fetchone()
            self.assertEqual(row, ("manual", None))

    def test_an_unknown_reason_code_never_escalates_to_a_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            memory, portfolio_id = self._memory_with_candidate(project)
            before = self._stages(memory)

            result = self._exclude(project, memory, portfolio_id, "lo-que-sea")

            self.assertFalse(result["verdict_applied"])
            self.assertEqual(result["reason_code"], "manual")
            self.assertEqual(self._stages(memory), before)


class ManagerNodePoolExclusionTests(unittest.TestCase):
    """Excluir un set que no esta en ningun portafolio.

    Es lo que hace la ventana «Gestion por simbolo» del manager. Antes esto
    abortaba en la primera linea con «Falta el portafolio que contiene las
    estrategias», porque el unico sitio donde se buscaba al miembro era
    `portfolio_allocations`: un set que ningun portafolio usara no habia forma de
    sacarlo del pool. Resolverlo aqui es imposible —la ruta que manda el manager
    no es la que guarda esta memoria—, asi que el manager lo resuelve
    (`PortfolioSource.pool_member_payload`) y lo manda en `pool_member`.
    """

    def _memory(self, project: Path) -> Path:
        memory = project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"
        memory.parent.mkdir()
        memory.touch()
        with contextlib.closing(sqlite3.connect(memory)) as conn:
            conn.executescript(CANDIDATE_STAGES)
            conn.commit()
        return memory

    def _exclude(self, project: Path, memory: Path, reason_code: str, **overrides) -> dict:
        payload = {
            "scope": "full_history",
            "set_path": "same.set",
            "reason_code": reason_code,
            "pool_member": {
                "set_path": "same.set",
                "candidate_id": "ICTRADING/STANDARD:1",
                "symbol": "EURUSD",
                "timeframe": "H1",
            },
        }
        payload.update(overrides)
        return exclude_portfolio_members_payload(project, "ICTRADING", memory, payload)

    def test_a_set_outside_every_portfolio_can_be_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            memory = self._memory(project)

            result = self._exclude(project, memory, "degradation")

            self.assertTrue(result["pool_exclusion"])
            self.assertTrue(result["verdict_applied"])
            self.assertIsNone(result["portfolio_id"])
            with contextlib.closing(sqlite3.connect(memory)) as conn:
                row = conn.execute(
                    "select account_type,symbol,timeframe,reason,source_portfolio_id,reason_code,"
                    "restore_json from portfolio_quarantine"
                ).fetchone()
                robustness = conn.execute(
                    "select status from candidate_robustness where candidate_id=1"
                ).fetchall()
            self.assertEqual(row[0], "ICTRADING/STANDARD")
            self.assertEqual(row[1], "EURUSD")
            self.assertEqual(row[2], "H1")
            self.assertIn("Excluida manualmente desde la gestión por símbolo", row[3])
            # No sale de ningun portafolio: la columna no puede inventarse uno.
            self.assertIsNone(row[4])
            self.assertEqual(row[5], "degradation")
            self.assertIn("candidate_final_tick_6m", row[6])
            # El veredicto es lo que la saca del pool en la siguiente generacion.
            self.assertEqual(robustness, [("rejected",)])

    def test_a_pool_exclusion_without_the_resolved_candidate_is_rejected(self) -> None:
        # El manager es quien sabe traducir la ruta; sin su resultado esto no
        # puede adivinar a que candidato pertenece el set.
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            memory = self._memory(project)

            with self.assertRaises(ValueError) as raised:
                self._exclude(project, memory, "degradation", pool_member=None)

            self.assertIn("Falta el portafolio que contiene las estrategias", str(raised.exception))

    def test_batch_exclusion_still_needs_a_portfolio(self) -> None:
        # Las casillas solo existen en el detalle de un portafolio guardado; una
        # exclusion multiple sin portafolio seria una peticion mal formada.
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            memory = self._memory(project)

            with self.assertRaises(ValueError) as raised:
                exclude_portfolio_members_payload(
                    project, "ICTRADING", memory,
                    {"scope": "full_history", "set_paths": ["same.set"], "reason_code": "manual"},
                )

            self.assertIn("Falta el portafolio que contiene las estrategias", str(raised.exception))


class ManagerNodeRequalifyTests(unittest.TestCase):
    """Cambiar el estado de una estrategia ya excluida corre en el nodo.

    El manager solo lee esta memoria por una copia: escribirla por CIFS o por un
    bind mount de Docker falla con "disk I/O error" porque el modo WAL necesita un
    `-shm` que esos sistemas de ficheros no respaldan. Por eso el boton «Cambiar
    estado» pasa por `/api/v1/portfolios/requalify`.

    Reclasificar es deshacer el veredicto vigente y aplicar el nuevo, nunca
    encadenarlos. La regla equivalente del manager esta en
    `mt5_manager/portfolio_service.py::PortfolioSource.requalify_strategy`.
    """

    def _excluded(self, project: Path, reason_code: str = "degradation"):
        verdict_tests = ManagerNodeExclusionVerdictTests()
        memory, portfolio_id = verdict_tests._memory_with_candidate(project)
        pristine = verdict_tests._stages(memory)
        result = verdict_tests._exclude(project, memory, portfolio_id, reason_code)
        quarantine_id = int(result["quarantine_ids"][0])
        return memory, f"ICTRADING/STANDARD|{quarantine_id}", pristine, verdict_tests

    @staticmethod
    def _quarantine(memory: Path):
        with contextlib.closing(sqlite3.connect(memory)) as conn:
            return conn.execute(
                "select id,reason_code,reason,restore_json from portfolio_quarantine"
            ).fetchall()

    def _requalify(self, project: Path, memory: Path, key: str, reason_code: str) -> dict:
        return requalify_portfolio_member_payload(
            project, "ICTRADING", memory, {"quarantine_id": key, "reason_code": reason_code}
        )

    def test_moving_from_degradation_to_ohlc_undoes_the_first_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            memory, key, pristine, verdict_tests = self._excluded(project)

            result = self._requalify(project, memory, key, "ohlc_mismatch")

            self.assertTrue(result["requalified"])
            self.assertEqual(result["reason_code"], "ohlc_mismatch")
            self.assertEqual(result["previous_reason_code"], "degradation")
            stages = verdict_tests._stages(memory)
            # Robustez y el tick corto vuelven: solo fallo el 6M.
            self.assertEqual(stages["candidate_robustness"], pristine["candidate_robustness"])
            self.assertEqual(stages["candidate_final_tick"], pristine["candidate_final_tick"])
            self.assertEqual(stages["candidate_final_tick_6m"], [("rejected",)])
            row = self._quarantine(memory)[0]
            self.assertEqual(row[1], "ohlc_mismatch")
            # Un veredicto nuevo trae respaldo nuevo, del estado ya restaurado.
            self.assertIn("candidate_final_tick_6m", row[3])
            # El texto no acumula veredictos: conserva el origen y cambia el motivo.
            self.assertIn("Final Tick 6M", row[2])
            self.assertNotIn("test de robustez", row[2])

    def test_going_back_to_the_pool_restores_every_stage_and_drops_the_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            memory, key, pristine, verdict_tests = self._excluded(project)

            self._requalify(project, memory, key, "ohlc_mismatch")
            result = self._requalify(project, memory, key, "pool")

            self.assertEqual(result["reason_code"], "pool")
            self.assertEqual(verdict_tests._stages(memory), pristine)
            self.assertEqual(self._quarantine(memory), [])

    def test_moving_to_quarantine_keeps_the_row_without_a_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            memory, key, pristine, verdict_tests = self._excluded(project)

            self._requalify(project, memory, key, "manual")

            self.assertEqual(verdict_tests._stages(memory), pristine)
            row = self._quarantine(memory)[0]
            self.assertEqual(row[1], "manual")
            # Sin veredicto no hay nada que restaurar la proxima vez.
            self.assertIsNone(row[3])

    def test_the_same_state_changes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            memory, key, _pristine, verdict_tests = self._excluded(project)
            before = verdict_tests._stages(memory)
            row_before = self._quarantine(memory)

            result = self._requalify(project, memory, key, "degradation")

            self.assertEqual(result["reason_code"], "degradation")
            self.assertEqual(verdict_tests._stages(memory), before)
            self.assertEqual(self._quarantine(memory), row_before)

    def test_an_unknown_quarantine_row_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            memory, _key, _pristine, _verdict_tests = self._excluded(project)

            with self.assertRaises(ValueError):
                self._requalify(project, memory, "ICTRADING/STANDARD|999", "pool")
            with self.assertRaises(ValueError):
                self._requalify(project, memory, "", "pool")


if __name__ == "__main__":
    unittest.main()
