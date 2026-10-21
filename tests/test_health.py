"""Recording health check."""

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
EXTRACTION = "Analyze this conversation and extract structured data. " + "x" * 5000
OLD_EXTRACTION = "Analyze this conversation exchange and extract structured data. " + "x" * 5000
PROMPT = "Why does the deploy fail on staging? " + "x" * 5000


class RecordingGapTest(unittest.TestCase):
    def setUp(self):
        support.reset_db()
        with contextlib.redirect_stdout(io.StringIO()):
            preflight.ensure_schema()
        self.tdir = tempfile.mkdtemp(prefix="larvling-transcripts-")
        self.addCleanup(shutil.rmtree, self.tdir, ignore_errors=True)
        self.age = 0
        self.write(CURRENT, PROMPT)

    def write(self, stem, prompt, entrypoint="cli", recorded=False):
        path = os.path.join(self.tdir, stem + ".jsonl")
        turn = {"type": "user", "message": {"role": "user", "content": prompt}}
        if entrypoint:
            turn["entrypoint"] = entrypoint
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "queue-operation", "operation": "enqueue"}) + "\n")
            f.write(json.dumps(turn) + "\n")
        self.age += 60
        t = time.time() - 3600 + self.age
        os.utime(path, (t, t))
        if recorded:
            with db.open_db() as conn:
                db.ensure_session(conn, stem)
                conn.commit()
        return path

    def gap(self):
        payload = {"session_id": CURRENT, "transcript_path": os.path.join(self.tdir, CURRENT + ".jsonl")}
        with db.open_db() as conn:
            return health.recording_gap(conn, payload)

    def test_extraction_transcripts_after_a_recorded_session_are_no_gap(self):
        self.write("s1", PROMPT, recorded=True)
        self.write("extract-1", EXTRACTION, "sdk-py")
        self.write("extract-2", [{"type": "text", "text": EXTRACTION}], "sdk-py")
        self.assertEqual(self.gap(), 0)

    def test_old_extraction_transcripts_without_an_entrypoint_are_no_gap(self):
        self.write("s1", PROMPT, recorded=True)
        self.write("extract-1", OLD_EXTRACTION, None)
        self.write("extract-2", OLD_EXTRACTION, None)
        self.assertEqual(self.gap(), 0)

    def test_unrecorded_sessions_are_still_reported(self):
        self.write("s1", PROMPT, recorded=True)
        self.write("lost-1", PROMPT)
        self.write("lost-2", PROMPT, "claude-vscode")
        self.assertEqual(self.gap(), 2)

    def test_sessions_started_through_an_sdk_still_count(self):
        self.write("s1", PROMPT, recorded=True)
        self.write("lost-1", PROMPT, "sdk-ts")
        self.write("lost-2", PROMPT, "sdk-cli")
        self.assertEqual(self.gap(), 2)

    def test_extraction_transcripts_between_unrecorded_sessions_are_skipped(self):
        self.write("s1", PROMPT, recorded=True)
        self.write("lost-1", PROMPT)
        self.write("extract-1", EXTRACTION, "sdk-py")
        self.write("lost-2", PROMPT)
        self.write("extract-2", EXTRACTION, "sdk-py")
        self.assertEqual(self.gap(), 2)

    def test_an_unreadable_transcript_is_skipped(self):
        self.write("s1", PROMPT, recorded=True)
        self.write("lost-1", PROMPT)
        self.write("lost-2", PROMPT)
        os.mkdir(os.path.join(self.tdir, "folder.jsonl"))
        self.assertEqual(self.gap(), 2)
        self.assertTrue(health._not_a_session(os.path.join(self.tdir, "gone.jsonl")))


if __name__ == "__main__":
    unittest.main()
