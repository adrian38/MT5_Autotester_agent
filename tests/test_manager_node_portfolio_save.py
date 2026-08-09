from __future__ import annotations

import contextlib
import sqlite3
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from manager_node_runtime.node import JobController
from manager_node_runtime.portfolio_save import (
    exclude_portfolio_members_payload,
    save_portfolio_payload,
)
from portfolio_manager.ubs_portfolio import PortfolioResult, StrategyAllocation


class ManagerNodePortfolioSaveTests(unittest.TestCase):
    @staticmethod
    def _proposal(key: str, label: str, units: int, request_id: str) -> dict[str, object]:
        inputs = {
            "capital": 5000,
            "valley_dd_pct": 6,
            "point_dd_pct": 6,
            "portfolio_type": key,
            "composition_portfolio_type": "balanced",
            "portfolio_scope": "full_history",
            "_manager_save_request_id": request_id,
        }
        allocation = StrategyAllocation(
            "same.set",
            "ICTRADING/STANDARD:1",
            "EURUSD",
            units,
            units * 0.01,
            100 * units,
            20 * units,
            10 * units,
            "H1",
            "same.set",
            "is.html",
            "oos.html",
            0.01,
        )
        result = PortfolioResult(
            [allocation],
            [0, 100 * units],
            100 * units,
            20 * units,
            10 * units,
            300,
            300,
            10,
            5,
            units * 0.01,
            units,
            1,
            "ok",
            [],
            [],
        )
        return {
            "key": key,
            "label": label,
            "reserve_pct": 10,
            "inputs": inputs,
            "result": asdict(result),
        }

    def _payload(
        self,
        request_id: str,
        *,
        operation: str = "generate",
        portfolio_id: int | None = None,
        balanced_units: int = 2,
    ) -> dict[str, object]:
        return {
            "scope": "full_history",
            "selected_key": "balanced",
            "operation": operation,
            "portfolio_id": portfolio_id,
            "request_id": request_id,
            "proposals": [
                self._proposal("aggressive", "Agresivo", 3, request_id),
                self._proposal("balanced", "Moderado", balanced_units, request_id),
                self._proposal("conservative", "Conservador", 1, request_id),
            ],
        }

    def test_save_is_local_idempotent_and_reoptimization_keeps_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = Path(temp_dir) / "ubs_memory_ICTRADING_STANDARD.sqlite"
            memory.touch()

            first_payload = self._payload("request-first")
            first = save_portfolio_payload(memory, first_payload)
            retry = save_portfolio_payload(memory, first_payload)
            portfolio_id = int(first["portfolio_id"])

            self.assertFalse(first["deduplicated"])
            self.assertTrue(retry["deduplicated"])
            self.assertEqual(retry["portfolio_id"], portfolio_id)

            updated = save_portfolio_payload(
                memory,
                self._payload(
                    "request-reoptimize",
                    operation="reoptimize",
                    portfolio_id=portfolio_id,
                    balanced_units=4,
                ),
            )
            self.assertEqual(updated["portfolio_id"], portfolio_id)

            with contextlib.closing(sqlite3.connect(memory)) as conn:
                variants = conn.execute(
                    "select variant_key,units from portfolio_allocations "
                    "where portfolio_id=? order by variant_key",
                    (portfolio_id,),
                ).fetchall()
                version_count = conn.execute(
                    "select count(*) from portfolio_versions where portfolio_id=?",
                    (portfolio_id,),
                ).fetchone()[0]
                portfolio_count = conn.execute("select count(*) from portfolios").fetchone()[0]

            self.assertEqual(dict(variants)["balanced"], 4)
            self.assertEqual({key for key, _units in variants}, {"aggressive", "balanced", "conservative"})
            self.assertEqual(version_count, 1)
            self.assertEqual(portfolio_count, 1)

    def test_delete_runs_locally_and_removes_parent_and_children(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = Path(temp_dir) / "ubs_memory_ICTRADING_STANDARD.sqlite"
            memory.touch()
            saved = save_portfolio_payload(memory, self._payload("request-delete"))
            portfolio_id = int(saved["portfolio_id"])
            controller = SimpleNamespace(
                _settings_and_memory=lambda: (None, memory),
                _persist=lambda: None,
            )

            result = JobController.delete_portfolio(controller, {
                "scope": "full_history", "portfolio_id": portfolio_id,
            })

            self.assertEqual(result, {
                "deleted": True, "portfolio_id": portfolio_id, "scope": "full_history",
            })
            with contextlib.closing(sqlite3.connect(memory)) as conn:
                self.assertEqual(conn.execute(
                    "select count(*) from portfolios where id=?", (portfolio_id,)
                ).fetchone()[0], 0)
                self.assertEqual(conn.execute(
                    "select count(*) from portfolio_allocations where portfolio_id=?", (portfolio_id,)
                ).fetchone()[0], 0)

    def test_batch_exclusion_is_local_and_keeps_the_saved_bundle(self) -> None:
        # El portafolio guardado no es un efecto colateral de una decision sobre
        # el pool: excluir pone en cuarentena y deja el A/M/C intacto.
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            memory = project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"
            memory.parent.mkdir()
            memory.touch()
            saved = save_portfolio_payload(memory, self._payload("request-batch-exclude"))
            portfolio_id = int(saved["portfolio_id"])

            result = exclude_portfolio_members_payload(
                project,
                "ICTRADING",
                memory,
                {
                    "scope": "full_history",
                    "portfolio_id": portfolio_id,
                    "set_paths": ["same.set", "same.set"],
                },
            )

            self.assertFalse(result["deleted"])
            self.assertEqual(result["portfolio_id"], portfolio_id)
            self.assertEqual(len(result["quarantine_ids"]), 1)
            with contextlib.closing(sqlite3.connect(memory)) as conn:
                self.assertEqual(
                    conn.execute("select count(*) from portfolios where id=?", (portfolio_id,)).fetchone()[0],
                    1,
                )
                # Las tres asignaciones A/M/C siguen ahi: no se toca ninguna.
                self.assertEqual(
                    conn.execute(
                        "select count(*) from portfolio_allocations where portfolio_id=?", (portfolio_id,)
                    ).fetchone()[0],
                    3,
                )
                quarantine = conn.execute(
                    "select set_path,source_portfolio_id from portfolio_quarantine"
                ).fetchone()
            self.assertEqual(quarantine, ("same.set", portfolio_id))

    def test_batch_exclusion_also_works_on_a_monthly_portfolio(self) -> None:
        # Un mes guardado no es un bundle y aun así se borra completo al excluir,
        # así que la exclusión múltiple vale igual. La fila se ajusta a mano en
        # lugar de recorrer el guardado mensual: la exclusión solo lee el ámbito,
        # el tipo, las métricas y las asignaciones del portafolio.
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            memory = project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"
            memory.parent.mkdir()
            memory.touch()
            saved = save_portfolio_payload(memory, self._payload("request-monthly-exclude"))
            portfolio_id = int(saved["portfolio_id"])
            with contextlib.closing(sqlite3.connect(memory)) as conn:
                conn.execute(
                    "update portfolios set portfolio_scope='monthly',portfolio_type='aggressive',"
                    "type='aggressive',metrics_json='{}' where id=?",
                    (portfolio_id,),
                )
                conn.commit()

            result = exclude_portfolio_members_payload(
                project,
                "ICTRADING",
                memory,
                {
                    "scope": "monthly",
                    "portfolio_id": portfolio_id,
                    "set_paths": ["same.set"],
                },
            )

            self.assertFalse(result["deleted"])
            self.assertEqual(result["portfolio_id"], portfolio_id)
            self.assertEqual(result["scope"], "monthly")
            self.assertEqual(len(result["quarantine_ids"]), 1)
            with contextlib.closing(sqlite3.connect(memory)) as conn:
                self.assertEqual(
                    conn.execute("select count(*) from portfolios where id=?", (portfolio_id,)).fetchone()[0],
                    1,
                )
                reason = conn.execute("select reason from portfolio_quarantine").fetchone()[0]
            self.assertIn("Portafolio UBS mensual", reason)


CANDIDATE_STAGES = """
create table if not exists candidates(id integer primary key,run_id integer not null default 1,
    set_path text,symbol text,target_symbol text,period text,family text,report_path text,
    status text,score real,accepted integer,metrics_json text,seed_path text,generation integer);
create table if not exists candidate_robustness(candidate_id integer primary key,run_id integer not null default 1,
    status text,report_path text,score real,accepted integer,metrics_json text,
    from_date text not null default '',to_date text not null default '',
    positive_bonus real not null default 70.0,negative_bonus real not null default -70.0,
    evaluated_at text not null default '');
create table if not exists candidate_final_tick(candidate_id integer primary key,run_id integer not null default 1,
    status text,accepted integer,ohlc_report_path text,real_tick_report_path text,
    ohlc_score real,real_tick_score real,ohlc_metrics_json text,real_tick_metrics_json text,
    similarity_json text,history_quality real,min_history_quality real,
    from_date text not null default '',to_date text not null default '',
    max_net_delta_pct real,max_pf_delta_pct real,max_dd_delta_pct real,max_trades_delta_pct real,
    evaluated_at text not null default '');
create table if not exists candidate_final_tick_6m(candidate_id integer primary key,run_id integer not null default 1,
    status text,accepted integer,ohlc_report_path text,real_tick_report_path text,
    ohlc_score real,real_tick_score real,ohlc_metrics_json text,real_tick_metrics_json text,
    similarity_json text,history_quality real,min_history_quality real,
    from_date text not null default '',to_date text not null default '',
    max_net_delta_pct real,max_pf_delta_pct real,max_dd_delta_pct real,max_trades_delta_pct real,
    evaluated_at text not null default '');
insert into candidates(id,run_id,set_path,symbol,status,score) values(1,1,'same.set','EURUSD','accepted',91.5);
insert into candidate_robustness(candidate_id,run_id,status,accepted,score) values(1,1,'accepted',1,88.0);
insert into candidate_final_tick(candidate_id,run_id,status,accepted) values(1,1,'accepted',1);
insert into candidate_final_tick_6m(candidate_id,run_id,status,accepted) values(1,1,'accepted',1);
"""


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
            memory, ManagerNodePortfolioSaveTests()._payload("request-verdict")
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


if __name__ == "__main__":
    unittest.main()
