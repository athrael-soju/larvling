"""UserPromptSubmit hook — logs the user's prompt and injects context hints."""

import json
import os
import re
import sys

from config import get_config
from db import (
    open_db,
    has_table,
    ensure_session,
    record_message,
    record_summary,
    log,
    PROJECT_ROOT,
)
from health import record_failure, pending_failure, clear_failure
from hooks_util import read_hook_payload
from transcript import strip_ide_tags


def _last_context_counts():
    """Read the last context event from the JSONL log and return (topics, stmts)."""
    try:
        log_path = os.path.join(PROJECT_ROOT, ".claude", "larvling.jsonl")
        last = None
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if entry.get("event") == "context" and entry.get("injected"):
                    last = entry
        if last:
            for item in last["injected"]:
                m = re.match(r"(\d+)(?:\s*\([^)]*\))?\s*topics,\s*(\d+)(?:\s*\([^)]*\))?\s*statements", item)
                if m:
                    return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return None, None


def _fmt_delta(current, previous):
    """Format a count with optional delta suffix, e.g. '17 (+2)'."""
    if previous is None or current == previous:
        return str(current)
    diff = current - previous
    sign = "+" if diff > 0 else ""
    return f"{current} ({sign}{diff})"


def inject_context(conn, session_id):
    """Print context hints (knowledge lookup, summary staleness) for the agent."""
    cfg = get_config()
    injected = []

    if cfg["context_hints"] and has_table(conn, "topics"):
        topic_count = conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
        stmt_count = (
            conn.execute("SELECT COUNT(*) FROM current_statements").fetchone()[0]
            if has_table(conn, "statements")
            else 0
        )
        scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        query_script = os.path.normpath(os.path.join(scripts_dir, "query.py")).replace(
            "\\", "/"
        )
        py = os.path.basename(sys.executable)
        text = (
            f"\n## Knowledge Context\n"
            f"{topic_count} topic(s), {stmt_count} statement(s). "
            f'query: {py} "{query_script}" "<SQL>"\n'
            "When the answer depends on stored knowledge not already in context, "
            "retrieve it and weave it in. Prefer `/recall <term>`; use raw SQL only "
            "for aggregates/joins it can't do. SQL hits large tables (current_statements, "
            "tasks, messages) — so select only the columns you need (`substr(claim,1,160)` "
            "for long text) and let the question itself size the result: filter narrowly, "
            "and add a `LIMIT` only when the answer would otherwise be excessive, not by "
            "reflex. Run one query then read it before widening. Use `COUNT`/`GROUP BY` for overviews, "
            "and let the question's scope drive the `WHERE` (e.g. 'today' → `horizon='now'`). "
            "Table mode does not truncate: a result over ~16KB is refused with an error "
            "to re-scope (add WHERE/GROUP BY/LIMIT) or pass `--full`."
        )
        print(text)

        prev_topics, prev_stmts = _last_context_counts()
        t_str = _fmt_delta(topic_count, prev_topics)
        s_str = _fmt_delta(stmt_count, prev_stmts)
        injected.append(f"{t_str} topics, {s_str} statements")

    if not cfg["summary_hints"]:
        if injected:
            log("context", session_id, injected=injected)
        return

    session = conn.execute(
        "SELECT summary_msg_count, agent_summary, summary_offered FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if session:
        msg_count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ? "
            "AND role IN ('user', 'assistant')",
            (session_id,),
        ).fetchone()[0]
        summarized = session["summary_msg_count"] or 0
        already_offered = session["summary_offered"] or 0

        if not already_offered and not session["agent_summary"] and msg_count >= 10:
            text = f"\n## Summary\nNo summary yet ({msg_count} messages)."
            print(text)
            conn.execute(
                "UPDATE sessions SET summary_offered = 1 WHERE id = ?",
                (session_id,),
            )
            conn.commit()
            injected.append("summary hint")
        elif not already_offered and session["agent_summary"] and msg_count > summarized + 4:
            text = (
                f"\n## Summary\nStale summary "
                f"(covers {summarized}/{msg_count} messages)."
            )
            print(text)
            conn.execute(
                "UPDATE sessions SET summary_offered = 1 WHERE id = ?",
                (session_id,),
            )
            conn.commit()
            injected.append("stale summary hint")

    if injected:
        log("context", session_id, injected=injected)


def handle(data):
    session_id = data.get("session_id")
    if not session_id:
        return

    prompt = strip_ide_tags(data.get("prompt", ""))
    if not prompt:
        return

    meta = {
        "cwd": data.get("cwd"),
        "permission_mode": data.get("permission_mode"),
    }

    # System-injected messages (e.g. task-notification) arrive through the
    # UserPromptSubmit hook but are not actual user input.
    role = "system" if prompt.startswith("<task-notification>") else "user"

    try:
        with open_db() as conn:
            # Critical path: persist the message. A failure here is otherwise
            # invisible (Claude Code swallows the non-zero exit), so mark it.
            try:
                ensure_session(conn, session_id)
                record_message(conn, session_id, role, prompt, meta)

                count = 0
                if role == "user":
                    count = conn.execute(
                        "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'user'",
                        (session_id,),
                    ).fetchone()[0]
                    if count == 1:
                        record_summary(conn, session_id, title=prompt)

                conn.commit()
            except Exception as e:
                record_failure("your last message", e)
                return

            # Bookkeeping past the commit (skills, context, surfacing) is
            # non-critical — its errors must not read as a recording failure.
            try:
                _record_body(conn, session_id, role, prompt, count)
            except Exception:
                pass
    except Exception as e:
        # open_db itself failed — the DB is unreachable; recording did not happen.
        record_failure("your last message", e)


def _record_body(conn, session_id, role, prompt, count):
    """Post-write bookkeeping: skill detection, context injection, and surfacing
    any pending recording failure from an earlier turn."""
    if role != "user":
        log("system-passthrough", session_id)
        return

    # Detect skill/command invocations:
    # - Raw slash command: "/status", "/recall"
    # - XML tags: <command-message>plugin:skill</command-message>
    cmd_match = re.search(
        r"<command-(?:message|name)>\s*/?(.+?)\s*</command-(?:message|name)>",
        prompt,
    )
    if not cmd_match and re.fullmatch(r"/[\w:/-]+", prompt.strip()):
        cmd_match = re.fullmatch(r"/([\w:/-]+)", prompt.strip())
    if cmd_match:
        log("skill", session_id, name=f"/{cmd_match.group(1)}")
    else:
        log("prompt", session_id, n=count)

    # Surface a recording failure from an earlier turn (near-real-time: the Stop
    # hook that failed left a marker; this is the user's very next message).
    _surface_pending_failure()

    try:
        inject_context(conn, session_id)
    except Exception:
        pass  # Context injection is non-critical


def _surface_pending_failure():
    """If an earlier write failed, warn the user once, then clear the marker."""
    marker = pending_failure()
    if not marker:
        return
    clear_failure()
    stage = marker.get("stage", "a recent exchange")
    err = marker.get("error", "")
    detail = f" ({err})" if err else ""
    print(
        f"\n## ⚠️ Larvling Recording Issue\n"
        f"Larvling failed to record {stage}{detail}. That exchange may not have "
        f"been saved. Tell the user now so they can check the plugin (a broken "
        f"plugin cache or DB error is the usual cause)."
    )


if __name__ == "__main__":
    data = read_hook_payload()
    if data:
        handle(data)
