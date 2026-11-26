import json
import unittest

from helpers import EXAMPLE_CONFIG, VaultTestCase

import config


class ConfigTest(VaultTestCase):
    def example(self) -> dict:
        return json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))

    def write_config(self, data) -> None:
        text = data if isinstance(data, str) else json.dumps(data)
        self.write({".mimir.json": text})

    def assert_error_names(self, data, key: str) -> None:
        self.write_config(data)
        with self.assertRaises(config.ConfigError) as caught:
            config.load(self.vault)
        self.assertIn(key, str(caught.exception))

    def test_config_example_loads(self):
        self.write_config(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        cfg = config.load(self.vault)
        self.assertEqual(cfg.states,
                         ("planned", "active", "paused", "done", "cancelled"))
        self.assertEqual(len(cfg.areas), 2)
        self.assertEqual(set(cfg.kinds), {"project", "plan"})
        plan = cfg.kinds["plan"]
        self.assertIsInstance(plan, config.Kind)
        self.assertEqual(plan.name, "plan")
        self.assertEqual(plan.progress, "checkboxes")
        self.assertIsInstance(cfg.view_properties, frozenset)
        self.assertIsInstance(cfg.size_cap_bytes, int)

    def test_config_missing_key_names_it(self):
        data = self.example()
        del data["states"]
        self.assert_error_names(data, "states")

    def test_missing_file(self):
        with self.assertRaises(config.ConfigError) as caught:
            config.load(self.vault)
        self.assertIn(".mimir.json", str(caught.exception))

    def test_invalid_json(self):
        self.assert_error_names("{not json", ".mimir.json")

    def test_unknown_top_level_key(self):
        data = self.example()
        data["colour"] = "blue"
        self.assert_error_names(data, "colour")

    def test_wrong_types_name_the_key(self):
        data = self.example()
        data["areas"] = "Garden"
        self.assert_error_names(data, "areas")
        data = self.example()
        data["size_cap_bytes"] = True
        self.assert_error_names(data, "size_cap_bytes")
        data = self.example()
        data["states_by_type"] = {"computer": "stale"}
        self.assert_error_names(data, "states_by_type.computer")

    def test_kind_fields(self):
        data = self.example()
        data["kinds"]["season"] = {"note_type": "project", "hub": True,
                                     "has_status": True,
                                     "folder": "Seasons"}
        self.write_config(data)
        cfg = config.load(self.vault)
        season = cfg.kinds["season"]
        self.assertEqual(season.note_type, "project")
        self.assertTrue(season.hub)
        self.assertEqual(season.folder, "Seasons")
        self.assertEqual(cfg.kinds["plan"].note_type, "plan")

    def test_kind_errors_name_the_key(self):
        data = self.example()
        data["kinds"]["plan"]["progress"] = "percent"
        self.assert_error_names(data, "kinds.plan.progress")
        data = self.example()
        data["kinds"]["plan"]["colour"] = "blue"
        self.assert_error_names(data, "kinds.plan.colour")
        data = self.example()
        data["kinds"]["plan"]["name"] = "other"
        self.assert_error_names(data, "kinds.plan.name")

    def test_frozen(self):
        self.write_config(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        cfg = config.load(self.vault)
        with self.assertRaises(AttributeError):
            cfg.size_cap_bytes = 1


if __name__ == "__main__":
    unittest.main()
