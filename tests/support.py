"""Shared test setup: isolate Larvling in a throwaway project directory.

Import this before any Larvling module. db.py resolves DB_PATH from
CLAUDE_PROJECT_DIR at import time, so the environment must be set first.
"""

import atexit
import os
import shutil
import sys
import tempfile

PROJECT_DIR = tempfile.mkdtemp(prefix="larvling-test-")
CLAUDE_DIR = os.path.join(PROJECT_DIR, ".claude")
os.makedirs(CLAUDE_DIR)
os.environ["CLAUDE_PROJECT_DIR"] = PROJECT_DIR
atexit.register(shutil.rmtree, PROJECT_DIR, ignore_errors=True)

SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "larvling", "scripts")
)
sys.path.insert(0, SCRIPTS_DIR)


def reset_db():
    """Delete the test database, its WAL files and any backups."""
    for name in os.listdir(CLAUDE_DIR):
        if name.startswith("larvling"):
            os.remove(os.path.join(CLAUDE_DIR, name))
