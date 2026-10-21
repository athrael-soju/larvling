"""Stop hook — logs the agent's last response, and what the user added while
the agent worked on it.

Extraction doesn't run here: it runs once per session (see analyze.py) over
the messages recorded here and by the prompt hook.
"""

from db import (
    open_db,
    ensure_session,
    record_message,
    log,
)
from health import record_failure
from hooks_util import read_hook_payload
from transcript import parse_last_turn, parse_user_turn, wait_for_transcript_stable


def handle(data):
    if data.get("stop_hook_active"):
        return  # Prevent recursive hook firing

    session_id = data.get("session_id")
    if not session_id:
        return

    transcript_path = data.get("transcript_path")

    wait_for_transcript_stable(transcript_path)

    response, tools = parse_last_turn(transcript_path)
    prompt, by_user, mid_turn = parse_user_turn(transcript_path)

    try:
        with open_db() as conn:
            ensure_session(conn, session_id)

            # Extraction takes 'user' messages as the user's own words. A
            # prompt Claude Code submitted for the user (a scheduled task, a
            # message from another session) is recorded as one, so relabel it.
            if prompt and not by_user:
                conn.execute(
                    "UPDATE messages SET role = 'system' WHERE id = ("
                    "SELECT MAX(id) FROM messages WHERE session_id = ? AND role = 'user'"
                    ") AND content = ?",
                    (session_id, prompt.strip()),
                )

            # A reply that is already the last one recorded means this turn
            # was recorded before.
            row = conn.execute(
                "SELECT content FROM messages "
                "WHERE session_id = ? AND role = 'assistant' "
                "ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            is_dup = bool(response and row and row[0] == response)

            if not is_dup:
                # Queued messages, AskUserQuestion answers and rejection
                # feedback, before the reply they came ahead of. Skip any the
                # prompt hook already recorded during this turn.
                recorded = {
                    r[0]
                    for r in conn.execute(
                        "SELECT content FROM messages WHERE session_id = ? AND role = 'user' "
                        "AND id > COALESCE((SELECT MAX(id) FROM messages "
                        "WHERE session_id = ? AND role = 'assistant'), 0)",
                        (session_id, session_id),
                    )
                }
                for text in mid_turn:
                    if text not in recorded:
                        record_message(conn, session_id, "user", text, {"mid_turn": True})
                if response:
                    meta = {"tool_calls": tools} if tools else None
                    record_message(conn, session_id, "assistant", response, meta)

            conn.commit()
    except Exception as e:
        # A failed write here is otherwise invisible (Claude Code swallows the
        # non-zero exit). Leave a marker so the next prompt warns the user.
        record_failure("the previous response", e)
        return

    # Log response details as JSONL
    resp_data = {"chars": len(response) if response else 0, "is_dup": is_dup}
    if mid_turn and not is_dup:
        resp_data["mid_turn"] = len(mid_turn)
    if tools:
        resp_data["tools"] = sum(tools.values())

    log("response", session_id, **resp_data)


if __name__ == "__main__":
    data = read_hook_payload()
    if data:
        handle(data)
