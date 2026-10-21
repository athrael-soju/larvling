"""Shared database helpers for Larvling hook scripts.

Schema: sessions, messages, topics, statements, tasks, updates
"""

import json
import os
import sqlite3
import sys
import time
from contextlib import contextmanager

def _find_project_root():
    """Discover the project root where .claude/larvling.db lives.

    Priority: CLAUDE_PROJECT_DIR env → walk up from cwd → cwd fallback.
    """
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root:
        return env_root

    # Walk up from cwd looking for .claude/larvling.db
    d = os.path.abspath(os.getcwd())
    for _ in range(20):  # safety bound
        if os.path.exists(os.path.join(d, ".claude", "larvling.db")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent

    return os.getcwd()


PROJECT_ROOT = _find_project_root()
DB_PATH = os.path.join(PROJECT_ROOT, ".claude", "larvling.db")


def get_plugin_version():
    """Read the plugin version from plugin.json. Returns '?' on failure."""
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    plugin_json = os.path.join(scripts_dir, "..", ".claude-plugin", "plugin.json")
    try:
        with open(plugin_json, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("version", "?") if isinstance(data, dict) else "?"
    except Exception:
        return "?"


def get_db():
    """Open a connection to larvling.db with WAL mode and Row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def open_db():
    """Context manager for database connections.

    Commits on clean exit, rolls back on exception, always closes.
    Callers may still call conn.commit() explicitly for intermediate
    checkpoints — the final commit on exit is a safe no-op in that case.
    """
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def parse_meta(metadata_str):
    """Parse a metadata JSON string. Returns dict (empty on failure)."""
    if not metadata_str:
        return {}
    try:
        return json.loads(metadata_str)
    except (json.JSONDecodeError, TypeError):
        return {}


def escape_like(query):
    """Escape special characters for SQL LIKE queries."""
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def reconfigure_stdout():
    """Reconfigure stdout for UTF-8 on Windows."""
    fn = getattr(sys.stdout, "reconfigure", None)
    if fn:
        fn(encoding="utf-8")


def require_db():
    """Exit with an error if the database doesn't exist."""
    if not os.path.exists(DB_PATH):
        print("No database found at", DB_PATH, file=sys.stderr)
        sys.exit(1)


def has_table(conn, name):
    """Check if a table exists in the database."""
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


# ---------------------------------------------------------------------------
# Schema creation and versioning
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 18


def get_schema_version(conn):
    """Read the current schema version from the database."""
    return conn.execute("PRAGMA user_version").fetchone()[0]


def set_schema_version(conn, version=SCHEMA_VERSION):
    """Set the schema version in the database."""
    conn.execute(f"PRAGMA user_version = {int(version)}")


# Tables FTS5 creates and manages for each index; never created by hand.
FTS_SHADOW_SUFFIXES = ("data", "idx", "content", "docsize", "config")


def get_current_schema(conn):
    """Read the live schema from sqlite_master, leaving out FTS5's own tables."""
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type IN ('table', 'index', 'view', 'trigger') AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    virtual = [r[0] for r in rows if r[1] and r[1].upper().startswith("CREATE VIRTUAL TABLE")]
    shadow = {f"{v}_{suffix}" for v in virtual for suffix in FTS_SHADOW_SUFFIXES}
    return "\n".join(r[1] + ";" for r in rows if r[1] and r[0] not in shadow)


def get_desired_schema():
    """Get the desired schema by creating it in an in-memory database."""
    mem = sqlite3.connect(":memory:")
    mem.row_factory = sqlite3.Row
    create_schema(mem)
    schema = get_current_schema(mem)
    mem.close()
    return schema


# An update entry touches its task, however it is written: by extraction, by
# /tidy, or by the agent keeping a drop candidate. tasks.updated is what task
# decay goes by.
TOUCH_TRIGGER = (
    "CREATE TRIGGER IF NOT EXISTS updates_touch_task AFTER INSERT ON updates BEGIN "
    "UPDATE tasks SET updated = new.timestamp WHERE id = new.task_id "
    "AND (julianday(updated) IS NULL OR julianday(new.timestamp) > julianday(updated)); END"
)


# Statements still in force. A statement leaves it when it is superseded
# (superseded_by names its replacement) or retired with no replacement.
CURRENT_STATEMENTS_VIEW = (
    "CREATE VIEW IF NOT EXISTS current_statements AS "
    "SELECT * FROM statements WHERE superseded_at IS NULL"
)


# Full-text index over statement claims (with their topic's title) and task
# titles, for ranked search and duplicate checks. Rowids are the statement and
# task ids. Porter stemming matches "deploy" with "deploying".
FTS_TABLES = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS statements_fts USING fts5("
    "claim, topic_title, tokenize = 'porter unicode61 remove_diacritics 2')",
    "CREATE VIRTUAL TABLE IF NOT EXISTS tasks_fts USING fts5("
    "title, tokenize = 'porter unicode61 remove_diacritics 2')",
)
_STATEMENT_ROW = (
    "INSERT INTO statements_fts (rowid, claim, topic_title) VALUES "
    "(new.id, new.claim, (SELECT title FROM topics WHERE id = new.topic_id));"
)
FTS_TRIGGERS = {
    "statements_fts_insert": "CREATE TRIGGER IF NOT EXISTS statements_fts_insert "
    f"AFTER INSERT ON statements BEGIN {_STATEMENT_ROW} END",
    "statements_fts_update": "CREATE TRIGGER IF NOT EXISTS statements_fts_update "
    "AFTER UPDATE OF claim, topic_id ON statements BEGIN "
    f"DELETE FROM statements_fts WHERE rowid = old.id; {_STATEMENT_ROW} END",
    "statements_fts_delete": "CREATE TRIGGER IF NOT EXISTS statements_fts_delete "
    "AFTER DELETE ON statements BEGIN DELETE FROM statements_fts WHERE rowid = old.id; END",
    "topics_fts_title": "CREATE TRIGGER IF NOT EXISTS topics_fts_title "
    "AFTER UPDATE OF title ON topics BEGIN UPDATE statements_fts SET topic_title = new.title "
    "WHERE rowid IN (SELECT id FROM statements WHERE topic_id = new.id); END",
    "tasks_fts_insert": "CREATE TRIGGER IF NOT EXISTS tasks_fts_insert "
    "AFTER INSERT ON tasks BEGIN INSERT INTO tasks_fts (rowid, title) VALUES (new.id, new.title); END",
    "tasks_fts_update": "CREATE TRIGGER IF NOT EXISTS tasks_fts_update "
    "AFTER UPDATE OF title ON tasks BEGIN DELETE FROM tasks_fts WHERE rowid = old.id; "
    "INSERT INTO tasks_fts (rowid, title) VALUES (new.id, new.title); END",
    "tasks_fts_delete": "CREATE TRIGGER IF NOT EXISTS tasks_fts_delete "
    "AFTER DELETE ON tasks BEGIN DELETE FROM tasks_fts WHERE rowid = old.id; END",
}

_fts5 = None


def fts5_available():
    """True if this Python's SQLite build has FTS5."""
    global _fts5
    if _fts5 is None:
        probe = sqlite3.connect(":memory:")
        try:
            probe.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
            _fts5 = True
        except sqlite3.OperationalError:
            _fts5 = False
        finally:
            probe.close()
    return _fts5


def _fts_triggers(conn):
    return {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
        if r[0] in FTS_TRIGGERS
    }


def fts_in_step(conn):
    """True if the search index matches what this SQLite build supports, and
    holds every statement and task (a migration done by hand creates it empty)."""
    if not fts5_available():
        return not _fts_triggers(conn)
    if not (
        has_table(conn, "statements_fts")
        and has_table(conn, "tasks_fts")
        and _fts_triggers(conn) == set(FTS_TRIGGERS)
    ):
        return False
    try:
        return conn.execute(
            "SELECT (SELECT COUNT(*) FROM statements_fts) = (SELECT COUNT(*) FROM statements) "
            "AND (SELECT COUNT(*) FROM tasks_fts) = (SELECT COUNT(*) FROM tasks)"
        ).fetchone()[0] == 1
    except sqlite3.OperationalError:
        return False


def ensure_fts(conn):
    """Bring the search index in step with this SQLite build.

    With FTS5: creates the index tables and their triggers where missing, and
    refills the index, since rows written without the triggers aren't in it.
    Without FTS5: drops the triggers, which would otherwise make every write
    to statements, topics and tasks fail; search falls back to LIKE.
    Returns True if the index is usable.
    """
    if fts_in_step(conn):
        return fts5_available()
    if not fts5_available():
        dropped = sorted(_fts_triggers(conn))
        for name in dropped:
            conn.execute(f"DROP TRIGGER {name}")
        if dropped:
            log("fts_triggers_dropped", reason="no FTS5 in this SQLite", triggers=len(dropped))
        return False
    for sql in FTS_TABLES:
        conn.execute(sql)
    for sql in FTS_TRIGGERS.values():
        conn.execute(sql)
    conn.execute("DELETE FROM statements_fts")
    conn.execute(
        "INSERT INTO statements_fts (rowid, claim, topic_title) "
        "SELECT s.id, s.claim, t.title FROM statements s LEFT JOIN topics t ON t.id = s.topic_id"
    )
    conn.execute("DELETE FROM tasks_fts")
    conn.execute("INSERT INTO tasks_fts (rowid, title) SELECT id, title FROM tasks")
    return True


def create_schema(conn):
    """Create all tables and indexes (idempotent)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
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
            summary_offered INTEGER DEFAULT 0,
            extracted_at TEXT,
            extracted_through INTEGER,
            extract_lease TEXT
        )
    """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            role TEXT NOT NULL,
            content TEXT,
            metadata TEXT
        )
    """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            domain TEXT NOT NULL CHECK(domain IN ('personal', 'professional', 'preferences', 'interests', 'knowledge', 'technical', 'workflow')),
            tags TEXT NOT NULL,
            created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS statements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER NOT NULL REFERENCES topics(id),
            claim TEXT NOT NULL,
            created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            source TEXT CHECK(source IN ('user', 'agent', 'doc', 'manual')),
            kind TEXT CHECK(kind IN ('fact', 'decision', 'preference', 'assessment')),
            session_id TEXT REFERENCES sessions(id),
            superseded_by INTEGER REFERENCES statements(id) ON DELETE SET NULL,
            superseded_at TEXT
        )
    """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            domain TEXT NOT NULL CHECK(domain IN ('personal', 'professional', 'preferences', 'interests', 'knowledge', 'technical', 'workflow')),
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'done', 'dropped')),
            priority TEXT NOT NULL DEFAULT 'medium' CHECK(priority IN ('low', 'medium', 'high')),
            horizon TEXT NOT NULL DEFAULT 'later' CHECK(horizon IN ('now', 'soon', 'later')),
            metadata TEXT,
            created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            due TEXT
        )
    """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL REFERENCES tasks(id),
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """
    )
    conn.execute(TOUCH_TRIGGER)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_session_role ON messages(session_id, role)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_statements_topic ON statements(topic_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_statements_session ON statements(session_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_statements_superseded_by ON statements(superseded_by)"
    )
    conn.execute(CURRENT_STATEMENTS_VIEW)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_updates_task ON updates(task_id)"
    )
    ensure_fts(conn)
    conn.commit()


# ---------------------------------------------------------------------------
# Session / Message / Summary CRUD
# ---------------------------------------------------------------------------


def ensure_session(conn, session_id):
    """Create or touch a session row.

    On first call creates the session. On subsequent calls (e.g. resume)
    updates ended_at so the session sorts to the top in session listings.
    """
    conn.execute(
        "INSERT INTO sessions (id, started_at) "
        "VALUES (?, datetime('now')) "
        "ON CONFLICT(id) DO UPDATE SET ended_at = datetime('now')",
        (session_id,),
    )


def record_message(conn, session_id, role, content, metadata=None):
    """Record a conversation turn in the messages table."""
    conn.execute(
        "INSERT INTO messages (session_id, role, content, metadata) "
        "VALUES (?, ?, ?, ?)",
        (session_id, role, content, json.dumps(metadata) if metadata else None),
    )


def record_summary(
    conn,
    session_id,
    title=None,
    agent_summary=None,
    exchange_count=None,
    summary_at=None,
    summary_msg_count=None,
):
    """Update summary fields on a session row.

    Only non-None values overwrite existing data (uses COALESCE).
    """
    conn.execute(
        """
        UPDATE sessions SET
            title = COALESCE(?, title),
            agent_summary = COALESCE(?, agent_summary),
            exchange_count = COALESCE(?, exchange_count),
            summary_at = COALESCE(?, summary_at),
            summary_msg_count = COALESCE(?, summary_msg_count)
        WHERE id = ?
        """,
        (title, agent_summary, exchange_count, summary_at, summary_msg_count, session_id),
    )


def finalize_session(conn, session_id):
    """Set ended_at and duration_min on a session."""
    conn.execute(
        """
        UPDATE sessions SET
            ended_at = datetime('now'),
            duration_min = ROUND(
                (julianday(datetime('now')) - julianday(started_at)) * 1440, 1
            )
        WHERE id = ?
        """,
        (session_id,),
    )


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def get_summary(conn, session_id):
    """Get the session row (which includes summary fields). Returns Row or None."""
    return conn.execute(
        "SELECT * FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()


def resolve_session(conn, short_id):
    """Resolve a short session ID to a full one."""
    if len(short_id) >= 36:
        return short_id
    row = conn.execute(
        "SELECT id FROM sessions WHERE id LIKE ? ESCAPE '\\'",
        (escape_like(short_id) + "%",),
    ).fetchone()
    return row[0] if row else None


def list_sessions(conn, show_summary_status=False):
    """List sessions with metadata. Prints formatted lines."""
    query = """
        SELECT s.id, s.started_at, s.duration_min, s.title,
               s.agent_summary, s.summary_msg_count,
               (SELECT COUNT(*) FROM messages m
                WHERE m.session_id = s.id
                AND m.role IN ('user', 'assistant')) AS current_msg_count
        FROM sessions s
        ORDER BY s.started_at DESC
    """ if show_summary_status else """
        SELECT id, started_at, duration_min, title, agent_summary
        FROM sessions
        ORDER BY started_at DESC
    """
    rows = conn.execute(query).fetchall()

    if not rows:
        print("No sessions found.")
        return

    for row in rows:
        short_id = row["id"][:8]
        date = (row["started_at"] or "?")[:16]
        duration = row["duration_min"]
        dur = f" ({duration}m)" if duration else ""
        title = row["title"] or ""
        if title:
            title = title.split("\n")[0]

        if show_summary_status:
            if row["agent_summary"]:
                summarized = row["summary_msg_count"] or 0
                current = row["current_msg_count"] or 0
                tag = f"  [summarized {summarized}/{current} msgs]"
            else:
                tag = "  [not summarized]"
            print(f"{short_id}  {date}{dur}{tag}  {title}")
        else:
            print(f"{short_id}  {date}{dur}  {title}")


def print_sessions(**kwargs):
    """Open DB, print session list, close. Passes kwargs to list_sessions."""
    with open_db() as conn:
        list_sessions(conn, **kwargs)






def fetch_session_tags(conn, session_id):
    """Fetch existing session tags for a session."""
    row = conn.execute(
        "SELECT tags FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    return row["tags"] or "" if row else ""



def log(event, session_id=None, **data):
    """Append a JSONL entry to .claude/larvling.jsonl for debugging."""
    try:
        log_path = os.path.join(PROJECT_ROOT, ".claude", "larvling.jsonl")
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event}
        if session_id:
            entry["sid"] = session_id[:8]
        entry.update(data)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except Exception:
        pass
