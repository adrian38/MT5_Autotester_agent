import unittest

from ui.app_chrome import AppChromeMixin


class Var:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class DummyChrome(AppChromeMixin):
    """Lo justo de la ventana para ejecutar el cambio de tema."""

    def __init__(self) -> None:
        self.process = None
        self.current_section = "configuracion"
        self.theme_mode = Var("dark")
        self.status_text = Var("")
        self.nav_buttons: dict[str, object] = {}
        self.section_frames: dict[str, object] = {}
        self.colors: dict[str, str] = {}
        self.calls: list[str] = []
        self.template_loads = 0

    def _apply_theme_palette(self) -> None:
        self.calls.append("_apply_theme_palette")

    def configure(self, **_kwargs) -> None:
        return None

    def winfo_children(self) -> list:
        return []

    def _configure_style(self) -> None:
        self.calls.append("_configure_style")

    def _build_ui(self) -> None:
        self.calls.append("_build_ui")
        # Reconstruir la interfaz deja los campos del Tester recien creados.
        self.template_loads = 0

    def _load_template(self) -> None:
        self.calls.append("_load_template")
        self.template_loads += 1

    def _refresh_all(self) -> None:
        self.calls.append("_refresh_all")

    def _show_section(self, key: str) -> None:
        self.calls.append(f"_show_section:{key}")

    def _write_ui_settings(self) -> None:
        self.calls.append("_write_ui_settings")


class ThemeToggleTests(unittest.TestCase):
    def test_toggle_reloads_the_tester_template_after_rebuilding(self):
        chrome = DummyChrome()

        chrome._toggle_theme()

        self.assertEqual(chrome.theme_mode.get(), "light")
        self.assertEqual(chrome.template_loads, 1)
        self.assertLess(
            chrome.calls.index("_build_ui"),
            chrome.calls.index("_load_template"),
            "el template se recarga despues de reconstruir, no antes",
        )

    def test_a_template_that_cannot_be_read_does_not_abort_the_toggle(self):
        chrome = DummyChrome()

        def boom() -> None:
            raise FileNotFoundError("no existe el template")

        chrome._load_template = boom

        chrome._toggle_theme()

        self.assertEqual(chrome.theme_mode.get(), "light")
        self.assertIn("_show_section:configuracion", chrome.calls)


if __name__ == "__main__":
    unittest.main()
