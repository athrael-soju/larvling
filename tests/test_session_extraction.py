"""Session extraction (#72): one extraction per session at SessionEnd and
PreCompact, catch-up at SessionStart, and the schema v15 watermark.

LiveCorrectionTest calls the model, so it only runs with LARVLING_LIVE_TESTS=1.
"""

import contextlib
import io
import json
import os
import re
import sys
import unittest
from unittest import mock

import support

import analyze
import db
import preflight
from test_provenance import make_v13_db, run_preflight

sys.path.insert(0, os.path.join(support.SCRIPTS_DIR, "hooks"))
import prompt  # noqa: E402
import session_start  # noqa: E402
import stop  # noqa: E402

SID = "sess-1"
OTHER = "sess-2"

EXCHANGES = [
    ("I'm a data engineer at a bank and we run Airflow 2.9.", "Airflow 2.9 added dataset scheduling."),
    ("What does the scheduler do?", "The scheduler triggers task instances whose dependencies are met."),
    ("Thanks. I'll upgrade our cluster next sprint.", "Sounds good."),
]


def fact(claim, evidence, source="user", about="user", **extra):
    return {
        "topic_title": "Work",
        "claim": claim,
        "domain": "professional",
        "tags": "work",
        "source": source,
        "kind": "fact",
        "about": about,
        "evidence": evidence,
        "action": "add_topic",
        **extra,
    }


class FakeModel:
    """Stands in for sdk.call_model: records each prompt and returns *results* in turn."""

    def __init__(self, *results, error=None, during=None):
        self.results = list(results)
        self.error = error
        self.during = during
        self.prompts = []

    async def __call__(self, prompt, **kwargs):
        self.prompts.append(prompt)
        if self.during:
            self.during()
        if self.error:
            raise self.error
        result = self.results.pop(0) if self.results else {}
        return {"knowledge": [], "session_tags": [], "tasks": [], **result}, None


def fresh_db():
    support.reset_db()
    with contextlib.redirect_stdout(io.StringIO()):
        preflight.ensure_schema()


def record(session_id, exchanges, minutes_ago=None):
    """Record user/agent messages; *minutes_ago* backdates them."""
    with db.open_db() as conn:
        db.ensure_session(conn, session_id)
        for user_text, agent_text in exchanges:
            for role, text in (("user", user_text), ("assistant", agent_text)):
                db.record_message(conn, session_id, role, text)
        if minutes_ago is not None:
            conn.execute(
                "UPDATE messages SET timestamp = datetime('now', ?) WHERE session_id = ?",
                (f"-{minutes_ago} minutes", session_id),
            )


def extract(model, data=None):
    with mock.patch.object(analyze, "call_model", model):
        analyze._run(data or {"session_id": SID, "hook_event_name": "SessionEnd"})


def session_row(session_id=SID):
    with db.open_db() as conn:
        return dict(conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone())


def last_message_id(session_id=SID):
    with db.open_db() as conn:
        return conn.execute(
            "SELECT MAX(id) FROM messages WHERE session_id = ? AND role IN ('user', 'assistant')",
            (session_id,),
        ).fetchone()[0]


def claims():
    with db.open_db() as conn:
        return [r[0] for r in conn.execute("SELECT claim FROM statements ORDER BY id")]


class SessionExtractionTest(unittest.TestCase):
    def setUp(self):
        fresh_db()
        record(SID, EXCHANGES)

    def test_one_call_covers_the_whole_session(self):
        model = FakeModel({"knowledge": [fact("Data engineer at a bank", "a data engineer at a bank")]})
        extract(model)

        self.assertEqual(len(model.prompts), 1)
        for n, (user_text, agent_text) in enumerate(EXCHANGES):
            self.assertIn(f"[{2 * n + 1}] USER: {user_text}", model.prompts[0])
            self.assertIn(f"[{2 * n + 2}] AGENT: {agent_text}", model.prompts[0])
        self.assertEqual(claims(), ["Data engineer at a bank"])
        row = session_row()
        self.assertEqual(row["extracted_through"], last_message_id())
        self.assertIsNotNone(row["extracted_at"])
        self.assertIsNone(row["extract_lease"])
        with db.open_db() as conn:
            marker = conn.execute(
                "SELECT content FROM messages WHERE role = 'system'"
            ).fetchone()[0]
        self.assertEqual(marker, "Extraction: knowledge=1 topics, 1 statements, no tasks")

    def test_evidence_may_come_from_any_message_but_not_span_two(self):
        model = FakeModel({"knowledge": [
            fact("Plans a cluster upgrade", "upgrade our cluster next sprint"),
            fact("Runs Airflow and asks about schedulers", "Airflow 2.9. What does the scheduler"),
            fact("Scheduler triggers ready tasks", "triggers task instances whose dependencies",
                 source="agent", about="domain"),
        ]})
        extract(model)
        self.assertEqual(claims(), ["Plans a cluster upgrade", "Scheduler triggers ready tasks"])
        reasons = [e["reason"] for e in support.logged("extraction_skipped")]
        self.assertEqual(reasons, ["evidence not in user text"])

    def test_nothing_new_means_no_call(self):
        extract(FakeModel())
        model = FakeModel()
        extract(model)
        self.assertEqual(model.prompts, [])

    def test_a_resumed_session_extracts_only_the_new_messages(self):
        extract(FakeModel())
        record(SID, [("We moved to Dagster last week.", "Dagster models assets directly.")])
        model = FakeModel()
        extract(model)
        self.assertEqual(len(model.prompts), 1)
        self.assertIn("[1] USER: We moved to Dagster last week.", model.prompts[0])
        self.assertNotIn("Airflow", model.prompts[0])
        self.assertEqual(session_row()["extracted_through"], last_message_id())

    def test_a_long_session_is_extracted_in_chunks_in_order(self):
        with mock.patch.object(analyze, "CHUNK_CHARS", 120):
            model = FakeModel()
            extract(model)
        self.assertGreater(len(model.prompts), 1)
        # Every exchange lands in one chunk, question and answer together.
        for user_text, agent_text in EXCHANGES:
            holding = [p for p in model.prompts if user_text in p]
            self.assertEqual(len(holding), 1)
            self.assertIn(agent_text, holding[0])
        self.assertIn("part 1 of", model.prompts[0])
        self.assertEqual(session_row()["extracted_through"], last_message_id())

    def test_a_failed_call_leaves_the_session_to_retry(self):
        extract(FakeModel(error=RuntimeError("model unavailable")))
        row = session_row()
        self.assertIsNone(row["extracted_through"])
        self.assertIsNone(row["extract_lease"])
        self.assertEqual(support.logged("extraction_error")[0]["error"], "model unavailable")

        model = FakeModel()
        extract(model)
        self.assertEqual(len(model.prompts), 1)
        self.assertEqual(session_row()["extracted_through"], last_message_id())

    def test_a_failed_chunk_keeps_the_chunks_before_it(self):
        class FailSecond(FakeModel):
            async def __call__(self, prompt, **kwargs):
                if self.prompts:
                    self.prompts.append(prompt)
                    raise RuntimeError("rate limited")
                return await super().__call__(prompt, **kwargs)

        with mock.patch.object(analyze, "CHUNK_CHARS", 120):
            extract(FailSecond())
        done = session_row()["extracted_through"]
        self.assertIsNotNone(done)
        self.assertLess(done, last_message_id())

    def test_a_session_already_being_extracted_is_left_alone(self):
        with db.open_db() as conn:
            conn.execute("UPDATE sessions SET extract_lease = datetime('now') WHERE id = ?", (SID,))
        model = FakeModel()
        extract(model)
        self.assertEqual(model.prompts, [])
        self.assertEqual(
            support.logged("extraction_skipped")[0]["reason"], "session already being extracted"
        )

    def test_an_expired_lease_is_taken_over(self):
        with db.open_db() as conn:
            conn.execute(
                "UPDATE sessions SET extract_lease = datetime('now', '-1 hour') WHERE id = ?", (SID,)
            )
        model = FakeModel()
        extract(model)
        self.assertEqual(len(model.prompts), 1)

    def test_results_are_dropped_if_the_lease_is_lost_during_the_call(self):
        def steal():
            with db.open_db() as conn:
                conn.execute("UPDATE sessions SET extract_lease = 'someone else' WHERE id = ?", (SID,))

        model = FakeModel(
            {"knowledge": [fact("Data engineer at a bank", "a data engineer at a bank")]},
            during=steal,
        )
        extract(model)
        self.assertEqual(claims(), [])
        row = session_row()
        self.assertIsNone(row["extracted_through"])
        self.assertEqual(row["extract_lease"], "someone else")

    def test_extraction_leaves_session_timing_alone(self):
        with db.open_db() as conn:
            conn.execute("UPDATE sessions SET ended_at = '2026-01-01 00:00:00' WHERE id = ?", (SID,))
        extract(FakeModel())
        self.assertEqual(session_row()["ended_at"], "2026-01-01 00:00:00")

    def test_analysis_switch_turns_extraction_off(self):
        with open(os.path.join(support.CLAUDE_DIR, "larvling.config.json"), "w") as f:
            json.dump({"analysis": False}, f)
        model = FakeModel()
        extract(model)
        self.assertEqual(model.prompts, [])


class CatchUpTest(unittest.TestCase):
    def setUp(self):
        fresh_db()
        record(SID, EXCHANGES[:1])  # the session that is starting
        record(OTHER, EXCHANGES[1:], minutes_ago=60)  # missed its SessionEnd

    def test_catch_up_extracts_quiet_earlier_sessions_only(self):
        record("sess-live", EXCHANGES[:1])  # still running in another terminal
        with db.open_db() as conn:
            self.assertEqual(analyze.pending_sessions(conn, exclude=SID), [OTHER])

        model = FakeModel()
        extract(model, {"catch_up": True, "session_id": SID})
        self.assertEqual(len(model.prompts), 1)
        self.assertIn(EXCHANGES[1][0], model.prompts[0])
        self.assertEqual(session_row(OTHER)["extracted_through"], last_message_id(OTHER))
        self.assertIsNone(session_row(SID)["extracted_through"])
        self.assertIsNone(session_row("sess-live")["extracted_through"])

    def test_session_start_spawns_a_worker_only_when_needed(self):
        with mock.patch.object(session_start, "spawn_with_payload") as spawn:
            session_start.start_catch_up(SID)
        spawn.assert_called_once()
        script, payload = spawn.call_args.args
        self.assertTrue(script.endswith(os.path.join("scripts", "analyze.py")))
        self.assertEqual(json.loads(payload), {"catch_up": True, "session_id": SID})

        extract(FakeModel(), {"catch_up": True, "session_id": SID})
        with mock.patch.object(session_start, "spawn_with_payload") as spawn:
            session_start.start_catch_up(SID)
        spawn.assert_not_called()


class MigrationTest(unittest.TestCase):
    def test_an_upgraded_db_counts_earlier_messages_as_extracted(self):
        support.reset_db()
        make_v13_db()
        self.assertEqual(run_preflight()[0], "migrated")
        with db.open_db() as conn:
            rows = conn.execute(
                "SELECT id, extracted_through, extracted_at FROM sessions ORDER BY id"
            ).fetchall()
            self.assertEqual([(r[0], r[1]) for r in rows], [("sess-a", 2), ("sess-b", 5), ("sess-c", 9)])
            self.assertTrue(all(r[2] for r in rows))
            self.assertEqual(analyze.pending_sessions(conn, idle_minutes=0), [])


class HookWiringTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(support.SCRIPTS_DIR, "..", "hooks", "hooks.json")) as f:
            self.hooks = json.load(f)["hooks"]

    def command(self, event):
        return self.hooks[event][0]["hooks"][0]["command"]

    def test_extraction_runs_at_session_end_and_compaction_not_per_reply(self):
        self.assertNotIn("analyze.py", self.command("Stop"))
        self.assertIn("analyze.py", self.command("SessionEnd"))
        self.assertIn("analyze.py", self.command("PreCompact"))


class PromptHintTest(unittest.TestCase):
    def test_no_last_learned_hint(self):
        fresh_db()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            prompt.handle({"session_id": SID, "prompt": "first"})
            with db.open_db() as conn:
                conn.execute("INSERT INTO topics (title, domain, tags) VALUES ('t', 'knowledge', 't')")
                conn.execute("INSERT INTO statements (topic_id, claim) VALUES (1, 'A new claim')")
            prompt.handle({"session_id": SID, "prompt": "second"})
        self.assertNotIn("Last learned", out.getvalue())
        self.assertNotIn("A new claim", out.getvalue())


class StopRelabelTest(unittest.TestCase):
    def transcript(self, entries):
        path = os.path.join(support.PROJECT_DIR, "relabel.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        return path

    def roles_after(self, prompt_text, first_entry):
        fresh_db()
        with contextlib.redirect_stdout(io.StringIO()):
            prompt.handle({"session_id": SID, "prompt": prompt_text})
        reply = {"type": "assistant", "message": {
            "role": "assistant", "content": [{"type": "text", "text": "Done."}]}}
        stop.handle({"session_id": SID, "transcript_path": self.transcript([first_entry, reply])})
        with db.open_db() as conn:
            return [tuple(r) for r in conn.execute(
                "SELECT role, content FROM messages ORDER BY id")]

    def test_a_prompt_claude_code_submitted_is_not_user_text(self):
        text = "Check the deploy status."
        entry = {"type": "user", "isMeta": True, "promptSource": "system",
                 "message": {"role": "user", "content": text}}
        self.assertEqual(self.roles_after(text, entry), [("system", text), ("assistant", "Done.")])

    def test_the_users_own_prompt_stays_user_text(self):
        text = "Check the deploy status."
        entry = {"type": "user", "message": {"role": "user", "content": text}}
        self.assertEqual(self.roles_after(text, entry), [("user", text), ("assistant", "Done.")])


@unittest.skipUnless(
    os.environ.get("LARVLING_LIVE_TESTS"),
    "set LARVLING_LIVE_TESTS=1 to run real extraction calls",
)
class LiveCorrectionTest(unittest.TestCase):
    def test_a_claim_corrected_later_is_stored_only_in_its_corrected_form(self):
        fresh_db()
        record(SID, [
            ("Our production database is MySQL 5.7. How do I add a column without locking the table?",
             "On MySQL 5.7 you can use ALTER TABLE ... ALGORITHM=INPLACE, LOCK=NONE for most column additions."),
            ("Hmm, what about indexes?",
             "Index creation also supports ALGORITHM=INPLACE on 5.7, without blocking writes."),
            ("Sorry, I misspoke earlier: our production database is PostgreSQL 15, not MySQL.",
             "Got it. On PostgreSQL 15, ADD COLUMN with no default is instant, and "
             "CREATE INDEX CONCURRENTLY avoids blocking writes."),
        ])
        analyze._run({"session_id": SID, "hook_event_name": "SessionEnd"})
        self.assertFalse(support.logged("extraction_error"), support.logged("extraction_error"))
        self.assertEqual(session_row()["extracted_through"], last_message_id())
        stored = claims()
        self.assertTrue([c for c in stored if re.search(r"postgres", c, re.I)], stored)
        # MySQL may only appear as what the database is not.
        for claim in stored:
            if re.search(r"mysql", claim, re.I):
                self.assertRegex(claim, re.compile(r"postgres", re.I))


if __name__ == "__main__":
    unittest.main()
