import unittest

from ui.universe_mt5_settings import universe_mt5_settings


class Variable:
    def __init__(self, value) -> None:
        self.value = value

    def get(self):
        return self.value


class UniverseMt5SettingsTests(unittest.TestCase):
    def test_serializes_persistent_connection_fields(self) -> None:
        owner = type("Owner", (), {})()
        owner.ubs_universe_mt5_path = Variable(" terminal64.exe ")
        owner.ubs_universe_mt5_use_account = Variable(True)
        owner.ubs_universe_mt5_login = Variable(" 123456 ")
        owner.ubs_universe_mt5_server = Variable(" Vantage-Live ")
        owner.ubs_universe_mt5_password = Variable(" secret ")

        settings = universe_mt5_settings(owner)

        self.assertEqual(settings["ubs_universe_mt5_path"], "terminal64.exe")
        self.assertEqual(settings["ubs_universe_mt5_use_account"], "1")
        self.assertEqual(settings["ubs_universe_mt5_login"], "123456")
        self.assertEqual(settings["ubs_universe_mt5_server"], "Vantage-Live")
        self.assertEqual(settings["ubs_universe_mt5_password"], " secret ")


if __name__ == "__main__":
    unittest.main()
