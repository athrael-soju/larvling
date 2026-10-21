"""Supersession (#73): a correction replaces a statement instead of sitting
next to it, current_statements lists what is still in force, and the chain
stays queryable.

LiveSupersessionTest calls the model, so it only runs with LARVLING_LIVE_TESTS=1.
"""

import contextlib
import io
import os
import re
import sqlite3
import sys
import unittest

import support

import analyze
import db
import preflight
from test_provenance import columns, make_v13_db, run_preflight

sys.path.insert(0, os.path.join(support.SCRIPTS_DIR, "hooks"))
import prompt  # noqa: E402
import session_start  # noqa: E402

SID = "sess-1"
USER = "We run PostgreSQL 15 in production. We moved off MySQL last year."


def item(claim, evidence, **extra):
    return {
        "topic_title": "Database",
        "claim": claim,
        "domain": "technical",
        "tags": "database",
        "source": "user",
        "kind": "fact",
        "about": "user",
        "evidence": evidence,
        "action": "add_topic",
        **extra,
    }


def fresh_db():
    support.reset_db()
    with contextlib.redirect_stdout(io.StringIO()):
        preflight.ensure_schema()
    conn = db.get_db()
    db.ensure_session(conn, SID)
    conn.execute("INSERT INTO topics (id, title, domain, tags) VALUES (1, 'Database', 'technical', 'db')")
    conn.execute(
        "INSERT INTO statements (id, topic_id, claim, source, kind) "
        "VALUES (1, 1, 'Production database is MySQL', 'user', 'fact')"
    )
    conn.commit()
    return conn


class SupersedeTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()

    def tearDown(self):
        self.conn.close()

    def supersede(self, statement_id=1, claim="Production database is PostgreSQL 15",
                  evidence="We run PostgreSQL 15 in production"):
        return analyze.process_knowledge(
            self.conn,
            [item(claim, evidence, action="supersede_statement", statement_id=statement_id)],
            SID, USER, "",
        )

    def current(self):
        return [tuple(r) for r in self.conn.execute(
            "SELECT id, topic_id, claim FROM current_statements ORDER BY id")]

    def test_a_correction_leaves_exactly_one_current_statement(self):
        self.assertEqual(self.supersede(), (0, 0, 1, 0))
        self.assertEqual(self.current(), [(2, 1, "Production database is PostgreSQL 15")])
        old = self.conn.execute(
            "SELECT claim, superseded_by, superseded_at FROM statements WHERE id = 1"
        ).fetchone()
        self.assertEqual(old["claim"], "Production database is MySQL")  # kept, not rewritten
        self.assertEqual(old["superseded_by"], 2)
        self.assertIsNotNone(old["superseded_at"])

    def test_the_chain_is_queryable(self):
        self.supersede()
        self.supersede(2, "Production database is PostgreSQL 15 on RDS", "We run PostgreSQL 15")
        chain = []
        sid = 3
        while sid:
            chain.append(sid)
            row = self.conn.execute(
                "SELECT id FROM statements WHERE superseded_by = ?", (sid,)).fetchone()
            sid = row[0] if row else None
        self.assertEqual(chain, [3, 2, 1])
        self.assertEqual([r[0] for r in self.current()], [3])

    def test_a_superseded_statement_cannot_be_superseded_again(self):
        self.supersede()
        self.assertEqual(self.supersede(1, "Production database is MariaDB", "We run PostgreSQL 15"),
                         (0, 0, 0, 0))
        self.assertEqual(support.logged("extraction_skipped")[-1]["reason"],
                         "statement already superseded")

    def test_the_replacement_needs_evidence_like_any_new_statement(self):
        self.assertEqual(self.supersede(evidence="we run Oracle"), (0, 0, 0, 0))
        self.assertEqual(self.current(), [(1, 1, "Production database is MySQL")])

    def test_superseding_with_the_same_claim_changes_nothing(self):
        self.supersede(claim="Production database is MySQL", evidence="moved off MySQL last year")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0], 1)

    def test_a_superseded_claim_does_not_block_stating_it_again(self):
        self.supersede()
        analyze.process_knowledge(
            self.conn,
            [item("Production database is MySQL", "moved off MySQL last year",
                  action="add_statement", topic_id=1)],
            SID, USER, "",
        )
        self.assertEqual(len(self.current()), 2)

    def test_a_retired_statement_is_not_current(self):
        self.conn.execute("UPDATE statements SET superseded_at = datetime('now') WHERE id = 1")
        self.assertEqual(self.current(), [])

    def test_deleting_a_replacement_leaves_the_old_statement_retired(self):
        self.supersede()
        self.conn.execute("DELETE FROM statements WHERE id = 2")
        row = self.conn.execute(
            "SELECT superseded_by, superseded_at FROM statements WHERE id = 1").fetchone()
        self.assertIsNone(row["superseded_by"])
        self.assertIsNotNone(row["superseded_at"])
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_superseded_by_must_name_a_statement(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE statements SET superseded_by = 99 WHERE id = 1")


class ReadersTest(unittest.TestCase):
    def setUp(self):
        conn = fresh_db()
        analyze.process_knowledge(
            conn,
            [item("Production database is PostgreSQL 15", "We run PostgreSQL 15 in production",
                  action="supersede_statement", statement_id=1)],
            SID, USER, "",
        )
        conn.commit()
        conn.close()

    def test_session_start_counts_and_lists_current_statements(self):
        context = session_start.get_session_context()
        self.assertIn("(1 topics, 1 statements)", context)
        self.assertIn("PostgreSQL 15", context)
        self.assertNotIn("is MySQL", context)

    def test_prompt_hint_counts_current_statements(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            prompt.handle({"session_id": SID, "prompt": "hello"})
        self.assertIn("1 topic(s), 1 statement(s)", out.getvalue())


class MigrationTest(unittest.TestCase):
    def test_upgrade_adds_supersession_and_keeps_every_statement_current(self):
        support.reset_db()
        make_v13_db()
        self.assertEqual(run_preflight()[0], "migrated")
        with db.open_db() as conn:
            self.assertEqual(db.get_schema_version(conn), db.SCHEMA_VERSION)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM current_statements").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0],
            )
            fresh = sqlite3.connect(":memory:")
            db.create_schema(fresh)
            self.assertEqual(columns(conn, "statements"), columns(fresh, "statements"))
            self.assertEqual(columns(conn, "current_statements"), columns(fresh, "current_statements"))
            fresh.close()
            self.assertIn("CREATE VIEW", db.get_current_schema(conn))
            self.assertIn("idx_statements_superseded_by",
                          {r[1] for r in conn.execute("PRAGMA index_list(statements)")})


@unittest.skipUnless(
    os.environ.get("LARVLING_LIVE_TESTS"),
    "set LARVLING_LIVE_TESTS=1 to run real extraction calls",
)
class LiveSupersessionTest(unittest.TestCase):
    def test_a_correction_in_a_later_session_supersedes_the_old_statement(self):
        fresh_db().close()
        with db.open_db() as conn:
            db.record_message(conn, SID, "user",
                              "Quick update: we migrated production from MySQL to PostgreSQL 15 last month.")
            db.record_message(conn, SID, "assistant",
                              "Thanks. PostgreSQL 15 supports MERGE and logical replication of row filters.")
        analyze._run({"session_id": SID, "hook_event_name": "SessionEnd"})
        self.assertFalse(support.logged("extraction_error"), support.logged("extraction_error"))
        with db.open_db() as conn:
            current = [r[0] for r in conn.execute(
                "SELECT claim FROM current_statements WHERE topic_id = 1")]
            old = conn.execute("SELECT superseded_by FROM statements WHERE id = 1").fetchone()[0]
        self.assertIsNotNone(old, current)
        db_claims = [c for c in current if re.search(r"mysql|postgres", c, re.I)]
        self.assertEqual(len(db_claims), 1, current)
        self.assertRegex(db_claims[0], re.compile("postgres", re.I))


if __name__ == "__main__":
    unittest.main()
