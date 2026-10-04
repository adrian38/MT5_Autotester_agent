from __future__ import annotations

import contextlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from manager_node_runtime.node import JobController
from manager_node_runtime.portfolio_save import (
    exclude_portfolio_members_payload,
    save_portfolio_payload,
    set_portfolio_alias_payload,
)
from tests.manager_node_portfolio_fixtures import portfolio_payload, portfolio_proposal


class ManagerNodePortfolioSaveTests(unittest.TestCase):
    def test_alias_is_additional_editable_and_removable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = Path(temp_dir) / "memory.sqlite"
            memory.touch()
            portfolio_id = save_portfolio_payload(memory, portfolio_payload("alias-base"))["portfolio_id"]

            result = set_portfolio_alias_payload(memory, {
                "scope": "full_history",
                "portfolio_id": portfolio_id,
                "alias": "  Londres   estable  ",
            })
            self.assertEqual(result["alias"], "Londres estable")
            with contextlib.closing(sqlite3.connect(memory)) as conn:
                row = conn.execute(
                    "select name,metrics_json from portfolios where id=?", (portfolio_id,)
                ).fetchone()
            self.assertNotEqual(row[0], "Londres estable")
            self.assertEqual(
                json.loads(row[1])["inputs"]["portfolio_alias"], "Londres estable"
            )

            cleared = set_portfolio_alias_payload(memory, {
                "scope": "full_history", "portfolio_id": portfolio_id, "alias": "",
            })
            self.assertEqual(cleared["alias"], "")
            with contextlib.closing(sqlite3.connect(memory)) as conn:
                metrics = json.loads(conn.execute(
                    "select metrics_json from portfolios where id=?", (portfolio_id,)
                ).fetchone()[0])
            self.assertNotIn("portfolio_alias", metrics["inputs"])

            with self.assertRaisesRegex(ValueError, "80 caracteres"):
                set_portfolio_alias_payload(memory, {
                    "scope": "full_history", "portfolio_id": portfolio_id, "alias": "x" * 81,
                })

    def test_improvement_is_new_named_single_mode_and_preserves_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = Path(temp_dir) / "memory.sqlite"
            memory.touch()
            original = save_portfolio_payload(memory, portfolio_payload("original"))["portfolio_id"]
            with contextlib.closing(sqlite3.connect(memory)) as conn:
                before = conn.execute("select * from portfolios where id=?", (original,)).fetchone()
                members_before = conn.execute("select * from portfolio_allocations where portfolio_id=?", (original,)).fetchall()
            improvement = portfolio_proposal("conservative", "Conservador", 2, "improvement")
            improvement["inputs"]["improvement_source_portfolio_id"] = original
            improvement["inputs"]["improvement_label"] = f"Mejora del portafolio #{original} | modo Conservador"
            payload = {"scope": "full_history", "operation": "generate", "selected_key": "conservative", "request_id": "improvement", "proposals": [improvement]}
            saved = save_portfolio_payload(memory, payload)
            retry = save_portfolio_payload(memory, payload)
            self.assertNotEqual(saved["portfolio_id"], original)
            self.assertEqual(saved["portfolio_id"], retry["portfolio_id"])
            self.assertTrue(retry["deduplicated"])
            with contextlib.closing(sqlite3.connect(memory)) as conn:
                self.assertEqual(before, conn.execute("select * from portfolios where id=?", (original,)).fetchone())
                self.assertEqual(members_before, conn.execute("select * from portfolio_allocations where portfolio_id=?", (original,)).fetchall())
                row = conn.execute("select name,portfolio_type,metrics_json from portfolios where id=?", (saved["portfolio_id"],)).fetchone()
                self.assertEqual(conn.execute("select count(*) from portfolios").fetchone()[0], 2)
            self.assertEqual(row[0], f"Mejora del portafolio #{original} | modo Conservador")
            self.assertEqual(row[1], "conservative")
            metrics = json.loads(row[2])
            self.assertFalse(metrics.get("portfolio_bundle", False))

    def test_save_is_local_idempotent_and_reoptimization_keeps_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = Path(temp_dir) / "ubs_memory_ICTRADING_STANDARD.sqlite"
            memory.touch()

            first_payload = portfolio_payload("request-first")
            first = save_portfolio_payload(memory, first_payload)
            retry = save_portfolio_payload(memory, first_payload)
            portfolio_id = int(first["portfolio_id"])

            self.assertFalse(first["deduplicated"])
            self.assertTrue(retry["deduplicated"])
            self.assertEqual(retry["portfolio_id"], portfolio_id)

            updated = save_portfolio_payload(
                memory,
                portfolio_payload(
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

    def test_a_payload_from_a_newer_manager_saves_at_the_first_attempt(self) -> None:
        # El nodo debe conservar la tanda de riesgo por equity y los campos de
        # auditoría, además de aceptar el payload sin el reintento legacy.
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = Path(temp_dir) / "ubs_memory_ICTRADING_STANDARD.sqlite"
            memory.touch()

            payload = portfolio_payload("request-newer-manager")
            for proposal in payload["proposals"]:
                result = proposal["result"]
                result["actual_closed_valley_dd"] = 50.0
                result["floating_dd_buffer"] = 30.0
                result["floating_overlap_audit"] = {"pairs": 0}
                for allocation in result["allocations"]:
                    allocation["max_balance_dd_001"] = 10.0
                    allocation["max_equity_dd_001"] = 25.0
                    allocation["floating_dd_source"] = "2020-2024"
                    allocation["standalone_floating_dd"] = 30.0
                    allocation["recent_net_profit_001"] = 12.0
                    allocation["recent_equity_dd_001"] = 8.0
                    allocation["has_recent_performance"] = True
                    allocation["final_tick_report_path"] = "final.html"
                    allocation["full_history_report_path"] = "full.html"

            saved = save_portfolio_payload(memory, payload)
            portfolio_id = int(saved["portfolio_id"])

            self.assertFalse(saved["deduplicated"])
            with contextlib.closing(sqlite3.connect(memory)) as conn:
                conn.row_factory = sqlite3.Row
                allocation = conn.execute(
                    "select * from portfolio_allocations "
                    "where portfolio_id=? and variant_key='balanced'",
                    (portfolio_id,),
                ).fetchone()
                portfolio = conn.execute(
                    "select * from portfolios where id=?", (portfolio_id,)
                ).fetchone()
                metrics = json.loads(portfolio["metrics_json"])
            self.assertEqual(allocation["units"], 2)
            self.assertEqual(allocation["max_equity_dd_001"], 25.0)
            self.assertEqual(allocation["floating_dd_source"], "2020-2024")
            self.assertEqual(allocation["recent_net_profit_001"], 12.0)
            self.assertEqual(allocation["final_tick_report_path"], "final.html")
            self.assertEqual(allocation["has_recent_performance"], 1)
            self.assertEqual(portfolio["actual_closed_valley_dd"], 50.0)
            self.assertEqual(portfolio["floating_dd_buffer"], 30.0)
            self.assertEqual(metrics["floating_overlap_audit"], {"pairs": 0})

    def test_delete_runs_locally_and_removes_parent_and_children(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = Path(temp_dir) / "ubs_memory_ICTRADING_STANDARD.sqlite"
            memory.touch()
            saved = save_portfolio_payload(memory, portfolio_payload("request-delete"))
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
            saved = save_portfolio_payload(memory, portfolio_payload("request-batch-exclude"))
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
            saved = save_portfolio_payload(memory, portfolio_payload("request-monthly-exclude"))
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
