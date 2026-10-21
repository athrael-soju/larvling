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
import subprocess
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

    def test_superseding_into_a_claim_already_current_points_at_it(self):
        # A legacy correction stored next to the claim it corrects.
        self.conn.execute(
            "INSERT INTO statements (id, topic_id, claim, source, kind) "
            "VALUES (2, 1, 'Production database is PostgreSQL 15', 'user', 'fact')"
        )
        self.assertEqual(self.supersede(), (0, 0, 1, 0))
        self.assertEqual(self.current(), [(2, 1, "Production database is PostgreSQL 15")])
        self.assertEqual(self.conn.execute(
            "SELECT superseded_by FROM statements WHERE id = 1").fetchone()[0], 2)


PLUGIN_DIR = os.path.join(support.SCRIPTS_DIR, "..")


def recipe(path, start):
    """The SQL in *path* (relative to the plugin) that starts with *start*,
    from inline code or a code block, as the agent would copy it."""
    with open(os.path.join(PLUGIN_DIR, path), encoding="utf-8") as f:
        text = f.read()
    for sql in re.findall(r"`([^`\n]+)`", text) + [line.strip() for line in text.splitlines()]:
        if sql.startswith(start):
            return sql.strip().rstrip(";")
    raise AssertionError(f"no recipe starting {start!r} in {path}")


def fill(sql, **values):
    for name, value in values.items():
        pattern = r"\bN\b" if name == "N" else re.escape(f"<{name}>")
        sql = re.sub(pattern, lambda _: str(value), sql)
    return sql


def run_query(sql):
    """Run *sql* through query.py, a connection of its own, as the agent does."""
    out = subprocess.run(
        [sys.executable, os.path.join(support.SCRIPTS_DIR, "query.py"), sql],
        capture_output=True, text=True, env={**os.environ, "CLAUDE_PROJECT_DIR": support.PROJECT_DIR},
    )
    if out.returncode:
        raise AssertionError(out.stderr)
    match = re.match(r"(-?\d+) row\(s\) affected", out.stdout)
    return int(match.group(1)) if match else out.stdout


class RecipesTest(unittest.TestCase):
    """The SQL the skills and the maintenance agent tell the model to run."""

    def setUp(self):
        fresh_db().close()

    def query(self, sql, *args):
        conn = db.get_db()
        try:
            return [tuple(r) for r in conn.execute(sql, args)]
        finally:
            conn.close()

    def add(self, sid, claim):
        run_query(f"INSERT INTO statements (id, topic_id, claim, source, kind) "
                  f"VALUES ({sid}, 1, '{claim}', 'manual', 'fact')")

    def test_remember_supersedes_once_and_only_onto_a_stored_statement(self):
        update = recipe("skills/remember/SKILL.md", "UPDATE statements SET superseded_by")
        self.add(2, "Production database is PostgreSQL")
        self.assertEqual(run_query(fill(update, N=1).replace("'...'", "'Production database is PostgreSQL'")), 1)
        self.add(3, "Production database is MariaDB")
        # Already superseded: the 1 -> 2 link stays.
        self.assertEqual(run_query(fill(update, N=1).replace("'...'", "'Production database is MariaDB'")), 0)
        # The replacement was never stored: nothing is retired.
        self.assertEqual(run_query(fill(update, N=3).replace("'...'", "'Not stored'")), 0)
        self.assertEqual(self.query("SELECT id, superseded_by FROM statements WHERE id IN (1, 3)"),
                         [(1, 2), (3, None)])

    def test_tidy_correction_is_manual_in_this_session_and_guarded(self):
        insert = recipe("agents/knowledge-maintenance.md", "INSERT INTO statements")
        update = recipe("agents/knowledge-maintenance.md",
                        "UPDATE statements SET superseded_by = (SELECT MAX(id)")
        values = {"old_id": 1, "session id": SID}
        claim = "Production database is PostgreSQL"
        run_query(fill(insert, **values).replace("<corrected claim>", claim))
        self.assertEqual(run_query(fill(update, **values).replace("<corrected claim>", claim)), 1)
        self.assertEqual(self.query("SELECT claim, source, session_id FROM current_statements"),
                         [(claim, "manual", SID)])
        # Run again: the old statement is already superseded.
        run_query(fill(insert, **values).replace("<corrected claim>", "Another claim"))
        self.assertEqual(run_query(fill(update, **values).replace("<corrected claim>", "Another claim")), 0)
        self.assertEqual(self.query("SELECT superseded_by FROM statements WHERE id = 1"), [(2,)])

    def test_tidy_duplicates_cannot_supersede_each_other(self):
        dup = recipe("agents/knowledge-maintenance.md",
                     "UPDATE statements SET superseded_by = <survivor_id>")
        self.add(2, "Production DB is MySQL")
        self.assertEqual(run_query(fill(dup, survivor_id=2, duplicate_id=1)), 1)
        self.assertEqual(run_query(fill(dup, survivor_id=1, duplicate_id=2)), 0)
        self.assertEqual(run_query(fill(dup, survivor_id=2, duplicate_id=2)), 0)
        self.assertEqual(self.query("SELECT id FROM current_statements"), [(2,)])

    def test_tidy_retire_leaves_a_superseded_statement_alone(self):
        retire = recipe("agents/knowledge-maintenance.md", "UPDATE statements SET superseded_at")
        self.add(2, "Production database is PostgreSQL")
        run_query("UPDATE statements SET superseded_by = 2, superseded_at = '2026-01-01 00:00:00' WHERE id = 1")
        self.assertEqual(run_query(fill(retire, id=1)), 0)
        self.assertEqual(run_query(fill(retire, id=2)), 1)
        self.assertEqual(self.query("SELECT id, superseded_by, superseded_at FROM statements WHERE id = 1"),
                         [(1, 2, "2026-01-01 00:00:00")])

    def test_forget_finds_every_earlier_version(self):
        older = recipe("skills/forget/SKILL.md", "WITH RECURSIVE older")
        self.add(2, "Production database is PostgreSQL")
        self.add(3, "Production database is PostgreSQL 15")
        run_query("UPDATE statements SET superseded_by = id + 1, superseded_at = datetime('now') WHERE id < 3")
        found = run_query(fill(older, N=3) + " ORDER BY s.id")
        self.assertIn("Production database is MySQL", found)
        self.assertIn("Production database is PostgreSQL ", found)
        self.assertIn("(2 rows)", found)
        run_query("DELETE FROM statements WHERE id IN (3, 2, 1)")
        self.assertEqual(self.query("SELECT COUNT(*) FROM statements"), [(0,)])


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
