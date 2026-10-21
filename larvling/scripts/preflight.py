"""
Larvling Preflight — schema bootstrap.
Ensures the database and schema exist before any other hooks run.

Upgrades with a registered step in MIGRATIONS run here, in code, inside one
transaction. Anything else (no registered path, a failed step, a downgrade)
falls back to printing both schemas for the agent to migrate.
"""

import bisect
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta

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

# analyze.py records its "Extraction: ..." system message in the same
# transaction as the statements it inserts, so both share a timestamp.
_MARKER_WINDOW = timedelta(seconds=5)
# A statement written mid-turn (/remember, ad-hoc SQL) lands after the user's
# prompt is recorded and before the reply is. Bound how long that turn can run.
_TURN_WINDOW = timedelta(minutes=10)


def _parse_ts(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _load_messages(conn, where):
    """Return [(timestamp, session_id, role)] for matching messages, oldest first."""
    rows = []
    for r in conn.execute(
        f"SELECT id, session_id, role, timestamp FROM messages WHERE {where}"
    ):
        ts = _parse_ts(r["timestamp"])
        if ts:
            rows.append((ts, r["id"], r["session_id"], r["role"]))
    rows.sort()
    return [(ts, sid, role) for ts, _, sid, role in rows]


def _sole(values):
    """The single distinct value in *values*, or None if there are 0 or 2+."""
    distinct = set(values)
    return distinct.pop() if len(distinct) == 1 else None


def backfill_statement_sessions(conn):
    """Attribute legacy statements to the session that wrote them.

    Each statement gets a session only when exactly one candidate fits:
    1. Extraction writes: the session of the "Extraction:" system message
       recorded within _MARKER_WINDOW of the statement's `created`.
    2. Mid-turn writes: the session whose latest user/assistant message before
       `created` is a user prompt no older than _TURN_WINDOW (a turn in flight).
    Anything else stays NULL. Returns the number of statements attributed.
    """
    markers = _load_messages(conn, "role = 'system' AND content LIKE 'Extraction:%'")
    marker_times = [m[0] for m in markers]
    turns = _load_messages(conn, "role IN ('user', 'assistant')")
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
        sid = _sole(m[1] for m in markers[lo:hi])

        if sid is None:
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
    """v14: provenance on statements (source, kind, session_id).

    source/kind stay NULL on legacy rows rather than guessing; session_id is
    backfilled where the message log pins it down.
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


# from_version -> step that upgrades it to from_version + 1. Steps are frozen
# snapshots: never edit one after release, add a new step instead.
MIGRATIONS = {
    13: migrate_v13_to_v14,
}


def run_migrations(conn, from_version):
    """Upgrade *from_version* to SCHEMA_VERSION in code, in one transaction.

    Returns a dict of step stats on success, or None when there is no
    registered path or a step failed (the DB is left as it was).
    """
    versions = range(from_version, SCHEMA_VERSION)
    if not versions or any(v not in MIGRATIONS for v in versions):
        return None
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = get_schema_version(conn)
        if current != from_version:  # a concurrent session migrated first
            conn.rollback()
            return {} if current == SCHEMA_VERSION else None
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


def backup_db(conn, db_version):
    """Snapshot the DB before a migration. Never overwrites an earlier backup.

    Uses the SQLite backup API so pages still in the WAL are included.
    """
    path = DB_PATH + f".v{db_version}.bak"
    if os.path.exists(path):
        path = DB_PATH + f".v{db_version}.{time.strftime('%Y%m%d-%H%M%S')}.bak"
    dest = sqlite3.connect(path)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    return path


def ensure_schema():
    """Ensure current schema exists.

    Returns:
        'fresh'    - first install, schema created
        'current'  - schema up to date
        'migrated' - upgraded in code via MIGRATIONS
        'migrate'  - no code path, migration context printed for Claude
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

        backup_path = backup_db(conn, db_version)
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

        # No code path - dump both schemas for Claude to handle
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
