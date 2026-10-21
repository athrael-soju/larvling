"""Recording health check: which transcripts count as sessions Larvling
missed.
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import time
import unittest

import support

import db
import health
import preflight

CURRENT = "current-session"


def entries(entrypoint):
    """A transcript's opening entries, padded past the size the check needs."""
    first = {"type": "queue-operation", "operation": "enqueue"}
    turn = {"type": "user", "message": {"role": "user", "content": "x" * 5000}}
    if entrypoint:
        turn["entrypoint"] = entrypoint
    return [first, turn]


class RecordingGapTest(unittest.TestCase):
    def setUp(self):
        support.reset_db()
        with contextlib.redirect_stdout(io.StringIO()):
            preflight.ensure_schema()
        self.tdir = tempfile.mkdtemp(prefix="larvling-transcripts-")
        self.addCleanup(shutil.rmtree, self.tdir, ignore_errors=True)
        self.age = 0
        self.write(CURRENT, "cli")

    def write(self, stem, entrypoint, recorded=False):
        """Add a transcript newer than the ones before it."""
        path = os.path.join(self.tdir, stem + ".jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for e in entries(entrypoint):
                f.write(json.dumps(e) + "\n")
        self.age += 60
        t = time.time() - 3600 + self.age
        os.utime(path, (t, t))
        if recorded:
            with db.open_db() as conn:
                db.ensure_session(conn, stem)
                conn.commit()

    def gap(self):
        payload = {"session_id": CURRENT, "transcript_path": os.path.join(self.tdir, CURRENT + ".jsonl")}
        with db.open_db() as conn:
            return health.recording_gap(conn, payload)

    def test_extraction_transcripts_after_a_recorded_session_are_no_gap(self):
        self.write("s1", "cli", recorded=True)
        self.write("extract-1", "sdk-py")
        self.write("extract-2", "sdk-py")
        self.assertEqual(self.gap(), 0)

    def test_unrecorded_sessions_are_still_reported(self):
        self.write("s1", "cli", recorded=True)
        self.write("lost-1", "cli")
        self.write("lost-2", "claude-vscode")
        self.assertEqual(self.gap(), 2)

    def test_extraction_transcripts_between_unrecorded_sessions_are_skipped(self):
        self.write("s1", "cli", recorded=True)
        self.write("lost-1", "cli")
        self.write("extract-1", "sdk-py")
        self.write("lost-2", "cli")
        self.write("extract-2", "sdk-ts")
        self.assertEqual(self.gap(), 2)

    def test_a_transcript_without_an_entrypoint_counts_as_a_session(self):
        self.write("s1", "cli", recorded=True)
        self.write("lost-1", None)
        self.write("lost-2", None)
        self.assertEqual(self.gap(), 2)


if __name__ == "__main__":
    unittest.main()
