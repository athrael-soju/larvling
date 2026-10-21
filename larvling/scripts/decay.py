"""Task decay — keeps the 'now' list to work that is being done.

Runs in code at SessionStart, so it doesn't depend on the model:
- an open task due within due_soon_days moves to 'now', whatever its age,
  once per due date (recorded as due_promoted in tasks.metadata, also for a
  task that was already in 'now'), so a user who moves it out is listened to;
- an open 'now' task untouched for decay_now_days moves to 'soon';
- an open 'soon' task untouched for decay_soon_days is a drop candidate,
  listed in the briefing but never dropped automatically.

"Touched" is tasks.updated, which every change and every update entry moves
(see db.TOUCH_TRIGGER). Each automatic move leaves an update entry saying
why, and doesn't count as a touch itself.
"""

# A task is due soon when it is overdue or due within the window. A due value
# that isn't a YYYY-MM-DD date (written by hand) is ignored.
_DUE_SOON = (
    "due GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' "
    "AND due <= date('now', 'localtime', ?)"
)
_UNTOUCHED = "julianday(updated) < julianday('now', ?)"
_VALID_META = "json_valid(COALESCE(metadata, '{}'))"
# Not yet moved for this due date. Malformed metadata can't hold the mark.
_NOT_PROMOTED = (
    f"(NOT {_VALID_META} OR json_extract(COALESCE(metadata, '{{}}'), '$.due_promoted') IS NOT due)"
)
_AGE = "CAST(julianday('now') - julianday(updated) AS INTEGER) AS age"


def _days(n):
    return f"+{int(n)} days"


def _untouched(n):
    return f"-{int(n)} days"


def _promoted_note(due):
    return f"Moved to now automatically: due {due}."


def _move(conn, task, horizon, guard, note):
    """Move *task* to *horizon* if *guard* still holds, with an update entry
    that doesn't count as a touch. Returns True if it moved."""
    cur = conn.execute(
        f"UPDATE tasks SET horizon = ? WHERE id = ? AND status = 'open' AND {guard}",
        (horizon, task["id"]),
    )
    if cur.rowcount != 1:
        return False  # another session moved it first
    conn.execute("INSERT INTO updates (task_id, content) VALUES (?, ?)", (task["id"], note))
    conn.execute("UPDATE tasks SET updated = ? WHERE id = ?", (task["updated"], task["id"]))
    return True


def decay_tasks(conn, cfg):
    """Apply the decay rules. Returns (promoted, demoted) lists of task rows
    (id, title), for the briefing. Takes the write lock first, so two
    sessions starting at once don't both move a task."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")

    promoted = []
    if cfg["due_soon_days"]:
        rows = conn.execute(
            "SELECT id, title, due, updated, horizon FROM tasks WHERE status = 'open' "
            f"AND {_DUE_SOON} AND {_NOT_PROMOTED}",
            (_days(cfg["due_soon_days"]),),
        ).fetchall()
        for t in rows:
            if t["horizon"] != "now" and _move(
                conn, t, "now", "horizon != 'now'", _promoted_note(t["due"])
            ):
                promoted.append(t)
            conn.execute(
                "UPDATE tasks SET metadata = json_set(COALESCE(metadata, '{}'), "
                f"'$.due_promoted', due) WHERE id = ? AND {_VALID_META}",
                (t["id"],),
            )

    demoted = []
    if cfg["decay_now_days"]:
        keep_due = f"AND NOT COALESCE({_DUE_SOON}, 0)" if cfg["due_soon_days"] else ""
        params = [_untouched(cfg["decay_now_days"])]
        if keep_due:
            params.append(_days(cfg["due_soon_days"]))
        rows = conn.execute(
            f"SELECT id, title, updated, {_AGE} FROM tasks "
            f"WHERE status = 'open' AND horizon = 'now' AND {_UNTOUCHED} {keep_due}",
            params,
        ).fetchall()
        demoted = [
            t for t in rows if _move(
                conn, t, "soon", "horizon = 'now'",
                f"Moved from now to soon automatically: untouched for {t['age']} days.",
            )
        ]
    return promoted, demoted


def drop_candidates(conn, cfg):
    """Return open 'soon' tasks untouched for decay_soon_days, oldest first:
    rows of (id, title, age in days)."""
    if not cfg["decay_soon_days"]:
        return []
    return conn.execute(
        f"SELECT id, title, {_AGE} FROM tasks "
        f"WHERE status = 'open' AND horizon = 'soon' AND {_UNTOUCHED} "
        "ORDER BY julianday(updated)",
        (_untouched(cfg["decay_soon_days"]),),
    ).fetchall()
