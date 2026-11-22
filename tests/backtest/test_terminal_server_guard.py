"""La guarda que aparta terminales conectadas a otro servidor del broker.

El 2026-11-15 el pool de AXI corrio partido: seis terminales en
`Axi-US51-Live`, que escribe `EURUSD.sa`, y cuatro en `Axi-US50-Live`, que
escribe `EURUSD`. Las cuatro aceptaron su parte de la cola y fallaron todos
los jobs con `symbol EURUSD.sa not exist`, sin un solo reporte en dos horas.
"""
import tempfile
import unittest
from pathlib import Path

from backtest.base import TerminalProfile
from backtest.terminal_identity import servers_seen_in_log, terminal_last_session
from backtest.terminals import check_terminal_servers


def write_journal(data_dir: Path, name: str, lines: str) -> None:
    logs = data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    # MT5 escribe sus journals en UTF-16LE.
    (logs / name).write_text(lines, encoding="utf-16-le")


def profile(name: str, data_dir: Path, expected_server: str = "") -> TerminalProfile:
    return TerminalProfile(
        name=name,
        mt5_path=data_dir / "terminal64.exe",
        data_dir=data_dir,
        experts_root=data_dir / "MQL5" / "Experts",
        ubs_ex5_file=None,
        portable=False,
        expected_server=expected_server,
    )


AUTHORIZED = (
    "DH\t0\t19:51:07.237\tNetwork\t'{login}': authorized on {server} "
    "through Access Server 4 NY (ping: 115.94 ms, build 5430)\n"
)


class TerminalLastSessionTests(unittest.TestCase):
    def test_reads_the_last_account_and_server_from_the_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            write_journal(
                data_dir,
                "20261115.log",
                AUTHORIZED.format(login="60082017", server="Axi-US50-Live")
                + AUTHORIZED.format(login="60268704", server="Axi-US51-Live"),
            )
            session = terminal_last_session(data_dir)
            self.assertIsNotNone(session)
            self.assertEqual(session.login, "60268704")
            self.assertEqual(session.server, "Axi-US51-Live")

    def test_prefers_the_newest_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            write_journal(
                data_dir, "20261114.log",
                AUTHORIZED.format(login="60082017", server="Axi-US50-Live"),
            )
            write_journal(
                data_dir, "20261115.log",
                AUTHORIZED.format(login="60268704", server="Axi-US51-Live"),
            )
            self.assertEqual(terminal_last_session(data_dir).server, "Axi-US51-Live")

    def test_reads_a_server_whose_name_carries_a_space(self) -> None:
        # Vantage numera con un espacio. Cortar en el primer blanco devolvia
        # `VantageMarkets-Live` para todos y la guarda vaciaba el pool.
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            write_journal(
                data_dir,
                "20261122.log",
                AUTHORIZED.format(login="34933859", server="VantageMarkets-Live 10"),
            )
            self.assertEqual(
                terminal_last_session(data_dir).server, "VantageMarkets-Live 10"
            )

    def test_the_metaeditor_log_is_not_a_journal(self) -> None:
        # `logs/` guarda tambien `metaeditor.log`, que por empezar por letra
        # encabezaba el orden por nombre y tapaba el diario del terminal.
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            write_journal(data_dir, "metaeditor.log", "compilando\n")
            write_journal(
                data_dir, "20261122.log",
                AUTHORIZED.format(login="34933859", server="VantageMarkets-Live 10"),
            )
            self.assertEqual(
                servers_seen_in_log(data_dir), ("VantageMarkets-Live 10",)
            )

    def test_without_journals_there_is_no_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.assertIsNone(terminal_last_session(Path(temporary)))
        self.assertIsNone(terminal_last_session(None))


class CheckTerminalServersTests(unittest.TestCase):
    def test_keeps_the_terminal_on_the_expected_server(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            write_journal(
                data_dir, "20261115.log",
                AUTHORIZED.format(login="60268704", server="Axi-US51-Live"),
            )
            check = check_terminal_servers([profile("AXI_1", data_dir)], "Axi-US51-Live")[0]
            self.assertTrue(check.matches)

    def test_drops_the_terminal_connected_to_another_server(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            write_journal(
                data_dir, "20261115.log",
                AUTHORIZED.format(login="60082017", server="Axi-US50-Live"),
            )
            check = check_terminal_servers([profile("AXI_5", data_dir)], "Axi-US51-Live")[0]
            self.assertFalse(check.matches)
            self.assertIn("Axi-US50-Live", check.describe())
            self.assertIn("Axi-US51-Live", check.describe())

    def test_the_server_of_the_section_wins_over_the_pool_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            write_journal(
                data_dir, "20261115.log",
                AUTHORIZED.format(login="60082017", server="Axi-US50-Live"),
            )
            check = check_terminal_servers(
                [profile("AXI_5", data_dir, expected_server="Axi-US50-Live")],
                "Axi-US51-Live",
            )[0]
            self.assertTrue(check.matches)

    def test_the_name_of_the_server_is_compared_without_case(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            write_journal(
                data_dir, "20261115.log",
                AUTHORIZED.format(login="60268704", server="axi-us51-live"),
            )
            self.assertTrue(
                check_terminal_servers([profile("AXI_1", data_dir)], "Axi-US51-Live")[0].matches
            )

    def test_a_numbered_server_is_told_apart_from_its_siblings(self) -> None:
        # Las dos cuentas del mismo broker viven en `VantageMarkets-Live 10` y
        # `VantageMarkets-Live 14`: si el nombre se corta en el espacio, ambas
        # se leen igual y la guarda deja de distinguir nada.
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            write_journal(
                data_dir, "20261122.log",
                AUTHORIZED.format(login="34933859", server="VantageMarkets-Live 10"),
            )
            other = Path(temporary) / "otra"
            write_journal(
                other, "20261122.log",
                AUTHORIZED.format(login="33881723", server="VantageMarkets-Live 14"),
            )
            checks = check_terminal_servers(
                [profile("VANTAGE_1", data_dir), profile("VANTAGE_2", other)],
                "VantageMarkets-Live 10",
            )
            self.assertTrue(checks[0].matches)
            self.assertFalse(checks[1].matches)
            self.assertIn("VantageMarkets-Live 14", checks[1].describe())

    def test_without_expectation_or_journal_nobody_is_dropped(self) -> None:
        # Un broker sin medidas del servidor, o un terminal recien instalado,
        # no pueden quedarse fuera del pool: la guarda aparta lo que consta
        # mal, no lo que no consta.
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            self.assertTrue(
                check_terminal_servers([profile("nuevo", data_dir)], "Axi-US51-Live")[0].matches
            )
            write_journal(
                data_dir, "20261115.log",
                AUTHORIZED.format(login="60082017", server="Axi-US50-Live"),
            )
            self.assertTrue(check_terminal_servers([profile("sin_esperado", data_dir)], "")[0].matches)


if __name__ == "__main__":
    unittest.main()
