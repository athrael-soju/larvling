"""Full-text search and duplicate checks (#74): the FTS5 index, ranked search,
near-duplicate skips on insert, and the LIKE fallback without FTS5.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

import support

import analyze
import db
import preflight
import search
from test_provenance import make_v13_db, run_preflight

SID = "sess-1"
USER = (
    "We deploy the billing API with Kubernetes on GKE. The billing database is "
    "PostgreSQL 15. I want to refactor the auth modules and fix the login bug on iOS."
)


def fresh_db():
    support.reset_db()
    with contextlib.redirect_stdout(io.StringIO()):
        preflight.ensure_schema()
    conn = db.get_db()
    db.ensure_session(conn, SID)
    conn.commit()
    return conn


def knowledge(claim, evidence, title="Billing", action="add_topic", **extra):
    return {
        "topic_title": title, "claim": claim, "domain": "technical", "tags": "billing",
        "source": "user", "kind": "fact", "about": "user", "evidence": evidence,
        "action": action, **extra,
    }


def task(title):
    return {"title": title, "domain": "technical", "priority": "medium",
            "horizon": "soon", "action": "add_task"}


def write_config(data):
    with open(os.path.join(support.CLAUDE_DIR, "larvling.config.json"), "w") as f:
        json.dump(data, f)


class IndexTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()

    def tearDown(self):
        self.conn.close()

    def indexed(self, table):
        return sorted(tuple(r) for r in self.conn.execute(f"SELECT rowid, * FROM {table}"))

    def test_the_index_follows_every_write(self):
        c = self.conn
        c.execute("INSERT INTO topics (id, title, domain, tags) VALUES (1, 'Billing', 'technical', 'b')")
        c.execute("INSERT INTO topics (id, title, domain, tags) VALUES (2, 'Infra', 'technical', 'i')")
        c.execute("INSERT INTO statements (id, topic_id, claim) VALUES (1, 1, 'Uses Stripe')")
        c.execute("INSERT INTO statements (id, topic_id, claim) VALUES (2, 1, 'Runs on GKE')")
        self.assertEqual(self.indexed("statements_fts"),
                         [(1, "Uses Stripe", "Billing"), (2, "Runs on GKE", "Billing")])
        c.execute("UPDATE statements SET claim = 'Uses Stripe and Adyen' WHERE id = 1")
        c.execute("UPDATE statements SET topic_id = 2 WHERE id = 2")
        c.execute("UPDATE topics SET title = 'Payments' WHERE id = 1")
        c.execute("DELETE FROM statements WHERE id = 2")
        self.assertEqual(self.indexed("statements_fts"), [(1, "Uses Stripe and Adyen", "Payments")])

        c.execute("INSERT INTO tasks (id, title, domain) VALUES (1, 'Add retries', 'technical')")
        c.execute("UPDATE tasks SET title = 'Add webhook retries' WHERE id = 1")
        self.assertEqual(self.indexed("tasks_fts"), [(1, "Add webhook retries")])
        c.execute("DELETE FROM tasks WHERE id = 1")
        self.assertEqual(self.indexed("tasks_fts"), [])

    def test_an_upgraded_db_indexes_existing_rows(self):
        support.reset_db()
        make_v13_db()
        self.assertEqual(run_preflight()[0], "migrated")
        with db.open_db() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM statements_fts").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0],
            )
            self.assertTrue(db.fts_in_step(conn))

    def test_a_missing_index_is_rebuilt_at_the_next_start(self):
        for name in db.FTS_TRIGGERS:
            self.conn.execute(f"DROP TRIGGER {name}")
        self.conn.execute("INSERT INTO topics (id, title, domain, tags) VALUES (1, 'Billing', 'technical', 'b')")
        self.conn.execute("INSERT INTO statements (topic_id, claim) VALUES (1, 'Written while unindexed')")
        self.conn.commit()
        self.assertEqual(run_preflight()[0], "current")
        self.assertTrue(db.fts_in_step(self.conn))
        self.assertEqual(self.indexed("statements_fts"), [(1, "Written while unindexed", "Billing")])


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()
        c = self.conn
        c.execute("INSERT INTO topics (id, title, domain, tags) VALUES (1, 'Billing API', 'technical', 'b')")
        c.execute("INSERT INTO topics (id, title, domain, tags) VALUES (2, 'Kubernetes', 'technical', 'k')")
        c.executemany(
            "INSERT INTO statements (id, topic_id, claim, kind) VALUES (?, ?, ?, ?)",
            [
                (1, 1, "Deployed on GKE with Kubernetes", "fact"),
                (2, 2, "Cluster autoscaling is enabled", "fact"),
                (3, 1, "Billing deploys are over-engineered", "assessment"),
                (4, 1, "Billing database is MySQL", "fact"),
                (5, 1, "Billing database is PostgreSQL 15", "fact"),
            ],
        )
        c.execute("UPDATE statements SET superseded_by = 5, superseded_at = datetime('now') WHERE id = 4")
        c.executemany(
            "INSERT INTO tasks (id, title, domain, status) VALUES (?, ?, 'technical', ?)",
            [(1, "Deploy billing to GKE", "done"), (2, "Rotate billing keys", "open")],
        )

    def tearDown(self):
        self.conn.close()

    def ids(self, rows):
        return [r["id"] for r in rows]

    def test_ranks_statements_and_topic_titles_in_one_search(self):
        rows = search.search_statements(self.conn, "kubernetes deploying")
        # A claim with both words first; a statement found by its topic's title too.
        self.assertEqual(self.ids(rows)[:2], [1, 2])

    def test_leaves_out_superseded_statements_and_assessments(self):
        found = self.ids(search.search_statements(self.conn, "billing database"))
        self.assertEqual(found[0], 5)
        self.assertNotIn(4, found)  # superseded by 5
        self.assertNotIn(3, found)  # an assessment
        self.assertIn(3, self.ids(search.search_statements(self.conn, "over-engineered",
                                                           assessments=True)))
        self.assertEqual(search.search_statements(self.conn, "over-engineered"), [])

    def test_searches_tasks_in_every_status(self):
        rows = search.search_tasks(self.conn, "billing")
        self.assertEqual(sorted(self.ids(rows)), [1, 2])

    def test_query_syntax_in_the_words_is_harmless(self):
        for text in ('billing" OR "x', "NEAR(billing", "billing*", "-", "  "):
            with self.subTest(text=text):
                search.search_statements(self.conn, text)

    def test_falls_back_to_like_without_fts5(self):
        with mock.patch.object(db, "_fts5", False):
            self.assertEqual(self.ids(search.search_statements(self.conn, "kubernetes gke"))[0], 1)
            self.assertEqual(sorted(self.ids(search.search_tasks(self.conn, "billing"))), [1, 2])

    def test_command_line(self):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=support.PROJECT_DIR)
        script = os.path.join(support.SCRIPTS_DIR, "search.py")
        self.conn.commit()

        out = subprocess.run([sys.executable, script, "billing", "database", "--json"],
                             capture_output=True, text=True, env=env)
        self.assertEqual([r["id"] for r in json.loads(out.stdout)][0], 5)
        out = subprocess.run([sys.executable, script, "rotate", "--tasks"],
                             capture_output=True, text=True, env=env)
        self.assertIn("Rotate billing keys", out.stdout)
        out = subprocess.run([sys.executable, script, "zebra"], capture_output=True, text=True, env=env)
        self.assertEqual(out.stdout.strip(), "No matches.")
        out = subprocess.run([sys.executable, script], capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 1)


class DuplicateTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()
        analyze.process_knowledge(
            self.conn, [knowledge("Billing database is PostgreSQL 15", "billing database is PostgreSQL 15")],
            SID, USER, "")
        analyze.process_tasks(self.conn, [task("Refactor the auth modules")], SID)
        analyze.process_tasks(self.conn, [task("Fix the login bug on iOS")], SID)
        self.conn.execute("UPDATE tasks SET status = 'dropped' WHERE id = 2")

    def tearDown(self):
        self.conn.close()

    def count(self, table):
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def skipped(self):
        return [(e["action"], e["reason"]) for e in support.logged("extraction_skipped")]

    def test_reworded_task_titles_are_caught_in_any_status(self):
        result = analyze.process_tasks(self.conn, [
            task("auth module refactor"),        # reordered, singular
            task("Fix login bugs on iOS"),       # rewords a dropped task
            task("Fix the login bug on Android"),  # a different task
        ], SID)
        self.assertEqual(result, (1, 0, 0))
        self.assertEqual(self.skipped(), [("add_task", "near_duplicate")] * 2)

    def test_reworded_statements_are_skipped(self):
        analyze.process_knowledge(self.conn, [
            knowledge("The billing database: PostgreSQL 15", "billing database is PostgreSQL 15",
                      action="add_statement", topic_id=1),
            knowledge("PostgreSQL 15 is the billing database", "billing database is PostgreSQL 15",
                      title="Databases"),
            knowledge("Deploys billing API on GKE", "deploy the billing API with Kubernetes on GKE",
                      action="add_statement", topic_id=1),
        ], SID, USER, "")
        self.assertEqual(self.count("statements"), 2)
        self.assertEqual(self.skipped(), [("add_statement", "near_duplicate"),
                                          ("add_topic", "near_duplicate")])

    def test_a_reworded_topic_title_reuses_the_topic(self):
        analyze.process_knowledge(self.conn, [
            knowledge("Billing API runs on GKE", "deploy the billing API with Kubernetes on GKE",
                      title="billing"),
        ], SID, USER, "")
        self.assertEqual(self.count("topics"), 1)
        self.assertEqual(self.count("statements"), 2)

    def test_the_threshold_is_configurable_and_zero_turns_it_off(self):
        write_config({"dedup_threshold": 0})
        result = analyze.process_tasks(self.conn, [task("auth module refactor")], SID)
        self.assertEqual(result, (1, 0, 0))

    def test_duplicates_are_caught_without_fts5_too(self):
        with mock.patch.object(db, "_fts5", False):
            result = analyze.process_tasks(self.conn, [task("auth module refactor")], SID)
        self.assertEqual(result, (0, 0, 0))

    def test_the_extractor_is_told_to_search(self):
        prompt = analyze.build_extraction_prompt([(1, "user", "hi")], SID)
        self.assertIn("search.py", prompt)
        self.assertNotIn("Use LIKE queries", prompt)


class WithoutFts5Test(unittest.TestCase):
    def test_triggers_are_dropped_so_writes_keep_working(self):
        conn = fresh_db()
        with mock.patch.object(db, "_fts5", False):
            self.assertFalse(db.fts_in_step(conn))
            self.assertFalse(db.ensure_fts(conn))
            self.assertTrue(db.fts_in_step(conn))
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger'").fetchone()[0], 0)
        conn.commit()
        conn.close()
        # Back on a build with FTS5, the next start rebuilds the index.
        self.assertEqual(run_preflight()[0], "current")
        with db.open_db() as conn:
            self.assertTrue(db.fts_in_step(conn))

    def test_a_fresh_install_works_without_fts5(self):
        support.reset_db()
        with mock.patch.object(db, "_fts5", False):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                preflight.main()
            self.assertIn("no FTS5", out.getvalue())
            with db.open_db() as conn:
                self.assertFalse(db.has_table(conn, "statements_fts"))
                conn.execute("INSERT INTO tasks (title, domain) VALUES ('Write docs', 'technical')")
                self.assertEqual(len(search.search_tasks(conn, "docs")), 1)


class SimilarityTest(unittest.TestCase):
    def test_similarity(self):
        cases = [
            ("Refactor the auth modules", "auth module refactor", 1.0),
            ("Add tests for the export skill", "Add test to export skill", 1.0),
            ("Fix login bug on iOS", "Fix login bug on Android", 0.6),
            ("", "anything", 0.0),
        ]
        for a, b, expected in cases:
            with self.subTest(a=a, b=b):
                self.assertAlmostEqual(search.similarity(a, b), expected)


if __name__ == "__main__":
    unittest.main()
