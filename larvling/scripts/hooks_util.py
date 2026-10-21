"""Hook infrastructure utilities — payload reading and detached process spawning."""

import json
import os
import sys

from db import log, reconfigure_stdout


def read_hook_payload():
    """Read and parse a JSON hook payload from stdin.

    Handles LARVLING_INTERNAL guard, stdout reconfiguration, and
    standard error logging. Returns parsed dict or None on failure.
    """
    if os.environ.get("LARVLING_INTERNAL"):
        sys.exit(0)
    reconfigure_stdout()
    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
    except Exception as e:
        log("stdin_error", error=str(e))
        sys.exit(0)
    if not raw.strip():
        sys.exit(0)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        log("payload_error", size=len(raw), error=str(e))
        sys.exit(0)


def spawn_detached(script_path, payload_path):
    """Spawn a script as a detached process that outlives the parent.

    Used by hooks that need to run slow work (SDK calls) without adding
    latency to the parent hook.  The child reads its payload from the
    temp file at *payload_path*.
    """
    import subprocess

    creation = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.Popen(
        [sys.executable, script_path, "--detached", payload_path],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation,
        start_new_session=(os.name != "nt"),
    )


def spawn_with_payload(script_path, raw):
    """Run *script_path* detached with the JSON text *raw* as its payload.

    The payload goes through a temp file, which the child deletes.
    """
    import tempfile

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    )
    tmp.write(raw)
    tmp.close()
    try:
        spawn_detached(script_path, tmp.name)
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def run_detached_or_inline(script_path, callback):
    """Handle the detached-process pattern used by slow hooks.

    *script_path* is the caller's ``__file__`` — the script to re-invoke
    as a detached child.

    In normal mode (stdin): reads payload, writes it to a temp file,
    spawns a detached child with ``--detached <path>``, and exits.

    In ``--detached`` mode: reads payload from the temp file, cleans it
    up, parses JSON, and calls *callback(data)*.

    Handles LARVLING_INTERNAL guard, stdout reconfiguration, and errors.
    """
    if os.environ.get("LARVLING_INTERNAL"):
        return
    reconfigure_stdout()

    if "--detached" in sys.argv:
        idx = sys.argv.index("--detached")
        if idx + 1 >= len(sys.argv):
            log("payload_error", error="--detached missing path argument")
            return
        payload_path = sys.argv[idx + 1]
        try:
            with open(payload_path, "r", encoding="utf-8") as f:
                raw = f.read()
        except Exception as e:
            log("payload_error", error=f"failed to read detached payload: {e}")
            try:
                os.unlink(payload_path)
            except OSError:
                pass
            return
        try:
            os.unlink(payload_path)
        except OSError:
            pass
    else:
        try:
            raw = sys.stdin.buffer.read().decode("utf-8")
        except Exception as e:
            log("stdin_error", error=str(e))
            return

        if not raw.strip():
            return

        spawn_with_payload(script_path, raw)
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    callback(data)
