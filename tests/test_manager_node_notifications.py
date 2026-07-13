import configparser
import contextlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from manager_node_runtime.node import (
    JobController,
    format_stage_counts,
    stage_notification_message,
    stage_run_counts,
)


class ManagerNodeNotificationTests(unittest.TestCase):
    def test_stage_run_counts_uses_the_table_for_the_current_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "memory.sqlite"
            with contextlib.closing(sqlite3.connect(path)) as conn:
                conn.execute("create table candidates(run_id integer, status text)")
                conn.execute("create table candidate_robustness(run_id integer, status text)")
                conn.executemany(
                    "insert into candidate_robustness(run_id,status) values(?,?)",
                    [(37, "accepted"), (37, "accepted"), (37, "rejected"), (36, "rejected")],
                )
                conn.commit()

            self.assertEqual(
                stage_run_counts(path, 37, "robustness"),
                {"accepted": 2, "rejected": 1},
            )

    def test_stage_message_includes_run_cycle_attempt_and_saved_counts(self) -> None:
        message = stage_notification_message(
            {
                "node_id": "ictrading-standard-test",
                "display_name": "ICTrading Standard",
                "broker": "ICTRADING",
                "account_type": "STANDARD",
            },
            {"request": {"cycles": 5, "repair_attempts": 4}},
            {"action": "robustness", "cycle": 1, "attempt": 2},
            0,
            37,
            {"rejected": 24, "accepted": 15},
        )

        self.assertIn("Robustez OOS finalizada (OK)", message)
        self.assertIn("run #37 | ciclo 1/5 | reparacion 2/4", message)
        self.assertIn("accepted: 15 | rejected: 24", message)

    def test_send_telegram_obeys_saved_setting_and_deduplicates_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            parser = configparser.ConfigParser(interpolation=None)
            parser["General"] = {"telegram_enabled": "1", "ubs_memory_path": "memory.sqlite"}
            with (project / "ui_settings.ini").open("w", encoding="utf-8") as handle:
                parser.write(handle)
            controller = JobController(
                {
                    "node_id": "ic",
                    "display_name": "ICTrading Standard",
                    "broker": "ICTRADING",
                    "account_type": "STANDARD",
                    "project_dir": str(project),
                    "settings_file": "ui_settings.ini",
                },
                project / "manager_node.json",
            )
            controller.state = {"log_path": str(project / "job.log"), "telegram_notifications": []}

            with patch("manager_node_runtime.node.telegram_notify.send_async") as send_async:
                controller._send_telegram("cycle_1_generation", "mensaje")
                controller._send_telegram("cycle_1_generation", "mensaje repetido")

            send_async.assert_called_once()
            self.assertEqual(controller.state["telegram_notifications"], ["cycle_1_generation"])

    def test_format_stage_counts_reports_empty_state(self) -> None:
        self.assertEqual(format_stage_counts({}), "sin datos guardados")


if __name__ == "__main__":
    unittest.main()
