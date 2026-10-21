"""Config loading: zero-config defaults, boolean switches and the extraction model."""

import asyncio
import contextlib
import io
import json
import os
import sys
import types
import unittest
from unittest import mock

import support

import config
import preflight
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


def fake_sdk(options_seen, version=sdk.MIN_SDK_VERSION):
    """A stand-in claude_agent_sdk module that records the options call_model builds."""
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
    pkg.__version__ = version
    pkg.query, pkg.ClaudeAgentOptions, pkg.ResultMessage = query, options, ResultMessage
    return {"claude_agent_sdk": pkg}


class CallModelTest(unittest.TestCase):
    """call_model hands the configured model to the SDK, apart from the user's setup."""

    def setUp(self):
        support.reset_db()

    def options(self, version=sdk.MIN_SDK_VERSION, **kwargs):
        seen = {}
        with mock.patch.dict(sys.modules, fake_sdk(seen, version)):
            asyncio.run(sdk.call_model("hello", **kwargs))
        return seen

    def test_default_is_the_sonnet_alias(self):
        self.assertEqual(self.options()["model"], "sonnet")

    def test_pinned_model_reaches_the_sdk(self):
        write_config({"model": "claude-sonnet-5-5"})
        self.assertEqual(self.options()["model"], "claude-sonnet-5-5")

    def test_the_call_runs_apart_from_the_users_setup(self):
        seen = self.options(allowed_tools=["Bash"])
        self.assertEqual(seen["setting_sources"], [])
        self.assertIs(seen["strict_mcp_config"], True)
        self.assertIn("no-session-persistence", seen["extra_args"])
        self.assertEqual(seen["tools"], ["Bash"])
        self.assertEqual(seen["allowed_tools"], ["Bash"])

    def test_a_call_without_tools_is_offered_none(self):
        self.assertEqual(self.options()["tools"], [])

    def test_an_outdated_sdk_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "older than " + sdk.MIN_SDK_VERSION):
            self.options(version="0.1.41")

    def test_versions_compare_by_number(self):
        self.assertFalse(sdk.sdk_outdated(sdk.MIN_SDK_VERSION))
        self.assertFalse(sdk.sdk_outdated("0.10.0"))
        self.assertFalse(sdk.sdk_outdated("1.0"))
        self.assertTrue(sdk.sdk_outdated("0.2.99"))
        self.assertTrue(sdk.sdk_outdated("unknown"))

    def test_requirements_ask_for_the_minimum_version(self):
        path = os.path.join(support.SCRIPTS_DIR, "..", "requirements.txt")
        with open(path, encoding="utf-8") as f:
            self.assertIn(f"claude-agent-sdk>={sdk.MIN_SDK_VERSION}", f.read().split())


class CheckDependenciesTest(unittest.TestCase):
    """Session start installs the SDK when it is missing and upgrades it when it is old."""

    def pip_calls(self, version):
        done = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.dict(sys.modules, fake_sdk({}, version)), \
                mock.patch("subprocess.run", return_value=done) as run, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(preflight.check_dependencies())
        return [c.args[0] for c in run.call_args_list]

    def test_a_current_sdk_is_left_alone(self):
        self.assertEqual(self.pip_calls(sdk.MIN_SDK_VERSION), [])

    def test_an_outdated_sdk_is_upgraded(self):
        calls = self.pip_calls("0.1.41")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1:4], ["-m", "pip", "install"])
        self.assertIn(f"claude-agent-sdk>={sdk.MIN_SDK_VERSION}", calls[0])


if __name__ == "__main__":
    unittest.main()
