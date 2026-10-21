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
    # Word overlap (0-1) at which a new statement or task counts as a
    # reworded duplicate of an existing one and is skipped.
    "dedup_threshold": 0.8,
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
                value = user[key]
                if isinstance(default, bool):
                    config[key] = bool(value)
                elif isinstance(default, (int, float)):
                    # A number of the default's kind, not negative; true/false aren't numbers here.
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                        config[key] = type(default)(value)
                elif isinstance(value, str) and value.strip():
                    config[key] = value.strip()
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return config
