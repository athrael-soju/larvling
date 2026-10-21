"""Task decay — keeps the 'now' list to work that is being done.

Runs in code at SessionStart, so it doesn't depend on the model:
- an open task due within due_soon_days moves to 'now', whatever its age;
- an open 'now' task untouched for decay_now_days moves to 'soon';
- an open 'soon' task untouched for decay_soon_days is a drop candidate,
  listed in the briefing but never dropped automatically.

"Touched" is tasks.updated, which extraction moves on every change or update.
Each automatic move leaves an update entry saying why, and doesn't count as a
touch itself.
"""

# A task is due soon when it is overdue or due within the window.
_DUE_SOON = "due IS NOT NULL AND due <= date('now', 'localtime', ?)"


def _days(n):
    return f"+{int(n)} days"


def _untouched(n):
    return f"-{int(n)} days"


def decay_tasks(conn, cfg):
    """Apply the decay rules. Returns (promoted, demoted) lists of task rows
    (id, title), for the briefing."""
    promoted = []
    if cfg["due_soon_days"]:
        promoted = conn.execute(
            f"SELECT id, title, due FROM tasks WHERE status = 'open' AND horizon != 'now' "
            f"AND {_DUE_SOON}",
            (_days(cfg["due_soon_days"]),),
        ).fetchall()
        for t in promoted:
            conn.execute("UPDATE tasks SET horizon = 'now' WHERE id = ?", (t["id"],))
            conn.execute(
                "INSERT INTO updates (task_id, content) VALUES (?, ?)",
                (t["id"], f"Moved to now automatically: due {t['due']}."),
            )

    demoted = []
    if cfg["decay_now_days"]:
        keep_due = f"AND NOT ({_DUE_SOON})" if cfg["due_soon_days"] else ""
        params = [_untouched(cfg["decay_now_days"])]
        if keep_due:
            params.append(_days(cfg["due_soon_days"]))
        demoted = conn.execute(
            "SELECT id, title, CAST(julianday('now') - julianday(updated) AS INTEGER) AS age "
            "FROM tasks WHERE status = 'open' AND horizon = 'now' "
            f"AND updated < datetime('now', ?) {keep_due}",
            params,
        ).fetchall()
        for t in demoted:
            conn.execute("UPDATE tasks SET horizon = 'soon' WHERE id = ?", (t["id"],))
            conn.execute(
                "INSERT INTO updates (task_id, content) VALUES (?, ?)",
                (t["id"], f"Moved from now to soon automatically: untouched for {t['age']} days."),
            )
    return promoted, demoted


def drop_candidates(conn, cfg):
    """Return open 'soon' tasks untouched for decay_soon_days, oldest first:
    rows of (id, title, age in days)."""
    if not cfg["decay_soon_days"]:
        return []
    return conn.execute(
        "SELECT id, title, CAST(julianday('now') - julianday(updated) AS INTEGER) AS age "
        "FROM tasks WHERE status = 'open' AND horizon = 'soon' "
        "AND updated < datetime('now', ?) ORDER BY updated",
        (_untouched(cfg["decay_soon_days"]),),
    ).fetchall()
