"""Transcript parsing (#71): which entries start a turn, and which hold the
user's own words.
"""

import contextlib
import io
import json
import os
import sys
import time
import unittest

import support

import db
import preflight
import transcript

sys.path.insert(0, os.path.join(support.SCRIPTS_DIR, "hooks"))
import stop  # noqa: E402

SID = "sess-1"
PROMPT = "I'm a data engineer at a bank. What do we know about Airflow?"


def user(content, **flags):
    return {"type": "user", "message": {"role": "user", "content": content}, **flags}


def reply(text):
    return {"type": "assistant", "message": {
        "role": "assistant", "content": [{"type": "text", "text": text}]}}


def tool_call(tool_id, name):
    return {"type": "assistant", "message": {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": {}}]}}


def tool_result(tool_id):
    return user([{"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}])


# A subagent's result, delivered to the main agent as a new turn.
TASK_NOTIFICATION = user(
    "<task-notification>\n<status>completed</status>\n"
    "<result>The user's team is three people.</result>\n</task-notification>"
)
# The Skill tool loading a skill body into the running turn.
SKILL_LOAD = [
    tool_call("t1", "Skill"),
    tool_result("t1"),
    user([{"type": "text", "text": "Base directory for this skill: /x\n\nSearch stored knowledge."}],
         isMeta=True),
]
# A scheduled task firing: a meta entry Claude Code submits as a prompt.
SCHEDULED_PROMPT = user("Check the deploy status.", isMeta=True, promptSource="system")


def write(path, entries, mode="w"):
    with open(path, mode, encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def new_path():
    return os.path.join(support.PROJECT_DIR, f"transcript-{time.time_ns()}.jsonl")


class LastTurnTest(unittest.TestCase):
    def parse(self, *entries):
        """Return (user text, agent text) of the last turn of *entries*."""
        path = new_path()
        write(path, entries)
        return transcript.parse_last_user_text(path), transcript.parse_last_turn(path)[0]

    def test_a_turn_started_by_a_task_notification_has_no_user_text(self):
        user_text, agent_text = self.parse(
            user(PROMPT), reply("I've asked a reviewer."),
            TASK_NOTIFICATION, reply("The reviewer finished."),
        )
        self.assertIsNone(user_text)  # not the subagent's words, nor the older prompt
        self.assertEqual(agent_text, "The reviewer finished.")

    def test_a_skill_loaded_mid_turn_stays_in_the_users_turn(self):
        user_text, agent_text = self.parse(
            user(PROMPT), reply("Let me check."), *SKILL_LOAD, reply("Here is what I found."),
        )
        self.assertEqual(user_text, PROMPT)
        self.assertEqual(agent_text, "Let me check.\n\nHere is what I found.")

    def test_a_compaction_summary_is_not_user_text(self):
        summary = user(
            "This session is being continued from a previous conversation. "
            "The user works at a bank.",
            isCompactSummary=True, isVisibleInTranscriptOnly=True,
        )
        user_text, agent_text = self.parse(
            user(PROMPT), reply("Starting."), tool_call("t1", "Bash"), tool_result("t1"),
            summary, reply("Continuing."),
        )
        self.assertEqual(user_text, PROMPT)
        self.assertEqual(agent_text, "Starting.\n\nContinuing.")

    def test_a_meta_prompt_from_claude_code_starts_a_turn(self):
        user_text, agent_text = self.parse(
            user(PROMPT), reply("First reply."), SCHEDULED_PROMPT, reply("The deploy is green."),
        )
        self.assertIsNone(user_text)
        self.assertEqual(agent_text, "The deploy is green.")


class StopRecordingTest(unittest.TestCase):
    def setUp(self):
        support.reset_db()
        with contextlib.redirect_stdout(io.StringIO()):
            preflight.ensure_schema()

    def test_each_reply_is_recorded_whole_and_once(self):
        path = new_path()
        turns = [
            [user(PROMPT), reply("Let me check."), *SKILL_LOAD, reply("Here is what I found.")],
            [TASK_NOTIFICATION, reply("The reviewer finished.")],
            [SCHEDULED_PROMPT, reply("The deploy is green.")],
        ]
        for entries in turns:
            write(path, entries, mode="a")
            stop.handle({"session_id": SID, "transcript_path": path})
        with db.open_db() as conn:
            recorded = [r[0] for r in conn.execute(
                "SELECT content FROM messages WHERE role = 'assistant' ORDER BY id")]
        self.assertEqual(recorded, [
            "Let me check.\n\nHere is what I found.",
            "The reviewer finished.",
            "The deploy is green.",
        ])


if __name__ == "__main__":
    unittest.main()
