"""Larvling configuration — reads .claude/larvling.config.json with zero-config defaults."""

import json
import os

CONFIG_PATH = os.path.join(
    os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd(),
    ".claude",
    "larvling.config.json",
)

DEFAULTS = {
    "analysis": True,
    "task_tracking": True,
    "knowledge_extraction": True,
    "context_hints": True,
    "summary_hints": True,
    "session_tags": True,
    "geolocation": False,
    "model": "sonnet",
}


def get_config():
    """Load config with defaults. Missing keys use defaults. Missing file = all defaults."""
    config = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user = json.load(f)
        if isinstance(user, dict):
            for key, default in DEFAULTS.items():
                if key not in user:
                    continue
                if isinstance(default, bool):
                    config[key] = bool(user[key])
                elif isinstance(user[key], str) and user[key].strip():
                    config[key] = user[key].strip()
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return config
