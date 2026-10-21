"""SessionEnd hook — finalizes session timing and records exchange count."""

from db import (
    open_db,
    ensure_session,
    finalize_session,
    record_summary,
    log,
)
from hooks_util import read_hook_payload


def handle(data):
    session_id = data.get("session_id")
    if not session_id:
        return

    with open_db() as conn:
        # Check if any messages were recorded for this session.
        # If not, it's a ghost session (started but no real exchange happened).
        msg_count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]

        if msg_count == 0:
            log("session_end", session_id, ghost=True)
            return

        ensure_session(conn, session_id)
        finalize_session(conn, session_id)

        # Words the user added mid-turn are recorded as user messages too, but
        # they don't start an exchange.
        exchange_count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'user' "
            "AND NOT COALESCE(CASE WHEN json_valid(metadata) "
            "THEN json_extract(metadata, '$.mid_turn') END, 0)",
            (session_id,),
        ).fetchone()[0]

        record_summary(
            conn,
            session_id,
            exchange_count=exchange_count or None,
        )
        conn.commit()

        row = conn.execute(
            "SELECT duration_min FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        dur = round(row["duration_min"], 1) if row and row["duration_min"] else None

        # Reclaim WAL/SHM space at end of session
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        log("session_end", session_id, exchanges=exchange_count, duration=dur)


if __name__ == "__main__":
    data = read_hook_payload()
    if data:
        handle(data)
