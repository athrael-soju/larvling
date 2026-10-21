"""Config loading: zero-config defaults, boolean switches and the extraction model."""

import asyncio
import json
import sys
import types
import unittest
from unittest import mock

import support

import config
import sdk


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

    def test_numbers_must_be_non_negative_numbers(self):
        write_config({"dedup_threshold": 0.6})
        self.assertEqual(config.get_config()["dedup_threshold"], 0.6)
        for value in (-1, "0.5", True, None, [0.5]):
            with self.subTest(value=value):
                write_config({"dedup_threshold": value})
                self.assertEqual(config.get_config()["dedup_threshold"], 0.8)

    def test_switches_are_still_coerced_to_booleans(self):
        write_config({"analysis": 0, "geolocation": 1})
        cfg = config.get_config()
        self.assertIs(cfg["analysis"], False)
        self.assertIs(cfg["geolocation"], True)


def fake_sdk(options_seen):
    """Stand-in claude_agent_sdk modules that record the options call_model builds."""
    class ResultMessage:
        subtype = "success"
        structured_output = None
        usage = None

    async def query(prompt, options):
        yield ResultMessage()

    def options(**kwargs):
        options_seen.update(kwargs)
        return kwargs

    pkg = types.ModuleType("claude_agent_sdk")
    pkg.__version__ = "0.1.0"
    pkg.query, pkg.ClaudeAgentOptions, pkg.ResultMessage = query, options, ResultMessage
    internal = types.ModuleType("claude_agent_sdk._internal")
    parser = types.ModuleType("claude_agent_sdk._internal.message_parser")
    parser.parse_message = lambda data: data
    client = types.ModuleType("claude_agent_sdk._internal.client")
    client.parse_message = parser.parse_message
    errors = types.ModuleType("claude_agent_sdk._errors")
    errors.MessageParseError = ValueError
    pkg._internal, pkg._errors = internal, errors
    internal.client, internal.message_parser = client, parser
    return {
        "claude_agent_sdk": pkg,
        "claude_agent_sdk._internal": internal,
        "claude_agent_sdk._internal.message_parser": parser,
        "claude_agent_sdk._internal.client": client,
        "claude_agent_sdk._errors": errors,
    }


class CallModelTest(unittest.TestCase):
    """call_model hands the configured model to the SDK."""

    def setUp(self):
        support.reset_db()

    def model_used(self):
        seen = {}
        with mock.patch.dict(sys.modules, fake_sdk(seen)):
            asyncio.run(sdk.call_model("hello"))
        return seen["model"]

    def test_default_is_the_sonnet_alias(self):
        self.assertEqual(self.model_used(), "sonnet")

    def test_pinned_model_reaches_the_sdk(self):
        write_config({"model": "claude-sonnet-5-5"})
        self.assertEqual(self.model_used(), "claude-sonnet-5-5")


if __name__ == "__main__":
    unittest.main()
