"""El lanzador del tester tiene que reapuntar todos los modulos del runner."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from manager_node_runtime.live_audit_tester import LiveAuditTesterMixin
from manager_node_runtime.manual_check import _wrapper as manual_check_wrapper

_BASE_STUB = """from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
CONFIG_DIR = BASE_DIR / "configs"
REPORT_DIR = BASE_DIR / "reports"
LOG_DIR = BASE_DIR / "logs"
"""

_EXPERTS_STUB = """from backtest.base import CONFIG_DIR, REPORT_DIR
"""

_REPORTS_STUB = """from backtest.base import REPORT_DIR
"""

_LOGGING_STUB = """from backtest.base import LOG_DIR
"""

_RUN_TESTS_STUB = """import json
import sys

from backtest import base, experts, logging, reports
from backtest.base import CONFIG_DIR, LOG_DIR, REPORT_DIR


def main():
    observed = {
        "run_tests.REPORT_DIR": str(REPORT_DIR),
        "run_tests.CONFIG_DIR": str(CONFIG_DIR),
        "run_tests.LOG_DIR": str(LOG_DIR),
        "backtest.base.REPORT_DIR": str(base.REPORT_DIR),
        "backtest.experts.REPORT_DIR": str(experts.REPORT_DIR),
        "backtest.experts.CONFIG_DIR": str(experts.CONFIG_DIR),
        "backtest.reports.REPORT_DIR": str(reports.REPORT_DIR),
        "backtest.logging.LOG_DIR": str(logging.LOG_DIR),
    }
    with open(sys.argv[1], "w", encoding="utf-8") as handle:
        json.dump(observed, handle)
    return 0
"""


def _write_runner_stubs(root: Path) -> None:
    """Deja en `root` un runner de mentira repartido como el de verdad."""
    package = root / "backtest"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "base.py").write_text(_BASE_STUB, encoding="utf-8")
    (package / "experts.py").write_text(_EXPERTS_STUB, encoding="utf-8")
    (package / "reports.py").write_text(_REPORTS_STUB, encoding="utf-8")
    (package / "logging.py").write_text(_LOGGING_STUB, encoding="utf-8")
    (root / "run_tests.py").write_text(_RUN_TESTS_STUB, encoding="utf-8")


class LiveAuditTesterWrapperTests(unittest.TestCase):
    def _observe(self, wrapper_for) -> None:
        """Corre el envoltorio sobre el runner de mentira y compara carpetas."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_runner_stubs(root)
            reports_dir, configs_dir, logs_dir = (
                root / "audit" / name for name in ("reports", "configs", "logs")
            )
            observed_path = root / "observed.json"
            wrapper = wrapper_for(reports_dir, configs_dir, logs_dir)

            completed = subprocess.run(
                [sys.executable, "-u", "-c", wrapper, str(observed_path)],
                cwd=str(root), text=True, capture_output=True, timeout=120,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            observed = json.loads(observed_path.read_text(encoding="utf-8"))

        self.assertEqual(observed, {
            "run_tests.REPORT_DIR": str(reports_dir),
            "run_tests.CONFIG_DIR": str(configs_dir),
            "run_tests.LOG_DIR": str(logs_dir),
            "backtest.base.REPORT_DIR": str(reports_dir),
            "backtest.experts.REPORT_DIR": str(reports_dir),
            "backtest.experts.CONFIG_DIR": str(configs_dir),
            "backtest.reports.REPORT_DIR": str(reports_dir),
            "backtest.logging.LOG_DIR": str(logs_dir),
        })

    def test_wrapper_repoints_every_runner_module(self) -> None:
        self._observe(LiveAuditTesterMixin._tester_wrapper)

    def test_manual_check_wrapper_repoints_every_runner_module(self) -> None:
        """La comprobacion manual usa el mismo criterio y se rompe igual."""
        self._observe(manual_check_wrapper)


if __name__ == "__main__":
    unittest.main()
