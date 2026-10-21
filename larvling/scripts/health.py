"""Recording-health tracking — makes silent recording failures loud.

Larvling records entirely through Claude Code hooks. Two ways recording can
stop without any indication:

* **Case A — a hook runs but its DB write throws** (locked DB, disk full, a
  schema mismatch). The script exits non-zero, Claude Code swallows the error,
  and nothing is recorded. `record_failure()` drops a one-shot marker so the
  next turn can warn the user; `pending_failure()`/`clear_failure()` read and
  consume it.

* **Case B — the hooks stop running entirely** (a broken plugin cache). Nothing
  inside the plugin executes, so it can't report its own absence in real time.
  `recording_gap()` catches this on the *next* working session by reconciling
  Larvling's recorded sessions against Claude Code's own per-project transcript
  files — which exist for every session regardless of Larvling's health.
"""

import json
import os
import time

from db import PROJECT_ROOT, log

MARKER_PATH = os.path.join(PROJECT_ROOT, ".claude", "larvling-health.json")

# A session with real work leaves a sizeable transcript; empty/aborted sessions
# are tiny. Filtering by size keeps those from reading as "missed" recordings.
_SUBSTANTIAL_BYTES = 4096
# How many recent transcripts to examine when looking for a gap.
_HEALTH_WINDOW = 15
# Consecutive recent substantial sessions missing from the DB before we warn.
# Two-in-a-row avoids crying wolf over a single fluke (e.g. a session that
# crashed before its Stop hook fired).
_GAP_THRESHOLD = 2


def record_failure(stage, error):
    """Persist a one-shot marker that a recording write failed.

    *stage* is a human phrase for what was being recorded (e.g. "your last
    message"). Also mirrored to the JSONL log for debugging.
    """
    err = str(error)[:300]
    try:
        os.makedirs(os.path.dirname(MARKER_PATH), exist_ok=True)
        with open(MARKER_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "stage": stage, "error": err},
                f,
            )
    except Exception:
        pass
    log("record_error", stage=stage, error=err)


def pending_failure():
    """Return the pending failure marker dict, or None if there is none."""
    try:
        with open(MARKER_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def clear_failure():
    """Remove the failure marker once it has been surfaced to the user."""
    try:
        os.unlink(MARKER_PATH)
    except OSError:
        pass


_EXTRACTION_PROMPT_START = "Analyze this conversation"


def _not_a_session(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for _, line in zip(range(50), f):
                try:
                    entry = json.loads(line)
                    if entry.get("type") != "user":
                        continue
                    content = entry["message"]["content"]
                except (ValueError, AttributeError, KeyError, TypeError):
                    continue
                if isinstance(content, list):
                    content = next((b.get("text", "") for b in content if isinstance(b, dict)), "")
                return str(content).startswith(_EXTRACTION_PROMPT_START)
    except OSError:
        return True
    return False


def recording_gap(conn, payload):
    """Count recent sessions Claude Code recorded that Larvling did not.

    Reconciles against Claude's own transcript directory (derived from the hook
    payload's ``transcript_path``), which is written for every session whether
    or not Larvling's hooks ran. Returns the number of consecutive most-recent
    substantial sessions missing from Larvling's DB, or 0 when there is no
    trustworthy signal — so it never cries wolf.
    """
    transcript_path = (payload or {}).get("transcript_path")
    session_id = (payload or {}).get("session_id")
    if not transcript_path or not session_id:
        return 0

    # Validate our filename<->session_id assumption using THIS session's own
    # transcript. Claude names a transcript "<session-id>.jsonl"; if that does
    # not hold here, the matching below would be meaningless, so stay silent.
    if os.path.splitext(os.path.basename(transcript_path))[0] != session_id:
        return 0

    tdir = os.path.dirname(transcript_path)
    try:
        names = os.listdir(tdir)
    except OSError:
        return 0

    entries = []
    for name in names:
        if not name.endswith(".jsonl"):
            continue
        stem = name[:-6]
        if stem == session_id:
            continue  # current session isn't recorded yet — expected, skip it
        full = os.path.join(tdir, name)
        try:
            st = os.stat(full)
        except OSError:
            continue
        if st.st_size < _SUBSTANTIAL_BYTES:
            continue
        entries.append((st.st_mtime, stem, full))

    if not entries:
        return 0

    entries.sort(reverse=True)  # newest first

    recorded = {r[0] for r in conn.execute("SELECT id FROM sessions").fetchall()}
    if not recorded:
        return 0  # brand-new install has recorded nothing yet — not a failure

    # Walk newest→oldest, counting the leading run of unrecorded sessions. A run
    # that starts at the most recent session means recording is broken *now*;
    # older gaps (pre-install, a one-off empty session) stop the count early.
    missing = 0
    for _, stem, full in entries:
        if stem in recorded or missing >= _HEALTH_WINDOW:
            break
        if not _not_a_session(full):
            missing += 1

    return missing if missing >= _GAP_THRESHOLD else 0
