"""Test setup: points Larvling at a temporary project directory.

Import this before any Larvling module; db.py reads CLAUDE_PROJECT_DIR on import.
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
    """Delete the files Larvling wrote to the test .claude directory."""
    for name in os.listdir(CLAUDE_DIR):
        if name.startswith("larvling"):
            os.remove(os.path.join(CLAUDE_DIR, name))
