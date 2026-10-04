import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ubs_agent_reports
from ubs.tester_diagnostics import journal_symbol_missing


# Transcrito del journal real de ICTRADING del 2026-10-04: el futuro DXY_U6
# vencio en septiembre y el broker lo retiro, pero seguia en assets/.
JOURNAL = (
    r"MT5 tester journal: C:\...\Tester\logs\20261004.log" "\n\n"
    "CD\t0\t16:04:52.796\tCore 01\tABBV.NYSE,M30: total time 0:00:38.889\n"
    "IQ\t2\t16:05:31.924\tTester\tcannot select symbol in market watch\n"
    "LE\t2\t16:05:31.958\tTester\tsymbol DXY_U6 not exist\n"
    "CQ\t2\t16:05:44.072\tTester\tcannot select symbol in market watch\n"
    "EE\t2\t16:05:44.102\tTester\tsymbol DXY_U6 not exist\n"
)


class JournalSymbolMissingTests(unittest.TestCase):
    def test_reads_the_symbol_mt5_could_not_select(self) -> None:
        self.assertTrue(journal_symbol_missing(JOURNAL, "DXY_U6"))
        self.assertTrue(journal_symbol_missing(JOURNAL, "dxy_u6"))

    def test_never_borrows_the_verdict_from_another_symbol(self) -> None:
        # ABBV.NYSE comparte journal y si se testeo bien: el estado que deriva
        # de esto es terminal, asi que no puede heredar evidencia ajena.
        self.assertFalse(journal_symbol_missing(JOURNAL, "ABBV.NYSE"))
        self.assertFalse(journal_symbol_missing(JOURNAL, "US500"))

    def test_an_empty_symbol_or_journal_says_nothing(self) -> None:
        self.assertFalse(journal_symbol_missing(JOURNAL, ""))
        self.assertFalse(journal_symbol_missing("", "DXY_U6"))
        self.assertFalse(journal_symbol_missing("Test passed", "DXY_U6"))


class SetSymbolMissingInTerminalTests(unittest.TestCase):
    def test_finds_the_abort_journal_left_next_to_the_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            reports = base / "reports"
            reports.mkdir()
            stem = "DXY_U6_M15_bf04fa"
            (reports / f"{stem}.tester_abort_attempt_1.mt5log.txt").write_text(
                JOURNAL, encoding="utf-8",
            )
            set_path = base / f"{stem}.set"

            with patch.object(ubs_agent_reports, "BASE_DIR", base):
                self.assertTrue(
                    ubs_agent_reports.set_symbol_missing_in_terminal(set_path, "DXY_U6")
                )
                self.assertFalse(
                    ubs_agent_reports.set_symbol_missing_in_terminal(set_path, "US500")
                )

    def test_without_any_journal_it_cannot_claim_the_symbol_is_gone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / "reports").mkdir()
            with patch.object(ubs_agent_reports, "BASE_DIR", base):
                self.assertFalse(
                    ubs_agent_reports.set_symbol_missing_in_terminal(
                        base / "DXY_U6_M15_bf04fa.set", "DXY_U6"
                    )
                )


if __name__ == "__main__":
    unittest.main()
