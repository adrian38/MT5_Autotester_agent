"""Perfiles de terminal MT4: lectura, filtro por broker, validacion y guardado."""
import configparser
import tempfile
import unittest
from pathlib import Path

from ui.mt4_terminal_logic import MT4TerminalLogicMixin
from ui.settings_logic import SettingsLogicMixin


TRUE_VALUES = {"1", "true", "yes", "on", "si"}


class Var:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class DummyMT4(MT4TerminalLogicMixin):
    def __init__(self, broker: str = "AXI", profiles=None) -> None:
        self.ubs_broker = Var(broker)
        self.mt4_terminal_profiles = profiles if profiles is not None else []
        self.mt4_selected_index = None

    def _bool_setting(self, value: object, default: bool = False) -> bool:
        if value is None:
            return default
        return str(value).strip().lower() in TRUE_VALUES


class DummyWriter(SettingsLogicMixin):
    def __init__(self, profiles) -> None:
        self.mt4_terminal_profiles = profiles


def parser_with(text: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read_string(text)
    return parser


class MT4TerminalProfileTests(unittest.TestCase):
    def test_profiles_are_read_in_section_order_without_expert_fields(self) -> None:
        parser = parser_with(
            "\n".join(
                [
                    "[MT4Terminal.2]",
                    "enabled=0",
                    "broker=AXI",
                    "name=AXI demo",
                    "mt4_path=C:\\MT4\\terminal.exe",
                    "data_dir=C:\\Datos\\MT4",
                    "[MT4Terminal.1]",
                    "enabled=1",
                    "broker=axi",
                    "name=AXI live",
                    "mt4_path=C:\\MT4live\\terminal.exe",
                ]
            )
        )

        profiles = DummyMT4()._read_mt4_terminal_profiles(parser)

        self.assertEqual([profile["name"] for profile in profiles], ["AXI live", "AXI demo"])
        self.assertEqual(profiles[0]["broker"], "AXI")
        self.assertTrue(profiles[0]["enabled"])
        self.assertFalse(profiles[1]["enabled"])
        self.assertNotIn("experts_root", profiles[0])
        self.assertNotIn("ubs_ex5_file", profiles[0])

    def test_only_the_active_broker_profiles_are_listed(self) -> None:
        app = DummyMT4(
            broker="AXI",
            profiles=[
                {"name": "Robo", "broker": "ROBOFOREX", "enabled": True},
                {"name": "AXI on", "broker": "AXI", "enabled": True},
                {"name": "AXI off", "broker": "AXI", "enabled": False},
            ],
        )

        names = [profile["name"] for profile in app._broker_mt4_terminal_profiles()]
        active = [
            profile["name"]
            for profile in app._broker_mt4_terminal_profiles(include_disabled=False)
        ]

        self.assertEqual(names, ["AXI on", "AXI off"])
        self.assertEqual(active, ["AXI on"])

    def test_validation_only_complains_about_missing_terminal_executables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            terminal = Path(temp_dir) / "terminal.exe"
            terminal.write_text("", encoding="utf-8")
            app = DummyMT4(
                broker="AXI",
                profiles=[
                    {
                        "name": "AXI ok",
                        "broker": "AXI",
                        "enabled": True,
                        "mt4_path": str(terminal),
                        "data_dir": temp_dir,
                    },
                    {
                        "name": "AXI roto",
                        "broker": "AXI",
                        "enabled": True,
                        "mt4_path": str(Path(temp_dir) / "no_existe.exe"),
                        "data_dir": "",
                    },
                    {
                        "name": "AXI apagada",
                        "broker": "AXI",
                        "enabled": False,
                        "mt4_path": "",
                        "data_dir": "",
                    },
                ],
            )

            errors = app._validate_mt4_terminal_errors()

            self.assertEqual(len(errors), 1)
            self.assertIn("AXI roto", errors[0])

    def test_an_empty_broker_is_reported_as_an_error(self) -> None:
        errors = DummyMT4(broker="AXI")._validate_mt4_terminal_errors()

        self.assertEqual(len(errors), 1)
        self.assertIn("AXI", errors[0])


class MT4TerminalSettingsTests(unittest.TestCase):
    def test_each_profile_is_written_as_its_own_section(self) -> None:
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        writer = DummyWriter([
            {
                "enabled": True,
                "broker": "axi",
                "name": "AXI live",
                "mt4_path": "C:\\MT4\\terminal.exe",
                "data_dir": "C:\\Datos\\MT4",
                "portable": False,
            },
        ])

        writer._write_mt4_terminal_settings(parser)

        section = parser["MT4Terminal.1"]
        self.assertEqual(section["enabled"], "1")
        self.assertEqual(section["broker"], "AXI")
        self.assertEqual(section["name"], "AXI live")
        self.assertEqual(section["mt4_path"], "C:\\MT4\\terminal.exe")
        self.assertEqual(section["data_dir"], "C:\\Datos\\MT4")
        self.assertEqual(section["portable"], "0")
        self.assertNotIn("ubs_ex5_file", section)


if __name__ == "__main__":
    unittest.main()
