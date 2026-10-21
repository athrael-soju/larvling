"""Transcript parsing utilities for Larvling hook scripts."""

import json
import os
import re
import time


def strip_ide_tags(text):
    """Remove leading IDE context tags (opened files, selections) prepended by VSCode."""
    return re.sub(
        r"^(?:<ide_(?:opened_file|selection)>.*?</ide_(?:opened_file|selection)>\s*)+",
        "",
        text,
        flags=re.DOTALL,
    ).strip()


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


def agent_prompts(transcript_path):
    """Return the texts of the prompts in the transcript that Claude Code or an
    agent wrote (see is_real_user_message), leaving out any text the user also
    sent as a prompt of their own. Texts are as the prompt hook stores them,
    without IDE tags."""
    if not transcript_path or not os.path.exists(transcript_path):
        return set()
    agent, user = set(), set()
    for line in _read_transcript_lines(transcript_path):
        if '"user"' not in line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict) or not starts_turn(entry):
            continue
        text = strip_ide_tags(_message_text(entry))
        if text:
            (user if is_real_user_message(entry) else agent).add(text)
    return agent - user


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


def _block_text(content):
    """Return the text of a string or a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("text")
        )
    return ""


# Claude Code's tool_result when the user rejects a tool call and says why.
_REJECTION = "The user doesn't want to"
_REJECTION_FEEDBACK = "the user said:"
# What Claude Code may append after the feedback; not the user's words.
_REJECTION_NOTE = "\nNote: The user's next message"


def _mid_turn_words(entry, tool_names):
    """Return what the user wrote in *entry*, an entry inside a running turn.

    That is a message they queued while the agent worked, their answers to
    AskUserQuestion (not the questions, which the agent wrote), or what they
    said when rejecting a tool call. *tool_names* maps tool_use ids to names.
    """
    if entry.get("isMeta"):
        return []
    if entry.get("type") == "attachment":
        att = entry.get("attachment")
        if not isinstance(att, dict) or att.get("type") != "queued_command":
            return []
        origin = att.get("origin")
        if att.get("isMeta") or not isinstance(origin, dict) or origin.get("kind") != "human":
            return []  # a task notification or another session's message
        text = _block_text(att.get("prompt")).strip()
        return [text] if text else []
    if entry.get("type") != "user":
        return []

    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return []
    words = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        result = _block_text(block.get("content"))
        # Claude Code marks a real rejection as an error and tags the entry
        # with toolDenialKind, which tool output can't set; output that only
        # quotes the wording is neither.
        rejected = (
            block.get("is_error") is True
            and entry.get("toolDenialKind") == "user-rejected"
        )
        if rejected and result.startswith(_REJECTION) and _REJECTION_FEEDBACK in result:
            feedback = result.split(_REJECTION_FEEDBACK, 1)[1].split(_REJECTION_NOTE, 1)[0].strip()
            if feedback:
                words.append(feedback)
        elif tool_names.get(block.get("tool_use_id")) == "AskUserQuestion":
            answered = entry.get("toolUseResult")
            if not isinstance(answered, dict):
                continue
            answers = answered.get("answers")
            notes = answered.get("annotations")
            for question, answer in (answers.items() if isinstance(answers, dict) else ()):
                if isinstance(answer, str) and answer.strip():
                    words.append(answer.strip())
                note = notes.get(question) if isinstance(notes, dict) else None
                note = note.get("notes") if isinstance(note, dict) else None
                if isinstance(note, str) and note.strip():
                    words.append(note.strip())
    return words


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


def _last_turn_user_words(lines):
    """Return (prompt, by_user, mid-turn words) for the last turn of transcript *lines*.

    *prompt* is the text that started the turn, and *by_user* whether the
    user wrote it.
    """
    start, entry = _last_turn_start(lines)
    if entry is None:
        return None, False, []
    prompt = _message_text(entry)
    by_user = is_real_user_message(entry)

    words = []
    tool_names = {}
    for line in lines[start + 1:]:
        try:
            later = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(later, dict):
            continue
        if later.get("type") == "assistant":
            msg = later.get("message", {})
            for block in (msg.get("content") if isinstance(msg, dict) else None) or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_names[block.get("id")] = block.get("name")
            continue
        for text in _mid_turn_words(later, tool_names):
            if not (by_user and text == prompt) and text not in words:
                words.append(text)
    return prompt, by_user, words


def parse_user_turn(transcript_path):
    """Return (prompt, by_user, mid_turn) for the last turn.

    *prompt* is the text that started it, and *by_user* is False when Claude
    Code or an agent wrote it, such as a task notification or a scheduled
    prompt. *mid_turn* lists what the user wrote while the turn ran, oldest
    first: messages they queued, their AskUserQuestion answers and feedback on
    rejected tool calls. Returns (None, False, []) if there is no turn.
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return None, False, []
    return _last_turn_user_words(_read_transcript_lines(transcript_path))


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
