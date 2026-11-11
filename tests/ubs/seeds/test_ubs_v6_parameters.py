import unittest
from unittest.mock import Mock, patch

from ubs.params_catalog import (
    UBS_PARAM_DESCRIPTIONS,
    UBS_SECTION_LABELS,
    parse_set_parameter_text,
)
from ubs.set_rules import SET_RULE_KEYS, normalize_generated_set_text
from ubs.v6_parameters import UBS_VERSION, V6_DEFAULT_SET_TEXT, V6_PARAMETER_KEYS
from ubs_agent_config import RULE_LOCKED_KEYS, is_agent_mutable_key
from ui.ubs_params_logic import UBSParamsLogicMixin


class UBSV6ParameterContractTests(unittest.TestCase):
    def test_catalog_and_default_set_have_the_same_164_keys(self) -> None:
        params = parse_set_parameter_text(V6_DEFAULT_SET_TEXT)
        parsed_keys = tuple(param["key"] for param in params)

        self.assertEqual(UBS_VERSION, "V6")
        self.assertEqual(len(V6_PARAMETER_KEYS), 164)
        self.assertEqual(len(set(V6_PARAMETER_KEYS)), 164)
        self.assertEqual(parsed_keys, V6_PARAMETER_KEYS)

    def test_every_v6_key_and_section_has_a_ui_description(self) -> None:
        params = parse_set_parameter_text(V6_DEFAULT_SET_TEXT)
        sections = {param["section"] for param in params if param["section"]}

        self.assertEqual(set(V6_PARAMETER_KEYS) - UBS_PARAM_DESCRIPTIONS.keys(), set())
        self.assertEqual(sections - UBS_SECTION_LABELS.keys(), set())

    def test_rule_locked_values_override_conflicting_v6_source_values(self) -> None:
        normalized, _found, _missing = normalize_generated_set_text(V6_DEFAULT_SET_TEXT)
        values = {
            param["key"]: param["value"]
            for param in parse_set_parameter_text(normalized)
        }

        self.assertEqual(values["AdjustLotsizeToVariableValues"], "false")
        self.assertEqual(values["OnlyUp"], "false")
        self.assertEqual(values["EnableNFP_Filter"], "true")
        self.assertTrue(set(SET_RULE_KEYS) <= RULE_LOCKED_KEYS)

    def test_rule_locked_v6_keys_ignore_mutable_overrides(self) -> None:
        with patch(
            "ubs_agent_config.load_mutation_overrides",
            return_value=({}, set(RULE_LOCKED_KEYS)),
        ):
            for key in RULE_LOCKED_KEYS:
                self.assertFalse(is_agent_mutable_key(key), key)

    def test_auto_load_uses_v6_and_ignores_global_values_for_locked_keys(self) -> None:
        logic = object.__new__(UBSParamsLogicMixin)
        logic.ubs_params_data = []
        logic.ubs_params_current_path = None
        logic.ubs_params_modified = False
        logic.ubs_params_file_label = Mock()
        logic.ubs_params_desc_var = Mock()
        logic._ubs_params_apply_filter = Mock()

        global_values = {
            "Exit_stop": "777",
            "OnlyUp": "true",
            "EnableNFP_Filter": "false",
        }
        with (
            patch("ui.ubs_params_logic.load_global_params", return_value=global_values),
            patch("ui.ubs_params_logic.GLOBAL_PARAMS_FILE") as global_file,
        ):
            global_file.exists.return_value = True
            global_file.name = "ubs_global_params.json"
            logic._ubs_params_auto_load()

        values = {param["key"]: param["value"] for param in logic.ubs_params_data}
        self.assertEqual(len(values), 164)
        self.assertEqual(values["Exit_stop"], "777")
        self.assertEqual(values["OnlyUp"], "false")
        self.assertEqual(values["EnableNFP_Filter"], "true")
        logic.ubs_params_file_label.set.assert_called_once_with(
            "V6 · Global — ubs_global_params.json"
        )


if __name__ == "__main__":
    unittest.main()
