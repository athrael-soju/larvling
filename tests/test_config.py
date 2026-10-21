"""Config loading: zero-config defaults, boolean switches and the extraction model."""

import json
import unittest

import support

import config


def write_config(data):
    with open(config.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f)


class ConfigTest(unittest.TestCase):
    def setUp(self):
        support.reset_db()  # also removes .claude/larvling.config.json

    def test_defaults_without_a_config_file(self):
        cfg = config.get_config()
        self.assertEqual(cfg, config.DEFAULTS)
        self.assertEqual(cfg["model"], "sonnet")  # alias: tracks the newest Sonnet

    def test_model_can_be_pinned(self):
        write_config({"model": " claude-sonnet-5-5 "})
        self.assertEqual(config.get_config()["model"], "claude-sonnet-5-5")

    def test_unusable_model_values_keep_the_default(self):
        for value in ("", "   ", False, 5, None):
            with self.subTest(value=value):
                write_config({"model": value})
                self.assertEqual(config.get_config()["model"], "sonnet")

    def test_switches_are_still_coerced_to_booleans(self):
        write_config({"analysis": 0, "geolocation": 1})
        cfg = config.get_config()
        self.assertIs(cfg["analysis"], False)
        self.assertIs(cfg["geolocation"], True)


if __name__ == "__main__":
    unittest.main()
