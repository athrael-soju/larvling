"""
Larvling Preflight — schema bootstrap.
Ensures the database and schema exist before any other hooks run.
Upgrades listed in MIGRATIONS run automatically; any other version change
prints both schemas for the agent to migrate.
"""

import bisect
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

from db import (
    DB_PATH,
    SCHEMA_VERSION,
    open_db,
    log,
    reconfigure_stdout,
    create_schema,
    get_schema_version,
    set_schema_version,
    get_current_schema,
    get_desired_schema,
)


# ---------------------------------------------------------------------------
# Code-driven migrations
# ---------------------------------------------------------------------------

# analyze.py writes its "Extraction:" message in the same transaction as its
# statements, so both get the same timestamp.
_MARKER_WINDOW = timedelta(seconds=5)
# How long a turn can run when a statement is written during it (/remember).
_TURN_WINDOW = timedelta(minutes=10)


def _parse_ts(value):
    """Parse a stored timestamp as naive UTC, the way SQLite writes them."""
    try:
        ts = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts


def _load_messages(conn, where):
    """Return [(timestamp, session_id, role)] for matching messages, oldest first.

    Messages whose session row is missing are left out.
    """
    rows = []
    for r in conn.execute(
        "SELECT m.id, m.session_id, m.role, m.timestamp FROM messages m "
        f"JOIN sessions s ON s.id = m.session_id WHERE {where}"
    ):
        ts = _parse_ts(r["timestamp"])
        if ts:
            rows.append((ts, r["id"], r["session_id"], r["role"]))
    rows.sort()
    return [(ts, sid, role) for ts, _, sid, role in rows]


def _sole(values):
    """Return the only distinct value in *values*, or None if there isn't exactly one."""
    distinct = set(values)
    return distinct.pop() if len(distinct) == 1 else None


def backfill_statement_sessions(conn):
    """Fill in session_id on old statements from the message log.

    A statement gets the session of, in order:
    1. an "Extraction:" message that wrote statements, within _MARKER_WINDOW
       of its `created` time, or
    2. if there is no such message, an unanswered user message in the
       _TURN_WINDOW before it.
    If no session or more than one matches, session_id stays NULL.
    Returns the number of statements updated.
    """
    markers = _load_messages(
        conn, "m.role = 'system' AND m.content LIKE 'Extraction:%statements%'"
    )
    marker_times = [m[0] for m in markers]
    turns = _load_messages(conn, "m.role IN ('user', 'assistant')")
    turn_times = [t[0] for t in turns]

    attributed = 0
    for row in conn.execute(
        "SELECT id, created FROM statements WHERE session_id IS NULL"
    ).fetchall():
        created = _parse_ts(row["created"])
        if created is None:
            continue

        lo = bisect.bisect_left(marker_times, created - _MARKER_WINDOW)
        hi = bisect.bisect_right(marker_times, created + _MARKER_WINDOW)
        nearby = markers[lo:hi]
        sid = _sole(m[1] for m in nearby)

        if not nearby:
            lo = bisect.bisect_left(turn_times, created - _TURN_WINDOW)
            hi = bisect.bisect_right(turn_times, created)
            last_role = {}
            for _, s, role in turns[lo:hi]:
                last_role[s] = role
            sid = _sole(s for s, role in last_role.items() if role == "user")

        if sid:
            conn.execute(
                "UPDATE statements SET session_id = ? WHERE id = ?", (sid, row["id"])
            )
            attributed += 1
    return attributed


def migrate_v13_to_v14(conn):
    """v14: add source, kind and session_id to statements.

    Existing rows keep NULL source and kind; session_id is filled in where possible.
    """
    conn.execute(
        "ALTER TABLE statements ADD COLUMN "
        "source TEXT CHECK(source IN ('user', 'agent', 'doc', 'manual'))"
    )
    conn.execute(
        "ALTER TABLE statements ADD COLUMN "
        "kind TEXT CHECK(kind IN ('fact', 'decision', 'preference', 'assessment'))"
    )
    conn.execute(
        "ALTER TABLE statements ADD COLUMN session_id TEXT REFERENCES sessions(id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_statements_session ON statements(session_id)"
    )
    return {"statements_attributed": backfill_statement_sessions(conn)}


def migrate_v14_to_v15(conn):
    """v15: track per-session extraction on sessions.

    Earlier versions extracted after every exchange, so every message already
    recorded counts as extracted.
    """
    conn.execute("ALTER TABLE sessions ADD COLUMN extracted_at TEXT")
    conn.execute("ALTER TABLE sessions ADD COLUMN extracted_through INTEGER")
    conn.execute("ALTER TABLE sessions ADD COLUMN extract_lease TEXT")
    cur = conn.execute(
        """
        UPDATE sessions SET
            extracted_through = (
                SELECT MAX(m.id) FROM messages m
                WHERE m.session_id = sessions.id AND m.role IN ('user', 'assistant')
            ),
            extracted_at = datetime('now')
        WHERE EXISTS (
            SELECT 1 FROM messages m
            WHERE m.session_id = sessions.id AND m.role IN ('user', 'assistant')
        )
        """
    )
    return {"sessions_marked_extracted": cur.rowcount}


# Schema version -> the step that upgrades it by one. Don't change a step
# after release; add a new one.
MIGRATIONS = {
    13: migrate_v13_to_v14,
    14: migrate_v14_to_v15,
}


def run_migrations(conn, from_version):
    """Run the steps from *from_version* to SCHEMA_VERSION and commit.

    The caller holds the write lock (BEGIN IMMEDIATE). Returns the steps'
    stats, or None if a step is missing or fails; the transaction is then
    rolled back and the DB left unchanged.
    """
    versions = range(from_version, SCHEMA_VERSION)
    if not versions or any(v not in MIGRATIONS for v in versions):
        conn.rollback()
        return None
    try:
        stats = {}
        for v in versions:
            stats.update(MIGRATIONS[v](conn) or {})
        set_schema_version(conn)
        conn.commit()
        return stats
    except Exception as e:
        conn.rollback()
        log("schema_migration_error", from_version=from_version, error=str(e))
        return None


def _reserve_backup_path(db_version):
    """Create an empty backup file under a name no earlier backup uses.

    Tries larvling.db.v<N>.bak, then .v<N>.<stamp>.bak, .v<N>.<stamp>-2.bak, ...
    O_EXCL makes taking a name atomic, so two sessions never share one.
    """
    base = DB_PATH + f".v{db_version}"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    names = [f"{base}.bak", f"{base}.{stamp}.bak"]
    names += [f"{base}.{stamp}-{n}.bak" for n in range(2, 1000)]
    for path in names:
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:
            continue
        return path
    raise RuntimeError(f"no free backup name for {base}")


def backup_db(db_version):
    """Copy the DB before a migration, without overwriting an older backup.

    Uses its own connection: the caller holds the write lock, so the copy is
    the committed DB as it is before migrating. The backup API includes recent
    writes that a plain file copy can miss. Returns the backup path.
    """
    path = _reserve_backup_path(db_version)
    src = sqlite3.connect(DB_PATH)
    dest = sqlite3.connect(path)
    try:
        src.backup(dest)
    finally:
        dest.close()
        src.close()
    return path


def ensure_schema():
    """Ensure current schema exists.

    Returns:
        'fresh'    - first install, schema created
        'current'  - schema up to date
        'migrated' - upgraded automatically (see MIGRATIONS)
        'migrate'  - no automatic upgrade, migration context printed for Claude
        'busy'     - another session held the DB too long; try at the next start
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    with open_db() as conn:
        has_tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
        ).fetchone()

        if not has_tables:
            create_schema(conn)
            set_schema_version(conn)
            return "fresh"

        db_version = get_schema_version(conn)
        if db_version == SCHEMA_VERSION:
            return "current"

        # Hold the write lock from the backup to the end of the migration, so
        # no other session migrates in between.
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as e:
            log("schema_migration_busy", from_version=db_version, error=str(e))
            return "busy"
        db_version = get_schema_version(conn)
        if db_version == SCHEMA_VERSION:  # another session migrated first
            conn.rollback()
            return "current"

        try:
            backup_path = backup_db(db_version)
        except Exception:
            conn.rollback()
            raise
        stats = run_migrations(conn, db_version)
        if stats is not None:
            log(
                "schema_migrated",
                from_version=db_version,
                to_version=SCHEMA_VERSION,
                backup=backup_path,
                **stats,
            )
            return "migrated"

        # No automatic upgrade - dump both schemas for Claude to handle
        old_schema = get_current_schema(conn)
        new_schema = get_desired_schema()

    print("# Larvling - Schema Migration Required\n")
    print(
        f"Database schema is version **{db_version}**, expected **{SCHEMA_VERSION}**."
    )
    print(f"A backup has been saved to `{backup_path}`.\n")
    print("## Current Schema (in database)")
    print(f"```sql\n{old_schema}\n```\n")
    print("## Desired Schema")
    print(f"```sql\n{new_schema}\n```\n")
    safe_path = DB_PATH.replace("\\", "/")
    py = os.path.basename(sys.executable)
    print(
        "Please migrate the database at `"
        + safe_path
        + "` from the current schema to the desired schema."
    )
    print("Preserve all existing data. After migrating, run:")
    print(
        f"```bash\n{py} -c \"import sqlite3; c=sqlite3.connect('{safe_path}'); c.execute('PRAGMA user_version={SCHEMA_VERSION}'); c.close()\"\n```"
    )

    return "migrate"


def check_dependencies():
    """Check if required Python packages are installed.

    Auto-installs from requirements.txt if missing.
    Returns True if all dependencies are satisfied, False otherwise.
    """
    try:
        import claude_agent_sdk  # noqa: F401
        return True
    except ImportError:
        pass

    # Auto-install missing dependency
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    req_file = os.path.join(plugin_root, "requirements.txt") if plugin_root else ""

    if sys.version_info < (3, 10):
        print("# Larvling - Python Version Too Old\n")
        print(f"Running Python **{sys.version_info.major}.{sys.version_info.minor}**, but `claude-agent-sdk` requires **3.10+**.\n")
        print("Install Python 3.10+ and ensure it is on your PATH (e.g. `brew install python@3.11`).")
        print("Without it, knowledge extraction, session tags, and task tracking are disabled.")
        return False

    import subprocess
    try:
        cmd = [sys.executable, "-m", "pip", "install", "--quiet"]
        if req_file and os.path.isfile(req_file):
            cmd += ["-r", req_file]
        else:
            cmd += ["claude-agent-sdk"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, output=result.stdout, stderr=result.stderr)
    except subprocess.CalledProcessError as e:
        print("# Larvling - Missing Dependency\n")
        print("`claude-agent-sdk` could not be auto-installed. Install it manually:\n")
        print("```bash")
        print(f"{sys.executable} -m pip install claude-agent-sdk")
        print("```\n")
        if e.stderr:
            print(f"Error: {e.stderr.strip()}\n")
        print("Without it, knowledge extraction, session tags, and task tracking are disabled.")
        return False

    # Verify import after install
    try:
        import claude_agent_sdk  # noqa: F401
        return True
    except ImportError:
        print("# Larvling - Missing Dependency\n")
        print("`claude-agent-sdk` was installed but cannot be imported. Try manually:\n")
        print("```bash")
        print(f"{sys.executable} -m pip install claude-agent-sdk")
        print("```\n")
        return False


def main():
    if os.environ.get("LARVLING_INTERNAL"):
        return
    reconfigure_stdout()

    check_dependencies()

    result = ensure_schema()

    if result == "fresh":
        print("# Larvling - First Run\n")
        print("Database created at `.claude/larvling.db`.")


if __name__ == "__main__":
    main()
