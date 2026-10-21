"""Larvling configuration — reads .claude/larvling.config.json with zero-config defaults."""

import json
import math
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
    # Task decay at session start, in days; 0 turns a rule off. An open 'now'
    # task untouched this long moves to 'soon'; a 'soon' one is listed as a
    # drop candidate; a task due this soon moves to 'now'.
    "decay_now_days": 7,
    "decay_soon_days": 30,
    "due_soon_days": 7,
}

# Largest value a numeric setting takes; every one is at least 0.
MAXIMUMS = {"dedup_threshold": 1}


def _number(value, default, maximum=None):
    """*value* as a number of *default*'s kind, or None if it isn't one: not a
    finite number from 0 to *maximum* (true/false aren't numbers here), or a
    fraction where a whole number is expected."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0 or (maximum is not None and value > maximum):
        return None
    if isinstance(default, int) and value != int(value):
        return None
    return type(default)(value)


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
                    number = _number(value, default, MAXIMUMS.get(key))
                    if number is not None:
                        config[key] = number
                elif isinstance(value, str) and value.strip():
                    config[key] = value.strip()
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return config
