"""Config loading: zero-config defaults, boolean switches and the extraction model."""

import asyncio
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
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


def fake_sdk(options_seen, version=preflight.MIN_SDK_VERSION):
    """A stand-in claude_agent_sdk module that records the options call_model builds."""
    class ResultMessage:
        subtype = "success"
        structured_output = {}

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

    def options(self, **kwargs):
        seen = {}
        with mock.patch.dict(sys.modules, fake_sdk(seen)):
            asyncio.run(sdk.call_model("hello", {"type": "object"}, **kwargs))
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
        self.assertEqual(seen["env"]["LARVLING_INTERNAL"], "1")
        self.assertEqual(seen["env"]["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1")

    def test_a_call_without_tools_is_offered_none(self):
        self.assertEqual(self.options()["tools"], [])


class CheckDependenciesTest(unittest.TestCase):
    """Session start installs a missing or outdated SDK in the background."""

    def setUp(self):
        tmp = tempfile.mkdtemp(prefix="larvling-sdk-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.log = os.path.join(tmp, "install.log")
        patcher = mock.patch.object(preflight, "SDK_INSTALL_LOG", self.log)
        patcher.start()
        self.addCleanup(patcher.stop)

    def check(self, version):
        """Run check_dependencies with *version* installed (None: missing)."""
        modules = fake_sdk({}, version) if version else {"claude_agent_sdk": None}
        out = io.StringIO()
        with mock.patch.dict(sys.modules, modules), \
                mock.patch("subprocess.Popen") as popen, contextlib.redirect_stdout(out):
            ready = preflight.check_dependencies()
        return ready, [c.args[0] for c in popen.call_args_list], out.getvalue()

    def write_log(self, text, age=0):
        with open(self.log, "w", encoding="utf-8") as f:
            f.write(text)
        t = time.time() - age
        os.utime(self.log, (t, t))

    def test_a_current_sdk_is_left_alone(self):
        self.write_log("done\nexit 0\n")
        self.assertEqual(self.check(preflight.MIN_SDK_VERSION), (True, [], ""))
        self.assertFalse(os.path.exists(self.log))

    def test_an_outdated_sdk_is_upgraded_in_the_background(self):
        ready, started, out = self.check("0.1.41")
        self.assertFalse(ready)
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0][-4:-1], ["pip", "install", "--quiet"])
        self.assertEqual(started[0][-1], f"claude-agent-sdk>={preflight.MIN_SDK_VERSION}")
        self.assertIn("Installing Dependency", out)
        self.assertIn("0.1.41", out)

    def test_a_missing_sdk_is_installed_in_the_background(self):
        ready, started, out = self.check(None)
        self.assertFalse(ready)
        self.assertEqual(len(started), 1)
        self.assertIn("missing", out)

    def test_an_install_in_progress_is_not_started_again(self):
        self.write_log("Collecting claude-agent-sdk\n")
        ready, started, out = self.check("0.1.41")
        self.assertEqual((ready, started), (False, []))
        self.assertIn("being installed", out)

    def test_a_failed_install_is_reported_and_tried_again(self):
        self.write_log("ERROR: externally-managed-environment\nexit 1\n")
        ready, started, out = self.check("0.1.41")
        self.assertFalse(ready)
        self.assertEqual(len(started), 1)
        self.assertIn("Missing Dependency", out)
        self.assertIn("externally-managed-environment", out)
        self.assertIn("pip install --upgrade", out)

    def test_a_stalled_install_counts_as_failed(self):
        self.write_log("Collecting claude-agent-sdk\n", age=preflight.SDK_INSTALL_STALE_SECONDS + 1)
        ready, started, out = self.check("0.1.41")
        self.assertEqual(len(started), 1)
        self.assertIn("Missing Dependency", out)

    def test_the_background_process_logs_its_exit_code(self):
        subprocess.run([sys.executable, "-c", preflight._PIP_WRAPPER, self.log,
                        sys.executable, "-c", "print('pip output')"], check=True)
        with open(self.log, encoding="utf-8") as f:
            self.assertEqual(f.read().split(), ["pip", "output", "exit", "0"])

    def test_versions_compare_by_number(self):
        self.assertFalse(preflight.sdk_outdated(preflight.MIN_SDK_VERSION))
        self.assertFalse(preflight.sdk_outdated("0.10.0"))
        self.assertFalse(preflight.sdk_outdated("1.0"))
        self.assertTrue(preflight.sdk_outdated("0.2.99"))
        self.assertTrue(preflight.sdk_outdated("unknown"))

    def test_requirements_ask_for_the_minimum_version(self):
        path = os.path.join(support.SCRIPTS_DIR, "..", "requirements.txt")
        with open(path, encoding="utf-8") as f:
            self.assertIn(f"claude-agent-sdk>={preflight.MIN_SDK_VERSION}", f.read().split())


if __name__ == "__main__":
    unittest.main()
