"""Task decay (#75): updated means last touched, due dates, and the
SessionStart rules that keep 'now' to work being done.
"""

import contextlib
import io
import json
import os
import sys
import unittest

import support

import analyze
import db
import preflight
from test_provenance import make_v13_db, run_preflight

sys.path.insert(0, os.path.join(support.SCRIPTS_DIR, "hooks"))
import session_start  # noqa: E402

SID = "sess-1"


def fresh_db():
    support.reset_db()
    with contextlib.redirect_stdout(io.StringIO()):
        preflight.ensure_schema()


def add_task(title, horizon="now", days_untouched=0, due_in=None, status="open"):
    """Insert a task last touched *days_untouched* ago, due *due_in* days from today."""
    with db.open_db() as conn:
        due = conn.execute("SELECT date('now', 'localtime', ?)", (f"{due_in:+d} days",)).fetchone()[0] \
            if due_in is not None else None
        return conn.execute(
            "INSERT INTO tasks (title, domain, horizon, status, due, created, updated) "
            "VALUES (?, 'technical', ?, ?, ?, datetime('now', ?), datetime('now', ?))",
            (title, horizon, status, due, f"-{days_untouched} days", f"-{days_untouched} days"),
        ).lastrowid


def task(task_id):
    with db.open_db() as conn:
        return dict(conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())


def notes(task_id):
    with db.open_db() as conn:
        return [r[0] for r in conn.execute(
            "SELECT content FROM updates WHERE task_id = ? ORDER BY id", (task_id,))]


def write_config(data):
    with open(os.path.join(support.CLAUDE_DIR, "larvling.config.json"), "w") as f:
        json.dump(data, f)


class TouchTest(unittest.TestCase):
    def setUp(self):
        fresh_db()
        self.id = add_task("Ship the exporter", days_untouched=20)

    def process(self, items):
        with db.open_db() as conn:
            return analyze.process_tasks(conn, items, SID)

    def untouched_days(self):
        with db.open_db() as conn:
            return conn.execute(
                "SELECT CAST(julianday('now') - julianday(updated) AS INTEGER) FROM tasks WHERE id = ?",
                (self.id,),
            ).fetchone()[0]

    def test_an_update_touches_the_task(self):
        self.process([{"action": "add_update", "task_id": self.id, "content": "Exporter PR is up."}])
        self.assertEqual(self.untouched_days(), 0)

    def test_a_reason_alone_touches_the_task(self):
        self.process([{"action": "update_task", "task_id": self.id, "content": "Still blocked on review."}])
        self.assertEqual(self.untouched_days(), 0)

    def test_due_dates_are_stored_and_checked(self):
        self.process([
            {"action": "add_task", "title": "Renew the TLS certificate", "domain": "technical",
             "priority": "high", "horizon": "soon", "due": "2026-11-03"},
            {"action": "add_task", "title": "Write the migration guide", "domain": "technical",
             "priority": "low", "horizon": "later", "due": "next Friday"},
            {"action": "update_task", "task_id": self.id, "due": "2026-10-30"},
        ])
        with db.open_db() as conn:
            dues = dict(conn.execute("SELECT title, due FROM tasks").fetchall())
        self.assertEqual(dues, {"Ship the exporter": "2026-10-30",
                                "Renew the TLS certificate": "2026-11-03"})
        self.assertEqual(support.logged("extraction_skipped")[0]["reason"], "invalid due")

    def test_the_prompt_dates_the_conversation(self):
        prompt = analyze.build_extraction_prompt([(1, "user", "hi")], SID, day="2026-10-21")
        self.assertIn("It took place on 2026-10-21.", prompt)


class DecayTest(unittest.TestCase):
    def setUp(self):
        fresh_db()
        self.fresh_now = add_task("Fix flaky test", days_untouched=2)
        self.stale_now = add_task("Upgrade Postgres", days_untouched=9)
        self.stale_now_due = add_task("File taxes", days_untouched=40, due_in=3)
        self.due_later = add_task("Book venue", horizon="later", days_untouched=60, due_in=5)
        self.overdue = add_task("Renew domain", horizon="soon", due_in=-1)
        self.due_far = add_task("Plan offsite", horizon="soon", due_in=30)
        self.stale_soon = add_task("Try Bun", horizon="soon", days_untouched=45)
        self.done = add_task("Old done work", days_untouched=90, status="done")

    def briefing(self):
        return session_start.get_session_context()

    def horizons(self):
        with db.open_db() as conn:
            return dict(conn.execute("SELECT id, horizon FROM tasks").fetchall())

    def test_now_only_holds_recent_or_due_soon_work(self):
        context = self.briefing()
        h = self.horizons()
        self.assertEqual(
            {i for i, hz in h.items() if hz == "now" and task(i)["status"] == "open"},
            {self.fresh_now, self.stale_now_due, self.due_later, self.overdue},
        )
        self.assertEqual(h[self.stale_now], "soon")
        self.assertEqual(h[self.due_far], "soon")
        self.assertEqual(h[self.done], "now")  # closed tasks are left alone
        self.assertIn("Moved automatically: 2 to now (due within 7 days); "
                      "1 from now to soon (untouched 7+ days).", context)

    def test_every_automatic_move_leaves_an_update(self):
        self.briefing()
        self.assertEqual(notes(self.stale_now),
                         ["Moved from now to soon automatically: untouched for 9 days."])
        self.assertEqual(len(notes(self.due_later)), 1)
        self.assertRegex(notes(self.due_later)[0], r"^Moved to now automatically: due \d{4}-\d\d-\d\d\.$")
        self.assertEqual(notes(self.fresh_now), [])
        self.assertEqual(notes(self.stale_now_due), [])  # already in now

    def test_a_move_is_not_a_touch_and_runs_once(self):
        self.briefing()
        self.briefing()
        self.assertEqual(len(notes(self.stale_now)), 1)
        self.assertGreaterEqual(
            db.get_db().execute(
                "SELECT julianday('now') - julianday(updated) FROM tasks WHERE id = ?",
                (self.stale_now,)).fetchone()[0],
            9,
        )

    def test_stale_soon_tasks_are_listed_but_not_dropped(self):
        context = self.briefing()
        self.assertIn("## Drop Candidates (1) - soon, untouched 30+ days", context)
        self.assertIn(f"- [{self.stale_soon}] Try Bun (45 days)", context)
        self.assertEqual(task(self.stale_soon)["status"], "open")

    def test_a_long_list_of_drop_candidates_shows_the_oldest(self):
        for n in range(12):
            add_task(f"Stale idea {n}", horizon="soon", days_untouched=100 + n)
        context = self.briefing()
        self.assertIn("## Drop Candidates (13)", context)
        self.assertIn("Stale idea 11 (111 days)", context)
        self.assertNotIn("Try Bun (45 days)", context)  # the youngest of 13
        self.assertIn("(3 more; query status='open' AND horizon='soon'", context)

    def test_due_dates_show_in_the_briefing(self):
        context = self.briefing()
        self.assertRegex(context, r"\] Book venue \(due \d{4}-\d\d-\d\d\)")

    def test_thresholds_come_from_config_and_zero_turns_a_rule_off(self):
        write_config({"decay_now_days": 0, "decay_soon_days": 50, "due_soon_days": 0})
        context = self.briefing()
        self.assertEqual(self.horizons()[self.stale_now], "now")
        self.assertEqual(self.horizons()[self.due_later], "later")
        self.assertNotIn("Drop Candidates", context)
        self.assertNotIn("Moved automatically", context)

    def test_task_tracking_off_leaves_tasks_alone(self):
        write_config({"task_tracking": False})
        self.briefing()
        self.assertEqual(self.horizons()[self.stale_now], "now")


class MigrationTest(unittest.TestCase):
    def test_upgrade_adds_due_and_backfills_last_touched(self):
        support.reset_db()
        make_v13_db()
        import sqlite3
        conn = sqlite3.connect(db.DB_PATH)
        conn.execute("INSERT INTO tasks (id, title, domain, created, updated) "
                     "VALUES (1, 'Old task', 'technical', '2026-01-01 00:00:00', '2026-01-01 00:00:00')")
        conn.execute("INSERT INTO tasks (id, title, domain, created, updated) "
                     "VALUES (2, 'Quiet task', 'technical', '2026-01-01 00:00:00', '2026-03-01 00:00:00')")
        conn.execute("INSERT INTO updates (task_id, content, timestamp) "
                     "VALUES (1, 'progress', '2026-05-01 10:00:00')")
        conn.execute("INSERT INTO updates (task_id, content, timestamp) "
                     "VALUES (2, 'older note', '2026-02-01 10:00:00')")
        conn.commit()
        conn.close()
        self.assertEqual(run_preflight()[0], "migrated")
        self.assertEqual(task(1)["updated"], "2026-05-01 10:00:00")
        self.assertEqual(task(2)["updated"], "2026-03-01 00:00:00")
        self.assertIsNone(task(1)["due"])


if __name__ == "__main__":
    unittest.main()
