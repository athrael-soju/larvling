"""Statement provenance (#70): schema v14, extraction writes, v13 -> v14 migration."""

import contextlib
import glob
import io
import os
import sqlite3
import unittest

import support

import analyze
import db
import preflight

# Frozen copy of the v13 schema (create_schema before provenance landed).
V13_SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_min REAL,
    title TEXT,
    agent_summary TEXT,
    exchange_count INTEGER,
    summary_at TEXT,
    summary_msg_count INTEGER,
    tags TEXT,
    summary_offered INTEGER DEFAULT 0
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    role TEXT NOT NULL,
    content TEXT,
    metadata TEXT
);
CREATE TABLE topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    domain TEXT NOT NULL CHECK(domain IN ('personal', 'professional', 'preferences', 'interests', 'knowledge', 'technical', 'workflow')),
    tags TEXT NOT NULL,
    created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE statements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL REFERENCES topics(id),
    claim TEXT NOT NULL,
    created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    domain TEXT NOT NULL CHECK(domain IN ('personal', 'professional', 'preferences', 'interests', 'knowledge', 'technical', 'workflow')),
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'done', 'dropped')),
    priority TEXT NOT NULL DEFAULT 'medium' CHECK(priority IN ('low', 'medium', 'high')),
    horizon TEXT NOT NULL DEFAULT 'later' CHECK(horizon IN ('now', 'soon', 'later')),
    metadata TEXT,
    created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    content TEXT NOT NULL,
    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_messages_session ON messages(session_id);
CREATE INDEX idx_messages_session_role ON messages(session_id, role);
CREATE INDEX idx_statements_topic ON statements(topic_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_updates_task ON updates(task_id);
"""

MARKER = "Extraction: knowledge=1 statements, no tasks"

# (session_id, timestamp, role, content)
V13_MESSAGES = [
    # sess-a: extraction lands ~2 min after the reply, with its marker.
    ("sess-a", "2026-05-01 10:00:00", "user", "I live in Lisbon"),
    ("sess-a", "2026-05-01 10:01:00", "assistant", "Noted."),
    ("sess-a", "2026-05-01 10:03:00", "system", MARKER),
    # sess-b: /remember writes mid-turn, between prompt and reply.
    ("sess-b", "2026-05-01 11:00:00", "user", "/remember I prefer tabs"),
    ("sess-b", "2026-05-01 11:02:00", "assistant", "Stored."),
    # sess-a and sess-c both extract in the same second: ambiguous.
    ("sess-a", "2026-05-01 12:00:00", "system", MARKER),
    ("sess-c", "2026-05-01 12:00:00", "system", MARKER),
    # sess-c: turn already finished when the statement appears, no marker.
    ("sess-c", "2026-05-01 13:00:00", "user", "hello"),
    ("sess-c", "2026-05-01 13:01:00", "assistant", "hi"),
]

# statement id -> (created, expected session_id after backfill)
V13_STATEMENTS = {
    1: ("2026-05-01 10:03:00", "sess-a"),  # extraction marker, same second
    2: ("2026-05-01 11:01:00", "sess-b"),  # mid-turn write
    3: ("2026-05-01 12:00:00", None),      # two sessions' markers match
    4: ("2026-05-01 13:05:00", None),      # after a finished turn, no marker
    5: ("2026-01-01 00:00:00", None),      # nothing nearby
}


def make_v13_db():
    conn = sqlite3.connect(db.DB_PATH)
    conn.executescript(V13_SCHEMA)
    for sid in ("sess-a", "sess-b", "sess-c"):
        conn.execute(
            "INSERT INTO sessions (id, started_at) VALUES (?, '2026-05-01 09:00:00')",
            (sid,),
        )
    conn.executemany(
        "INSERT INTO messages (session_id, timestamp, role, content) VALUES (?, ?, ?, ?)",
        V13_MESSAGES,
    )
    conn.execute(
        "INSERT INTO topics (id, title, domain, tags) VALUES (1, 'Home', 'personal', 'home')"
    )
    for sid, (created, _) in V13_STATEMENTS.items():
        conn.execute(
            "INSERT INTO statements (id, topic_id, claim, created, updated) "
            "VALUES (?, 1, ?, ?, ?)",
            (sid, f"claim {sid}", created, created),
        )
    conn.execute("PRAGMA user_version = 13")
    conn.commit()
    conn.close()


def run_preflight():
    """Run ensure_schema, returning (result, captured stdout)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = preflight.ensure_schema()
    return result, out.getvalue()


def columns(conn, table):
    return [
        (r[1], r[2], r[3], r[4], r[5])  # name, type, notnull, default, pk
        for r in conn.execute(f"PRAGMA table_info({table})")
    ]


class FreshSchemaTest(unittest.TestCase):
    def setUp(self):
        support.reset_db()

    def test_fresh_install_has_provenance_columns(self):
        self.assertEqual(run_preflight()[0], "fresh")
        with db.open_db() as conn:
            self.assertEqual(db.get_schema_version(conn), 14)
            names = [c[0] for c in columns(conn, "statements")]
            self.assertEqual(names[-3:], ["source", "kind", "session_id"])
            indexes = {r[1] for r in conn.execute("PRAGMA index_list(statements)")}
            self.assertIn("idx_statements_session", indexes)

    def test_check_constraints_reject_unknown_values(self):
        run_preflight()
        with db.open_db() as conn:
            conn.execute("INSERT INTO topics (id, title, domain, tags) VALUES (1, 't', 'knowledge', 't')")
            for col, bad in (("source", "guess"), ("kind", "rumour")):
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        f"INSERT INTO statements (topic_id, claim, {col}) VALUES (1, 'c', ?)",
                        (bad,),
                    )


class MigrationTest(unittest.TestCase):
    def setUp(self):
        support.reset_db()
        make_v13_db()

    def test_migrates_v13_in_code_without_data_loss(self):
        result, out = run_preflight()
        self.assertEqual(result, "migrated")
        self.assertEqual(out, "")  # nothing for the agent to do

        with db.open_db() as conn:
            self.assertEqual(db.get_schema_version(conn), 14)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                len(V13_MESSAGES),
            )
            rows = {
                r["id"]: r
                for r in conn.execute(
                    "SELECT id, claim, created, source, kind, session_id FROM statements"
                )
            }
            self.assertEqual(set(rows), set(V13_STATEMENTS))
            for sid, (created, expected_session) in V13_STATEMENTS.items():
                row = rows[sid]
                self.assertEqual(row["claim"], f"claim {sid}")
                self.assertEqual(row["created"], created)
                self.assertEqual(row["session_id"], expected_session, f"statement {sid}")
                self.assertIsNone(row["source"])
                self.assertIsNone(row["kind"])
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_migrated_schema_matches_fresh_schema(self):
        run_preflight()
        fresh = sqlite3.connect(":memory:")
        db.create_schema(fresh)
        with db.open_db() as conn:
            for table in ("sessions", "messages", "topics", "statements", "tasks", "updates"):
                self.assertEqual(columns(conn, table), columns(fresh, table), table)
            index_sql = "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
            self.assertEqual(
                sorted(r[0] for r in conn.execute(index_sql)),
                sorted(r[0] for r in fresh.execute(index_sql)),
            )
            # The added CHECK constraints are live on the migrated table too.
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE statements SET source = 'guess' WHERE id = 1")
        fresh.close()

    def test_backup_never_overwrites_an_earlier_one(self):
        earlier = db.DB_PATH + ".v13.bak"
        with open(earlier, "wb") as f:
            f.write(b"an older backup")

        run_preflight()

        with open(earlier, "rb") as f:
            self.assertEqual(f.read(), b"an older backup")
        new_backups = glob.glob(db.DB_PATH + ".v13.*.bak")
        self.assertEqual(len(new_backups), 1)
        bak = sqlite3.connect(new_backups[0])
        try:
            self.assertEqual(bak.execute("PRAGMA user_version").fetchone()[0], 13)
            self.assertEqual(
                bak.execute("SELECT COUNT(*) FROM statements").fetchone()[0],
                len(V13_STATEMENTS),
            )
        finally:
            bak.close()

    def test_failed_step_rolls_back_and_falls_back_to_agent(self):
        def broken_step(conn):
            conn.execute("ALTER TABLE statements ADD COLUMN source TEXT")
            raise RuntimeError("boom")

        saved = dict(preflight.MIGRATIONS)
        preflight.MIGRATIONS[13] = broken_step
        try:
            result, out = run_preflight()
        finally:
            preflight.MIGRATIONS.clear()
            preflight.MIGRATIONS.update(saved)

        self.assertEqual(result, "migrate")
        self.assertIn("Schema Migration Required", out)
        with db.open_db() as conn:
            self.assertEqual(db.get_schema_version(conn), 13)
            names = [c[0] for c in columns(conn, "statements")]
            self.assertNotIn("source", names)

    def test_unregistered_version_falls_back_to_agent(self):
        with db.open_db() as conn:
            db.set_schema_version(conn, 12)
        result, out = run_preflight()
        self.assertEqual(result, "migrate")
        self.assertIn("version **12**, expected **14**", out)


class ProcessKnowledgeTest(unittest.TestCase):
    SID = "sess-1"

    def setUp(self):
        support.reset_db()
        run_preflight()
        self.conn = db.get_db()
        db.ensure_session(self.conn, self.SID)

    def tearDown(self):
        self.conn.close()

    def item(self, **overrides):
        item = {
            "topic_title": "Editor",
            "claim": "Prefers tabs over spaces",
            "domain": "preferences",
            "tags": "editor",
            "source": "user",
            "kind": "preference",
            "action": "add_topic",
        }
        item.update(overrides)
        return item

    def provenance(self, stmt_id):
        return tuple(
            self.conn.execute(
                "SELECT source, kind, session_id FROM statements WHERE id = ?",
                (stmt_id,),
            ).fetchone()
        )

    def test_schema_requires_source_and_kind(self):
        items = analyze.EXTRACTION_SCHEMA["properties"]["knowledge"]["items"]
        self.assertIn("source", items["required"])
        self.assertIn("kind", items["required"])
        self.assertEqual(set(items["properties"]["source"]["enum"]), {"user", "agent"})

    def test_new_statements_carry_provenance(self):
        analyze.process_knowledge(self.conn, [self.item()], self.SID)
        topic_id = self.conn.execute("SELECT id FROM topics").fetchone()[0]
        analyze.process_knowledge(
            self.conn,
            [self.item(action="add_statement", topic_id=topic_id,
                       claim="Tabs render at width 4", source="agent", kind="fact")],
            self.SID,
        )
        self.assertEqual(self.provenance(1), ("user", "preference", self.SID))
        self.assertEqual(self.provenance(2), ("agent", "fact", self.SID))

    def test_update_takes_the_updaters_provenance(self):
        analyze.process_knowledge(self.conn, [self.item()], self.SID)
        db.ensure_session(self.conn, "sess-2")
        analyze.process_knowledge(
            self.conn,
            [self.item(action="update_statement", statement_id=1,
                       claim="Uses tabs, but spaces in YAML", kind="decision")],
            "sess-2",
        )
        self.assertEqual(self.provenance(1), ("user", "decision", "sess-2"))

    def test_items_without_valid_provenance_are_skipped(self):
        bad_items = [
            self.item(source="doc"),  # valid in the DB, not for extraction
            self.item(source=""),
            self.item(kind="rumour"),
            self.item(kind=""),
        ]
        counts = analyze.process_knowledge(self.conn, bad_items, self.SID)
        self.assertEqual(counts, (0, 0, 0, 0))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
