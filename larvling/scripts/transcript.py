"""Transcript parsing utilities for Larvling hook scripts."""

import json
import os
import time


def starts_turn(entry):
    """Return True if this entry starts a turn: a prompt the agent then answers.

    That is the user's message or a <task-notification> (a subagent's result
    delivered to the agent). Tool results don't start one. Neither do meta
    entries, such as a skill body loaded into the running turn, unless Claude
    Code submitted them as a prompt (a scheduled task firing, a message from
    another session), which it marks with promptSource. A compaction summary
    doesn't either: the turn that filled the context carries on after it.
    """
    if entry.get("type") != "user" or entry.get("isCompactSummary"):
        return False
    if entry.get("isMeta") and "promptSource" not in entry:
        return False
    msg = entry.get("message", {})
    if not isinstance(msg, dict):
        return False
    content = msg.get("content", "")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                return False
        return True
    return False


def is_real_user_message(entry):
    """Return True if this entry starts a turn with words the user wrote.

    Meta prompts and task notifications start turns too, but Claude Code or
    an agent wrote them.
    """
    return (
        starts_turn(entry)
        and not entry.get("isMeta")
        and not _message_text(entry).lstrip().startswith("<task-notification>")
    )


def _message_text(entry):
    """Return the text of a user entry, leaving out tool results."""
    content = entry.get("message", {}).get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in content
            if not (isinstance(b, dict) and b.get("type") == "tool_result")
        ]
        return " ".join(p for p in parts if p).strip()
    return ""


def _read_transcript_lines(transcript_path):
    """Read non-empty stripped lines from a transcript file."""
    lines = []
    with open(transcript_path, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if raw:
                lines.append(raw)
    return lines


def _last_turn_start(lines):
    """Return (index, entry) of the entry that started the last turn, or (-1, None)."""
    for i in range(len(lines) - 1, -1, -1):
        try:
            entry = json.loads(lines[i])
        except json.JSONDecodeError:
            continue
        if starts_turn(entry):
            return i, entry
    return -1, None


def parse_last_turn(transcript_path):
    """Extract text and tool call counts from the last assistant turn.

    Reads the transcript once, finds the entry that started the last turn
    (see starts_turn), and collects text blocks and tool_use counts after it.

    Returns (text, tools) where text is the concatenated assistant response
    and tools is a dict of {tool_name: count}.
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return None, {}

    lines = _read_transcript_lines(transcript_path)
    turn_start = _last_turn_start(lines)[0] + 1

    all_text = []
    tools = {}
    for line in lines[turn_start:]:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message", {})
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text = block.get("text", "").strip()
                        if text:
                            parts.append(text)
                    elif block.get("type") == "tool_use":
                        name = block.get("name", "unknown")
                        tools[name] = tools.get(name, 0) + 1
                elif isinstance(block, str) and block.strip():
                    parts.append(block.strip())
            if parts:
                all_text.append("\n".join(parts))
        elif content:
            all_text.append(str(content))

    text = "\n\n".join(all_text) if all_text else None

    return text, tools


def parse_last_user_text(transcript_path):
    """Return the text the user wrote to start the last turn.

    Returns None when something else started it, such as a task notification.
    The user's earlier message belongs to an earlier turn, so it isn't used.
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return None

    _, entry = _last_turn_start(_read_transcript_lines(transcript_path))
    if entry is None or not is_real_user_message(entry):
        return None
    return _message_text(entry)


def wait_for_transcript_stable(transcript_path, interval=0.1, max_wait=2):
    """Wait until the transcript file stops being written to."""
    if not transcript_path or not os.path.exists(transcript_path):
        return
    last_size = os.path.getsize(transcript_path)
    waited = 0
    while waited < max_wait:
        time.sleep(interval)
        waited += interval
        size = os.path.getsize(transcript_path)
        if size == last_size:
            return
        last_size = size
