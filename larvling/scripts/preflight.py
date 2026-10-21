"""
Larvling Preflight — schema bootstrap.
Ensures the database and schema exist before any other hooks run.
Upgrades listed in MIGRATIONS run automatically; any other version change
prints both schemas for the agent to migrate.
"""

import bisect
import hashlib
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

from db import (
    DB_PATH,
    SCHEMA_VERSION,
    TOUCH_TRIGGER,
    open_db,
    log,
    reconfigure_stdout,
    create_schema,
    get_schema_version,
    set_schema_version,
    get_current_schema,
    get_desired_schema,
    CURRENT_STATEMENTS_VIEW,
    ensure_fts,
    fts5_available,
    fts_in_step,
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

    Earlier versions extracted after every exchange, so messages already
    recorded count as extracted. A session active in the last day may have
    been recorded by this version while the DB waited for its migration (a
    busy start): there, only the exchanges up to the reply before its last
    "Extraction:" message count (that message can land after the next prompt),
    and catch-up extracts the rest.
    """
    conn.execute("ALTER TABLE sessions ADD COLUMN extracted_at TEXT")
    conn.execute("ALTER TABLE sessions ADD COLUMN extracted_through INTEGER")
    conn.execute("ALTER TABLE sessions ADD COLUMN extract_lease TEXT")
    cur = conn.execute(MARK_EXTRACTED)
    return {"sessions_marked_extracted": cur.rowcount}


MARK_EXTRACTED = """
UPDATE sessions SET
    extracted_through = (
        SELECT MAX(m.id) FROM messages m
        WHERE m.session_id = sessions.id AND m.role IN ('user', 'assistant')
        AND (
            (SELECT MAX(timestamp) FROM messages WHERE session_id = sessions.id)
                < datetime('now', '-1 day')
            OR m.id <= (
                SELECT MAX(r.id) FROM messages r
                WHERE r.session_id = sessions.id AND r.role = 'assistant' AND r.id < (
                    SELECT MAX(id) FROM messages
                    WHERE session_id = sessions.id AND role = 'system'
                    AND content LIKE 'Extraction:%'
                )
            )
        )
    ),
    extracted_at = datetime('now')
WHERE extracted_through IS NULL AND EXISTS (
    SELECT 1 FROM messages m
    WHERE m.session_id = sessions.id AND m.role IN ('user', 'assistant')
)
""".strip()

# Schema version -> data an upgrade to it carries over, for a migration done
# by hand. The automatic steps above run the same SQL.
DATA_STEPS = {
    15: ("Earlier versions extracted after every exchange, so count every "
         "message already recorded as extracted", MARK_EXTRACTED),
}


def migrate_v15_to_v16(conn):
    """v16: statements are superseded instead of rewritten; current_statements
    lists the ones still in force."""
    conn.execute(
        "ALTER TABLE statements ADD COLUMN "
        "superseded_by INTEGER REFERENCES statements(id) ON DELETE SET NULL"
    )
    conn.execute("ALTER TABLE statements ADD COLUMN superseded_at TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_statements_superseded_by ON statements(superseded_by)"
    )
    conn.execute(CURRENT_STATEMENTS_VIEW)


def migrate_v16_to_v17(conn):
    """v17: full-text index over statements and tasks, filled from existing rows.

    Without FTS5 in this SQLite build the index is left out; ensure_fts adds it
    at a later start whose SQLite has FTS5.
    """
    return {"fts5": ensure_fts(conn)}


def migrate_v17_to_v18(conn):
    """v18: tasks.due, and tasks.updated as the last time a task was touched,
    which a trigger moves on every update entry.

    Earlier versions didn't move updated when an update was added, so it is
    brought up to each task's latest update.
    """
    conn.execute("ALTER TABLE tasks ADD COLUMN due TEXT")
    conn.execute(TOUCH_TRIGGER)
    cur = conn.execute(
        """
        UPDATE tasks SET updated = (
            SELECT MAX(u.timestamp) FROM updates u WHERE u.task_id = tasks.id
        )
        WHERE (SELECT MAX(u.timestamp) FROM updates u WHERE u.task_id = tasks.id) > updated
        """
    )
    return {"tasks_touch_backfilled": cur.rowcount}


# Schema version -> the step that upgrades it by one. Don't change a step
# after release; add a new one.
MIGRATIONS = {
    13: migrate_v13_to_v14,
    14: migrate_v14_to_v15,
    15: migrate_v15_to_v16,
    16: migrate_v16_to_v17,
    17: migrate_v17_to_v18,
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


def _keep_fts_in_step(conn):
    """Build or drop the search index if this Python's SQLite differs from the
    one that last set it up (see db.ensure_fts)."""
    if fts_in_step(conn):
        return
    try:
        conn.execute("BEGIN IMMEDIATE")
        ensure_fts(conn)
        conn.commit()
    except sqlite3.OperationalError as e:
        conn.rollback()
        log("fts_sync_error", error=str(e))


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
            _keep_fts_in_step(conn)
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
    steps = [DATA_STEPS[v] for v in sorted(DATA_STEPS) if v > db_version]
    if steps:
        print("Preserve all existing data, and once the new columns exist:")
        for why, sql in steps:
            print(f"- {why}:\n```sql\n{sql};\n```")
        print("After migrating, run:")
    else:
        print("Preserve all existing data. After migrating, run:")
    print(
        f"```bash\n{py} -c \"import sqlite3; c=sqlite3.connect('{safe_path}'); c.execute('PRAGMA user_version={SCHEMA_VERSION}'); c.close()\"\n```"
    )

    return "migrate"


MIN_SDK_VERSION = "0.2.170"
SDK_INSTALL_LOG = os.path.join(
    os.path.expanduser("~"), ".claude",
    "larvling-sdk-%s.log" % hashlib.sha1(sys.executable.encode()).hexdigest()[:12],
)
SDK_INSTALL_STALE_SECONDS = 1800
_PIP_WRAPPER = (
    "import subprocess, sys\n"
    "with open(sys.argv[1], 'a') as log:\n"
    "    code = subprocess.run(sys.argv[2:], stdout=log, stderr=log).returncode\n"
    "    log.write(f'\\nexit {code}\\n')\n"
)


def sdk_outdated(version):
    def parts(v):
        return [int(n) for n in re.findall(r"\d+", v)[:3]]
    return parts(version) < parts(MIN_SDK_VERSION)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _install_sdk_in_background(spec):
    import subprocess

    new = f"{SDK_INSTALL_LOG}.{os.getpid()}.new"
    try:
        os.makedirs(os.path.dirname(SDK_INSTALL_LOG), exist_ok=True)
        with open(os.open(new, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600), "w") as f:
            f.write(f"started by {os.getpid()} at {time.time()}\n")
        os.link(new, SDK_INSTALL_LOG)
    except OSError:
        return
    finally:
        _remove(new)
    try:
        subprocess.Popen(
            [sys.executable, "-c", _PIP_WRAPPER, SDK_INSTALL_LOG,
             sys.executable, "-m", "pip", "install", "--quiet", spec],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=(os.name != "nt"),
        )
    except OSError:
        _remove(SDK_INSTALL_LOG)


def _claim_retry(previous):
    claimed = f"{SDK_INSTALL_LOG}.{os.getpid()}"
    try:
        os.rename(SDK_INSTALL_LOG, claimed)
    except OSError:
        return False
    if _read(claimed) != previous:
        try:
            os.rename(claimed, SDK_INSTALL_LOG)
        except OSError:
            pass
        return False
    _remove(claimed)
    return True


def check_dependencies():
    """Return True if claude-agent-sdk is ready; otherwise install it in the background."""
    try:
        import claude_agent_sdk
        installed = getattr(claude_agent_sdk, "__version__", "")
    except Exception:
        installed = None
    if installed and not sdk_outdated(installed):
        _remove(SDK_INSTALL_LOG)
        return True

    if sys.version_info < (3, 10):
        print("# Larvling - Python Version Too Old\n")
        print(f"Running Python **{sys.version_info.major}.{sys.version_info.minor}**, but `claude-agent-sdk` requires **3.10+**.\n")
        print("Install Python 3.10+ and ensure it is on your PATH (e.g. `brew install python@3.11`).")
        print("Without it, knowledge extraction, session tags, and task tracking are disabled.")
        return False

    spec = f"claude-agent-sdk>={MIN_SDK_VERSION}"
    state = f"{installed}, older than {MIN_SDK_VERSION}" if installed else "missing"
    previous = _read(SDK_INSTALL_LOG)
    try:
        fresh = time.time() - os.path.getmtime(SDK_INSTALL_LOG) < SDK_INSTALL_STALE_SECONDS
    except OSError:
        fresh = False
    running = previous is not None and "\nexit " not in previous and fresh

    if running:
        print("# Larvling - Installing Dependency\n")
        print(f"`claude-agent-sdk` is {state} and is being installed in the background. "
              "Knowledge extraction, session tags, and task tracking resume once it is done.")
        return False
    if previous is not None:
        print("# Larvling - Missing Dependency\n")
        print(f"`claude-agent-sdk` is {state}, and installing it in the background failed. "
              "Larvling is trying again; to install it yourself, run:\n")
        print("```bash")
        print(f"{sys.executable} -m pip install --upgrade '{spec}'")
        print("```\n")
        tail = previous.strip().splitlines()[-6:]
        if tail:
            print("pip's last output:\n```\n" + "\n".join(tail) + "\n```\n")
        if not _claim_retry(previous):
            return False
    else:
        print("# Larvling - Installing Dependency\n")
        print(f"`claude-agent-sdk` is {state}; installing it in the background. "
              "Knowledge extraction, session tags, and task tracking start once it is done.")
    _install_sdk_in_background(spec)
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
    if result in ("fresh", "migrated") and not fts5_available():
        log("fts5_unavailable")
        print(
            "Larvling: this Python's SQLite has no FTS5, so knowledge search uses "
            "plain LIKE matching, which ranks by how many words match rather than by relevance."
        )


if __name__ == "__main__":
    main()
