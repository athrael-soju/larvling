"""User-sourced facts (#71): where extracted knowledge may come from, and
keeping assessments out of context.

LiveExtractionTest calls the model, so it only runs with LARVLING_LIVE_TESTS=1.
"""

import contextlib
import io
import json
import os
import re
import sys
import time
import unittest

import support

import analyze
import db
import preflight

sys.path.insert(0, os.path.join(support.SCRIPTS_DIR, "hooks"))
import session_start  # noqa: E402

SID = "sess-1"

# The agent guesses at the user's situation; the user never says it.
SPECULATIVE_USER = "Can you help me set up CI for my project?"
SPECULATIVE_AGENT = (
    "Sure. Since you're probably working solo on a small side project, one workflow "
    "file is plenty. GitHub Actions supports reusable workflows through the "
    "workflow_call trigger."
)
# The same claim, stated by the user.
STATED_USER = "I'm working solo on a small side project. Can you help me set up CI?"
STATED_AGENT = (
    "Sure. GitHub Actions supports reusable workflows through the workflow_call trigger."
)

SOLO_CLAIM = "Works solo on a small side project"


def item(**overrides):
    base = {
        "topic_title": "Side project",
        "claim": SOLO_CLAIM,
        "domain": "professional",
        "tags": "ci, side project",
        "source": "user",
        "kind": "fact",
        "about": "user",
        "evidence": "",
        "action": "add_topic",
    }
    base.update(overrides)
    return base


def fresh_db():
    support.reset_db()
    with contextlib.redirect_stdout(io.StringIO()):
        preflight.ensure_schema()
    conn = db.get_db()
    db.ensure_session(conn, SID)
    return conn


class SourcingRulesTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()

    def tearDown(self):
        self.conn.close()

    def stored(self, items, user_text, agent_text):
        analyze.process_knowledge(self.conn, items, SID, user_text, agent_text)
        return [
            dict(r)
            for r in self.conn.execute("SELECT claim, source, kind FROM statements")
        ]

    def test_agent_speculation_about_the_user_produces_no_statement(self):
        speculation = "you're probably working solo on a small side project"
        labellings = [
            item(source="agent", kind="fact", about="user", evidence=speculation),
            item(source="user", kind="fact", about="user", evidence=speculation),
            item(source="agent", kind="assessment", about="user", evidence=speculation),
            item(source="agent", kind="assessment", about="user", evidence=speculation,
                 endorsement=speculation),  # endorsement must come from the user
        ]
        for labelling in labellings:
            with self.subTest(source=labelling["source"], kind=labelling["kind"]):
                self.assertEqual(
                    self.stored([labelling], SPECULATIVE_USER, SPECULATIVE_AGENT), []
                )

    def test_agent_recommendation_is_not_stored_as_a_user_decision(self):
        rec = item(claim="Uses a single workflow file", kind="decision",
                   evidence="one workflow file is plenty")
        self.assertEqual(self.stored([rec], SPECULATIVE_USER, SPECULATIVE_AGENT), [])

    def test_agent_domain_knowledge_is_kept(self):
        fact = item(topic_title="GitHub Actions", claim="GitHub Actions reuses workflows via workflow_call",
                    domain="technical", source="agent", kind="fact", about="domain",
                    evidence="GitHub Actions supports reusable workflows through the workflow_call trigger")
        self.assertEqual(
            self.stored([fact], SPECULATIVE_USER, SPECULATIVE_AGENT),
            [{"claim": fact["claim"], "source": "agent", "kind": "fact"}],
        )

    def test_the_same_claim_stated_by_the_user_is_stored(self):
        stated = item(evidence="I'm working solo on a small side project")
        self.assertEqual(
            self.stored([stated], STATED_USER, STATED_AGENT),
            [{"claim": SOLO_CLAIM, "source": "user", "kind": "fact"}],
        )

    def test_assessment_needs_the_users_explicit_endorsement(self):
        agent = "Honestly, your pipeline is over-engineered for a team of three."
        assessment = item(claim="The CI pipeline is over-engineered for the team",
                          source="agent", kind="assessment",
                          evidence="your pipeline is over-engineered")
        self.assertEqual(self.stored([assessment], "Thanks, I'll think about it.", agent), [])
        endorsed = dict(assessment, endorsement="agreed, it's over-engineered")
        self.assertEqual(
            self.stored([endorsed], "Agreed, it's over-engineered. What should I cut?", agent),
            [{"claim": assessment["claim"], "source": "agent", "kind": "assessment"}],
        )

    def test_kinds_each_speaker_cannot_supply_are_skipped(self):
        evidence = {"agent": "one workflow file is plenty", "user": "I'm working solo"}
        for source, kind in (("agent", "decision"), ("agent", "preference"), ("user", "assessment")):
            with self.subTest(source=source, kind=kind):
                bad = item(source=source, kind=kind, evidence=evidence[source],
                           endorsement="I'm working solo")
                self.assertEqual(self.stored([bad], STATED_USER, SPECULATIVE_AGENT), [])

    def test_evidence_must_be_a_verbatim_span(self):
        user = "I’m working solo — on a small side-project!"
        loose = item(evidence="i'm working SOLO on a small side project")  # case/punctuation differ
        self.assertEqual(len(self.stored([loose], user, "")), 1)
        scattered = item(claim="Solo project", evidence="working solo project")
        paraphrase = item(claim="Single developer", evidence="I am a single developer")
        part_word = item(claim="Small project", evidence="mall side project")
        self.assertEqual(len(self.stored([scattered, paraphrase, part_word], user, "")), 1)

    def test_evidence_in_unspaced_scripts_is_matched_by_character(self):
        user = "我是后端开发者，主要写Go服务。"
        part = item(claim="Is a backend developer", evidence="后端开发者")
        mixed = item(claim="Writes Go services", evidence="主要写Go服务")
        too_short = item(claim="Writes Go", evidence="写Go")
        self.assertEqual(
            [r["claim"] for r in self.stored([part, mixed, too_short], user, "")],
            ["Is a backend developer", "Writes Go services"],
        )

    def test_evidence_needs_three_words_and_an_endorsement_two(self):
        # Every quote here is in the exchange, but a word or two turns up anywhere.
        user = "I want CI for my project. Thanks, agreed."
        agent = "Honestly, your pipeline is over-engineered for a team of three."
        assessment = item(claim="The CI pipeline is over-engineered", source="agent",
                          kind="assessment", evidence="your pipeline is over-engineered")
        too_short = [
            item(claim="Wants CI", evidence="I"),
            item(claim="Wants CI", evidence="my project"),
            dict(assessment, endorsement="thanks"),
        ]
        self.assertEqual(self.stored(too_short, user, agent), [])
        self.assertEqual(
            [e["reason"] for e in support.logged("extraction_skipped")],
            ["evidence too short", "evidence too short", "endorsement too short"],
        )
        long_enough = [
            item(claim="Wants CI", evidence="I want CI"),
            dict(assessment, endorsement="Thanks, agreed"),
        ]
        self.assertEqual(
            [r["claim"] for r in self.stored(long_enough, user, agent)],
            ["Wants CI", "The CI pipeline is over-engineered"],
        )

    def test_a_contraction_counts_as_one_word(self):
        user = "I'm a data engineer. I’m happy with that, don't worry."
        too_short = [
            item(claim="Is a data engineer", evidence="I'm a"),
            item(claim="Pipeline is fine", source="agent", kind="assessment",
                 evidence="your pipeline is over-engineered", endorsement="I’m"),
        ]
        self.assertEqual(self.stored(too_short, user, SPECULATIVE_AGENT + " Honestly, "
                                     "your pipeline is over-engineered."), [])
        self.assertEqual(
            [e["reason"] for e in support.logged("extraction_skipped")],
            ["evidence too short", "endorsement too short"],
        )
        # A straight quote matches a curly apostrophe in the message.
        self.assertEqual(
            len(self.stored([item(claim="Is a data engineer", evidence="I’m a data engineer")],
                            user, "")),
            1,
        )

    def test_evidence_in_thai_is_matched_mid_clause(self):
        user = "ฉันชอบใช้ Python มากกว่า Java ในงาน"
        preference = item(claim="Prefers Python to Java", kind="preference",
                          evidence="ใช้ Python มากกว่า Java")
        self.assertEqual([r["claim"] for r in self.stored([preference], user, "")],
                         ["Prefers Python to Java"])

    def test_about_must_be_given_and_valid(self):
        fact = item(topic_title="GitHub Actions", claim="GitHub Actions reuses workflows",
                    domain="technical", source="agent", kind="fact",
                    evidence="GitHub Actions supports reusable workflows")
        missing = {k: v for k, v in fact.items() if k != "about"}
        for bad in (missing, dict(fact, about=None), dict(fact, about="world")):
            with self.subTest(about=bad.get("about", "missing")):
                self.assertEqual(self.stored([bad], SPECULATIVE_USER, SPECULATIVE_AGENT), [])

    def test_an_update_is_checked_like_a_new_statement(self):
        stated = item(evidence="I'm working solo on a small side project")
        self.stored([stated], STATED_USER, STATED_AGENT)
        # A later exchange can't rewrite the user's fact with the agent's words.
        rewrites = [
            item(action="update_statement", statement_id=1, claim="Works solo with one workflow file",
                 evidence="one workflow file is plenty"),
            item(action="update_statement", statement_id=1, claim="Works solo with one workflow file",
                 source="agent", evidence="one workflow file is plenty"),
        ]
        self.assertEqual(
            self.stored(rewrites, SPECULATIVE_USER, SPECULATIVE_AGENT),
            [{"claim": SOLO_CLAIM, "source": "user", "kind": "fact"}],
        )

    def test_null_fields_skip_the_item_without_failing(self):
        stated = item(evidence="I'm working solo on a small side project")
        fields = ("action", "claim", "topic_title", "domain", "tags", "source", "kind",
                  "about", "evidence")
        nulls = [{**stated, "claim": f"{field} is null", field: None} for field in fields]
        nulls.append(item(claim="endorsement is null", source="agent", kind="assessment",
                          evidence="one workflow file is plenty", endorsement=None))
        self.assertEqual(
            self.stored(nulls + [stated], STATED_USER, SPECULATIVE_AGENT),
            [{"claim": SOLO_CLAIM, "source": "user", "kind": "fact"}],
        )


class TaskFieldsTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()

    def tearDown(self):
        self.conn.close()

    def test_null_or_non_string_task_fields_skip_the_item_without_failing(self):
        good = {"title": "Set up CI", "domain": "technical", "priority": "medium",
                "horizon": "soon", "action": "add_task"}
        bad = [
            {**good, "title": None},
            {**good, "domain": None},
            {**good, "priority": 3},
            {**good, "action": None},
            {"action": "add_update", "task_id": 1, "content": None},
            {"action": "update_task", "task_id": 1, "status": None, "content": None},
        ]
        analyze.process_tasks(self.conn, bad + [good], SID)
        self.assertEqual(
            [tuple(r) for r in self.conn.execute("SELECT title, priority FROM tasks")],
            [("Set up CI", "medium")],
        )


class AssessmentContextTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()
        self.conn.execute(
            "INSERT INTO topics (id, title, domain, tags) VALUES (1, 'CI', 'technical', 'ci')"
        )
        self.conn.executemany(
            "INSERT INTO statements (topic_id, claim, source, kind, session_id) "
            "VALUES (1, ?, ?, ?, ?)",
            [
                ("Team uses GitHub Actions", "user", "fact", SID),
                ("The pipeline is over-engineered", "agent", "assessment", SID),
                ("Legacy claim without provenance", None, None, None),
            ],
        )
        self.conn.commit()
        self.conn.close()

    def test_session_start_briefing_leaves_out_assessments(self):
        context = session_start.get_session_context()
        self.assertIn("Team uses GitHub Actions", context)
        self.assertIn("Legacy claim without provenance", context)
        self.assertNotIn("over-engineered", context)


def _mentions_solo(claim):
    """True if *claim* restates "works solo on a small side project", however worded."""
    return bool(re.search(
        r"\bsolo\b|\balone\b|\bsole\b|\bindependently\b|\bon (his|her|their) own\b"
        r"|\bby (him|her|them)sel(f|ves)\b|\b(single|one)[- ](person|man|developer|dev|maintainer)\b"
        r"|\b(side|personal|hobby|pet)[- ]project\b",
        claim.lower(),
    ))


@unittest.skipUnless(
    os.environ.get("LARVLING_LIVE_TESTS"),
    "set LARVLING_LIVE_TESTS=1 to run real extraction calls",
)
class LiveExtractionTest(unittest.TestCase):
    def extract(self, user_text, agent_text):
        fresh_db().close()
        path = os.path.join(support.PROJECT_DIR, f"transcript-{time.time_ns()}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": user_text}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {
                "role": "assistant", "content": [{"type": "text", "text": agent_text}]}}) + "\n")
        analyze._run({"session_id": SID, "transcript_path": path})
        with db.open_db() as conn:
            ran = conn.execute(
                "SELECT 1 FROM messages WHERE role = 'system' AND content LIKE 'Extraction:%'"
            ).fetchone()
            rows = [dict(r) for r in conn.execute("SELECT claim, source, kind FROM statements")]
        # _run logs and swallows a failed model call, which would leave no
        # statements and pass the speculation test for the wrong reason.
        self.assertTrue(ran, support.logged("extraction_error"))
        return rows

    def test_agent_speculation_about_the_user_produces_no_statement(self):
        rows = self.extract(SPECULATIVE_USER, SPECULATIVE_AGENT)
        self.assertEqual([r for r in rows if _mentions_solo(r["claim"])], [], rows)

    def test_the_same_claim_stated_by_the_user_is_stored(self):
        rows = self.extract(STATED_USER, STATED_AGENT)
        solo = [r for r in rows if _mentions_solo(r["claim"])]
        self.assertTrue(solo, rows)
        self.assertTrue(all(r["source"] == "user" for r in solo), rows)


if __name__ == "__main__":
    unittest.main()
