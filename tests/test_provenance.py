"""Statement provenance (#70): schema v14, extraction writes, v13 -> v14 migration."""

import contextlib
import glob
import io
import os
import sqlite3
import unittest
from unittest import mock

import support

import analyze
import db
import preflight

# The v13 schema, copied from create_schema before v14.
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


def make_v13_db(messages=V13_MESSAGES, statements=V13_STATEMENTS,
                sessions=("sess-a", "sess-b", "sess-c")):
    conn = sqlite3.connect(db.DB_PATH)
    conn.executescript(V13_SCHEMA)
    for sid in sessions:
        conn.execute(
            "INSERT INTO sessions (id, started_at) VALUES (?, '2026-05-01 09:00:00')",
            (sid,),
        )
    conn.executemany(
        "INSERT INTO messages (session_id, timestamp, role, content) VALUES (?, ?, ?, ?)",
        messages,
    )
    conn.execute(
        "INSERT INTO topics (id, title, domain, tags) VALUES (1, 'Home', 'personal', 'home')"
    )
    for sid, (created, _) in statements.items():
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
            self.assertEqual(db.get_schema_version(conn), db.SCHEMA_VERSION)
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
            self.assertEqual(db.get_schema_version(conn), db.SCHEMA_VERSION)
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
        self.assertIn(f"version **12**, expected **{db.SCHEMA_VERSION}**", out)


class BackfillEdgeCaseTest(unittest.TestCase):
    """Each case is one statement; the expected session is in its fixture."""

    def migrate(self, messages, created, sessions=("sess-a", "sess-b", "sess-c")):
        support.reset_db()
        make_v13_db(messages, {1: (created, None)}, sessions)
        result, out = run_preflight()
        self.assertEqual(result, "migrated", out)
        with db.open_db() as conn:
            # Only statements: an orphaned message is old data the migration leaves as is.
            self.assertEqual(conn.execute("PRAGMA foreign_key_check(statements)").fetchall(), [])
            return conn.execute("SELECT session_id FROM statements WHERE id = 1").fetchone()[0]

    def test_a_marker_that_wrote_no_statements_is_ignored(self):
        # /remember mid-turn in sess-a; sess-b's extraction changed nothing.
        session = self.migrate([
            ("sess-a", "2026-05-01 10:00:00", "user", "/remember I prefer tabs"),
            ("sess-b", "2026-05-01 10:01:02", "system", "Extraction: knowledge=no changes, no tasks"),
        ], "2026-05-01 10:01:00")
        self.assertEqual(session, "sess-a")

    def test_markers_from_two_sessions_leave_the_statement_unassigned(self):
        # The mid-turn pass would pick sess-c, but the markers already say it
        # came from an extraction in sess-a or sess-b.
        session = self.migrate([
            ("sess-c", "2026-05-01 11:59:00", "user", "still thinking"),
            ("sess-a", "2026-05-01 12:00:00", "system", MARKER),
            ("sess-b", "2026-05-01 12:00:02", "system", MARKER),
        ], "2026-05-01 12:00:01")
        self.assertIsNone(session)

    def test_messages_of_a_missing_session_are_ignored(self):
        session = self.migrate([
            ("ghost", "2026-05-01 10:00:00", "system", MARKER),
        ], "2026-05-01 10:00:00", sessions=("sess-a",))
        self.assertIsNone(session)

    def test_timezone_aware_timestamps_are_compared_in_utc(self):
        session = self.migrate([
            ("sess-a", "2026-05-01 09:00:00", "user", "hi"),
            ("sess-b", "2026-05-01T12:00:00+02:00", "system", MARKER),
        ], "2026-05-01 10:00:01")
        self.assertEqual(session, "sess-b")


class MigrationLockTest(unittest.TestCase):
    def setUp(self):
        support.reset_db()
        make_v13_db()

    def backups(self):
        return sorted(glob.glob(db.DB_PATH + ".v13*.bak"))

    def test_a_session_that_finds_the_db_migrated_under_the_lock_backs_nothing_up(self):
        self.assertEqual(run_preflight()[0], "migrated")
        before = self.backups()
        real = preflight.get_schema_version
        reads = []

        def stale_first_read(conn):
            reads.append(1)
            return 13 if len(reads) == 1 else real(conn)

        with mock.patch.object(preflight, "get_schema_version", stale_first_read):
            result, out = run_preflight()
        self.assertEqual(result, "current")
        self.assertEqual(out, "")
        self.assertEqual(self.backups(), before)

    def test_a_held_lock_reports_busy_without_the_agent_prompt(self):
        real_get_db = db.get_db

        def quick_timeout():
            conn = real_get_db()
            conn.execute("PRAGMA busy_timeout=100")
            return conn

        db.get_db().close()  # switch to WAL first, as every real DB already is
        blocker = sqlite3.connect(db.DB_PATH)
        blocker.execute("BEGIN IMMEDIATE")
        try:
            with mock.patch.object(db, "get_db", quick_timeout):
                result, out = run_preflight()
        finally:
            blocker.rollback()
            blocker.close()
        self.assertEqual(result, "busy")
        self.assertEqual(out, "")
        self.assertEqual(self.backups(), [])
        with open(os.path.join(support.CLAUDE_DIR, "larvling.jsonl"), encoding="utf-8") as f:
            self.assertIn('"event":"schema_migration_busy"', f.read())
        self.assertEqual(run_preflight()[0], "migrated")

    def test_backup_names_are_taken_atomically_and_never_reused(self):
        stamp = "20260501-090000"
        taken = [db.DB_PATH + ".v13.bak", db.DB_PATH + f".v13.{stamp}.bak"]
        for path in taken:
            with open(path, "wb") as f:
                f.write(b"older")
        with mock.patch.object(preflight.time, "strftime", return_value=stamp):
            first = preflight._reserve_backup_path(13)
            second = preflight._reserve_backup_path(13)
        self.assertEqual(first, db.DB_PATH + f".v13.{stamp}-2.bak")
        self.assertEqual(second, db.DB_PATH + f".v13.{stamp}-3.bak")
        for path in taken:
            with open(path, "rb") as f:
                self.assertEqual(f.read(), b"older")


class ProcessKnowledgeTest(unittest.TestCase):
    SID = "sess-1"
    USER = "I prefer tabs over spaces. Actually, I use spaces in YAML."
    AGENT = "Tabs usually render at a width of 4 in most editors."

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
            "about": "user",
            "evidence": "I prefer tabs over spaces",
            "action": "add_topic",
        }
        item.update(overrides)
        return item

    def process(self, items, session_id=SID):
        return analyze.process_knowledge(self.conn, items, session_id, self.USER, self.AGENT)

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
        self.process([self.item()])
        topic_id = self.conn.execute("SELECT id FROM topics").fetchone()[0]
        self.process([
            self.item(action="add_statement", topic_id=topic_id,
                      claim="Tabs usually render at width 4", source="agent", kind="fact",
                      about="domain", evidence="Tabs usually render at a width of 4"),
        ])
        self.assertEqual(self.provenance(1), ("user", "preference", self.SID))
        self.assertEqual(self.provenance(2), ("agent", "fact", self.SID))

    def test_update_takes_the_updaters_provenance(self):
        self.process([self.item()])
        db.ensure_session(self.conn, "sess-2")
        self.process(
            [self.item(action="update_statement", statement_id=1,
                       claim="Uses tabs, but spaces in YAML", kind="decision",
                       evidence="I use spaces in YAML")],
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
        self.assertEqual(self.process(bad_items), (0, 0, 0, 0))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
