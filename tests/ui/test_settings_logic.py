import configparser
import tempfile
import unittest
from pathlib import Path

from ui.settings_logic import SettingsLogicMixin


class Var:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class DummySettings(SettingsLogicMixin):
    def __init__(self, template_path: Path) -> None:
        self.template_path = Var(str(template_path))
        self.tester_vars = {
            "Symbol": Var(""),
            "Period": Var(""),
            "Model": Var(""),
            "FromDate": Var(""),
            "ToDate": Var(""),
            "Deposit": Var(""),
            "Currency": Var(""),
            "Leverage": Var(""),
            "Optimization": Var(""),
            "Visual": Var(""),
            "ReplaceReport": Var(""),
            "ShutdownTerminal": Var(""),
        }
        self.status_text = Var("")

    def _write_ui_settings(self) -> None:
        return None


class SettingsTemplateTests(unittest.TestCase):
    def test_save_template_preserves_existing_values_when_ui_fields_are_blank(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            template = Path(temp_dir) / "tester_template.ini"
            template.write_text(
                "\n".join(
                    [
                        "[Tester]",
                        "Symbol=BTCUSD",
                        "Period=H1",
                        "Model=1",
                        "FromDate=2020.01.01",
                        "ToDate=2026.05.30",
                        "Deposit=2000",
                        "Currency=USD",
                        "Leverage=1:200",
                        "Optimization=0",
                        "Visual=0",
                        "ReplaceReport=1",
                        "ShutdownTerminal=1",
                        "Report=",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            DummySettings(template)._save_template()

            parser = configparser.ConfigParser(interpolation=None)
            parser.optionxform = str
            parser.read(template, encoding="utf-8")
            tester = parser["Tester"]
            self.assertEqual(tester["Symbol"], "BTCUSD")
            self.assertEqual(tester["Period"], "H1")
            self.assertEqual(tester["Deposit"], "2000")
            self.assertEqual(tester["ReplaceReport"], "1")
            self.assertEqual(tester["ShutdownTerminal"], "1")

    def test_save_template_fills_defaults_when_ui_and_file_are_blank(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            template = Path(temp_dir) / "tester_template.ini"
            dummy = DummySettings(template)

            dummy._save_template()

            parser = configparser.ConfigParser(interpolation=None)
            parser.optionxform = str
            parser.read(template, encoding="utf-8")
            tester = parser["Tester"]
            self.assertEqual(tester["Symbol"], "XAUUSD")
            self.assertEqual(tester["Period"], "M30")
            self.assertEqual(tester["Model"], "1")
            self.assertEqual(tester["ReplaceReport"], "1")
            self.assertEqual(tester["ShutdownTerminal"], "1")


if __name__ == "__main__":
    unittest.main()
