"""
Session extraction — SessionEnd, PreCompact and SessionStart catch-up.

Reads the messages a session recorded since its last extraction, calls the
model (via sdk.py) to identify knowledge, session tags and tasks, then writes
the results to SQLite. A long session is extracted in chunks, one call each.
"""

import asyncio
import contextlib
import io
import json
import os
import re
import sys
import time
import unicodedata
from datetime import date

from config import get_config
from db import (
    SCHEMA_VERSION,
    open_db,
    has_table,
    get_schema_version,
    record_message,
    log,
)
from hooks_util import run_detached_or_inline
from sdk import call_model
from search import duplicate_statement, duplicate_task, duplicate_topic
from transcript import agent_prompts


# ---------------------------------------------------------------------------
# Validation constants (used by schema and storage)
# ---------------------------------------------------------------------------

VALID_DOMAINS = {
    "personal",
    "professional",
    "preferences",
    "interests",
    "knowledge",
    "technical",
    "workflow",
}
VALID_PRIORITY = {"low", "medium", "high"}
VALID_HORIZON = {"now", "soon", "later"}
VALID_STATUS = {"open", "done", "dropped"}
# Extraction sets 'user' or 'agent'. /remember sets 'manual'; 'doc' is unused for now.
VALID_SOURCE = {"user", "agent"}
VALID_KIND = {"fact", "decision", "preference", "assessment"}
VALID_ABOUT = {"user", "domain"}
# (source, kind) pairs extraction may store. Facts about the user come from the
# user; from the agent, only general facts and assessments the user agreed with.
ALLOWED_SOURCING = {
    ("user", "fact"),
    ("user", "decision"),
    ("user", "preference"),
    ("agent", "fact"),
    ("agent", "assessment"),
}
# Shortest evidence and endorsement accepted, in words: a word or two ("I",
# "my project", "thanks") turns up in almost any message.
MIN_EVIDENCE_WORDS = 3
MIN_ENDORSEMENT_WORDS = 2

# Messages per model call are capped by their total length; a longer session
# is extracted in several chunks. One very long message is cut short in the
# prompt, so its evidence must come from the part the model saw.
CHUNK_CHARS = 60000
MESSAGE_CHARS = 8000
# A worker holds a session for this long; a crashed worker's claim expires.
LEASE_MINUTES = 15
# How often a SessionEnd worker checks whether a running worker let go.
CLAIM_POLL_SECONDS = 5
# SessionStart catch-up leaves a session alone until it has been quiet this
# long, in the DB and in its transcript, so a session still running in
# another terminal isn't cut in half.
CATCH_UP_IDLE_MINUTES = 10


# ---------------------------------------------------------------------------
# Analysis prompt and schema
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """\
Analyze this conversation and extract structured data. {scope} Messages are \
numbered in order.

{transcript}

## Database access

Query the database to check for existing data before deciding actions:

{python} "{query_script}" "<SQL>" --read-only

To look for existing knowledge or tasks, search instead of writing LIKE queries. \
It ranks matches on any of the words, in claims and topic titles:

{python} "{search_script}" "<words>"            (current statements, with their topic)
{python} "{search_script}" "<words>" --tasks    (tasks in every status)

Six tables in 3 parent→child pairs:
- `topics` (id INTEGER PK, title, domain, tags, created, updated)
- `statements` (id INTEGER PK, topic_id INTEGER FK→topics(id), claim, created, updated, source, kind, session_id, superseded_by, superseded_at)
  - `current_statements`: a view of the statements still in force. Query it, not \
`statements`, for existing knowledge; superseded statements are history.
- `tasks` (id INTEGER PK, title, domain, status, priority, horizon, metadata, created)
- `updates` (id INTEGER PK, task_id INTEGER FK→tasks(id), content, timestamp)
- `sessions` (id TEXT PK, started_at, ended_at, duration_min, title, agent_summary, exchange_count, summary_at, summary_msg_count, tags)
- `messages` (id INTEGER PK, session_id TEXT FK, timestamp, role, content, metadata)

## Workflow — follow these three phases in order

### Phase 1 — Identify candidates

Read the whole conversation and list what is worth extracting. Do NOT assign actions yet.

Later messages can correct earlier ones. When the USER corrects, retracts or changes \
something later on, extract only the final version: never store a claim the \
conversation goes on to contradict.

- **Knowledge candidates**: durable facts worth remembering across sessions. \
Who said it decides whether it can be stored:
  - From USER (source "user"): personal info, professional info, preferences, \
interests (asking about ANY topic = an interest), decisions, opinions, workflow habits, \
and facts about the people they deal with. Anything about the user, their work or \
those people comes from the USER's own words only.
  - From AGENT (source "agent"): general domain knowledge only (science, history, \
concepts), true no matter who is asking — NOT code-level implementation details, and \
NEVER anything about the user's situation. The agent's guesses, inferences and \
characterisations of the user ("you're probably...", "since your team is small...") \
are not knowledge: drop them, however plausible.
  - AGENT recommendations and judgements are dropped too. If the USER adopts a \
recommendation, that is the user's decision (source "user", kind "decision"). Only \
when the USER explicitly agrees with an AGENT judgement, store the judgement as kind \
"assessment" with the user's words of agreement as "endorsement".
  - A claim says what its evidence says and no more. Do not infer, generalise or \
combine: "our CI" says nothing about the size of the user's team.
  - Ask: "Is this useful?" If not, do not include it.
  - Prefer fewer, higher-quality items over many low-value ones.
- **Task candidates**: durable commitments and TODOs that outlive this conversation, \
plus progress on — or completion of — existing tasks. If the conversation shows an open \
task was finished, that is an `update_task → done` candidate; record it so completed \
work leaves the open list instead of lingering until the next tidy.
- **Session tags**: topics/themes of this conversation (1-4 words each, max ~8 tags).

### Phase 2 — Dedup check (mandatory)

For EACH knowledge candidate, search for overlap with its key words \
(`search.py "<words>"`). Try other wordings too: a match may use different words.

For EACH task candidate, search tasks for overlap (`search.py "<words>" --tasks`, \
which covers ALL statuses, not just open ones) and query the updates of any match. A task with \
`status='dropped'` was deliberately retired, and one with `status='done'` is already \
complete: treat both as tombstones. If a candidate resembles a dropped or done task, \
`skip` it — never recreate or reopen retired work unless the user explicitly asked for \
it again in this conversation, and then reopen it with `update_task` (`status: open`) \
rather than adding it again.

For session tags, query the current session's tags:
`SELECT tags FROM sessions WHERE id = '{session_id}'`

This step is mandatory — skipping it creates duplicates. You must run the queries \
before proceeding to Phase 3.

### Phase 3 — Decide actions

Based on your query results from Phase 2, assign an action for each candidate.

**Knowledge actions:**
- **add_topic**: no existing overlap — create a new topic with its first statement
- **add_statement**: related topic exists — add a new statement to it (include "topic_id")
- **supersede_statement**: a current statement is wrong, out of date or less precise \
than the candidate — replace it (include its "statement_id"). The new claim goes into \
the same topic and the old statement is kept as history. Use this for every correction: \
never add a second statement that contradicts or "corrects" a current one.
- **update_topic**: existing topic title/domain/tags need updating (include "topic_id")
- **skip**: knowledge already exists unchanged — do NOT include it in the output

Never delete data, and never rewrite a claim in place.

**Task actions:**
- **add_task**: a durable commitment that outlives this conversation and is not tracked \
in ANY status — create a new task. Do NOT mint a task for a passing aside or an \
intention that was already resolved within this same conversation, and never create one \
that matches a `dropped` or `done` task (that resurrects retired work — `skip` instead)
- **add_update**: genuinely new context, progress, or notes about an existing task (include "task_id"). If the same fact is already recorded in updates, skip instead.
- **update_task**: change status/priority/horizon on an existing task (include "task_id"); also records the reason as an update entry
- **skip**: task already tracked and no new information — do NOT include it in the output

**Session tags:** return the FULL updated list — merge similar tags, drop tags no longer \
relevant, and add new tags from this conversation. If current tags is empty, just return \
new tags from this conversation.

## Output schema

Each knowledge item: {{"topic_title": "...", "claim": "...", "domain": "...", "tags": "...", "source": "user|agent", "kind": "fact|decision|preference|assessment", "about": "user|domain", "evidence": "...", "endorsement": "...", "action": "add_topic|add_statement|supersede_statement|update_topic", "topic_id": N, "statement_id": N}}
- topic_title: the subject in a few words (e.g. "CI pipeline", "Octopuses"). Name the \
subject only: a title never makes a claim about the user.
- claim: only what the evidence says, with no inference, generalisation or detail \
taken from elsewhere
- Domains: personal, professional, preferences, interests, knowledge, technical, workflow
- Tags: short topic label (e.g. "octopuses", "physics", "python")
- source: whose words the claim comes from: "user" (a USER message) or "agent" (an AGENT message)
- kind: "fact" (something true: about the user, their work or the people they deal \
with, or general domain knowledge), "decision" (a choice the user has made), \
"preference" (how the user likes things done, or their opinion), "assessment" (an \
AGENT judgement the USER explicitly agreed with — nothing else)
- about: "user" (the user, their work, or the people they deal with) or "domain" \
(general knowledge, true no matter who is asking)
- evidence: a span of at least three words copied word for word from one of the \
source's messages that supports the claim. Do not paraphrase or join text from two \
messages: an item whose evidence is not in one of that speaker's messages, or is \
shorter than three words, is discarded.
- endorsement: for kind "assessment" only, the USER's words agreeing with it, at least \
two words copied word for word; otherwise ""
- Allowed: source "user" with kind fact, decision or preference; source "agent" with \
kind "fact" and about "domain", or kind "assessment" with an endorsement. Anything else \
is discarded.

Each task item: {{"title": "...", "domain": "...", "priority": "low|medium|high", "horizon": "now|soon|later", "status": "open|done|dropped", "due": "YYYY-MM-DD", "action": "add_task|add_update|update_task", "task_id": N, "content": "..."}}
- "content" is used for add_update (the fact/note) and update_task (the reason for the change)
- domain: same as knowledge domains
- priority: how important (low/medium/high)
- horizon: when to act (now/soon/later)
- status: open (default), done (completed), dropped (no longer relevant)
- due: the date the task must be done by, only when the USER gives one ("by Friday" \
is resolved against the date the conversation took place); otherwise leave it out

Return JSON:
{{
  "knowledge": [{{"topic_title": "...", "claim": "...", "domain": "...", "tags": "...", "source": "user", "kind": "fact", "about": "user", "evidence": "...", "action": "add_topic"}}],
  "session_tags": ["python", "deployment"],
  "tasks": [{{"title": "refactor auth module", "domain": "technical", "priority": "medium", "horizon": "soon", "action": "add_task"}}]
}}

If nothing to extract for a section, use empty list."""


EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "knowledge": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic_title": {"type": "string"},
                    "claim": {"type": "string"},
                    "domain": {"type": "string", "enum": list(VALID_DOMAINS)},
                    "tags": {"type": "string"},
                    "source": {"type": "string", "enum": list(VALID_SOURCE)},
                    "kind": {"type": "string", "enum": list(VALID_KIND)},
                    "about": {"type": "string", "enum": list(VALID_ABOUT)},
                    "evidence": {"type": "string"},
                    "endorsement": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": [
                            "add_topic",
                            "add_statement",
                            "supersede_statement",
                            "update_topic",
                        ],
                    },
                    "topic_id": {"type": "integer"},
                    "statement_id": {"type": "integer"},
                },
                "required": [
                    "topic_title",
                    "claim",
                    "domain",
                    "tags",
                    "source",
                    "kind",
                    "about",
                    "evidence",
                    "action",
                ],
            },
        },
        "session_tags": {"type": "array", "items": {"type": "string"}},
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "domain": {"type": "string", "enum": list(VALID_DOMAINS)},
                    "priority": {"type": "string", "enum": list(VALID_PRIORITY)},
                    "horizon": {"type": "string", "enum": list(VALID_HORIZON)},
                    "status": {"type": "string", "enum": list(VALID_STATUS)},
                    "action": {
                        "type": "string",
                        "enum": ["add_task", "add_update", "update_task"],
                    },
                    "task_id": {"type": "integer"},
                    "content": {"type": "string"},
                    "due": {"type": "string"},
                },
                "required": ["action"],
            },
        },
    },
    "required": ["knowledge", "session_tags", "tasks"],
}


_SPEAKERS = {"user": "USER", "assistant": "AGENT"}


def format_transcript(messages):
    """Render (id, role, content) messages as numbered USER/AGENT turns.

    A message over MESSAGE_CHARS is cut short with a note of how much is missing.
    """
    lines = []
    for n, (_, role, content) in enumerate(messages, 1):
        text = content or ""
        if len(text) > MESSAGE_CHARS:
            text = text[:MESSAGE_CHARS] + f" [... {len(text) - MESSAGE_CHARS} more characters]"
        lines.append(f"[{n}] {_SPEAKERS[role]}: {text}")
    return "\n\n".join(lines)


def build_extraction_prompt(messages, session_id="", part=None, day=None):
    """Format the extraction prompt for *messages*, (id, role, content) tuples.

    *part* is (n, total) when the messages are one chunk of a longer run, and
    *day* the date the conversation took place.
    """
    if part and part[1] > 1:
        scope = (
            f"It is part {part[0]} of {part[1]} of a long session; the parts are "
            "extracted in order, so query the database to see what earlier parts stored."
        )
    else:
        scope = "It is a whole session, or the part of it not yet extracted."
    if day:
        scope += f" It took place on {day}."
    scripts = os.path.dirname(__file__)
    query_script = os.path.join(scripts, "query.py")
    search_script = os.path.join(scripts, "search.py")
    return EXTRACTION_PROMPT.format(
        search_script=search_script.replace("\\", "/"),
        scope=scope,
        transcript=format_transcript(messages),
        session_id=session_id or "",
        query_script=query_script.replace("\\", "/"),
        python=os.path.basename(sys.executable),
    )


# ---------------------------------------------------------------------------
# Storage functions
# ---------------------------------------------------------------------------


def _skip(session_id, action, reason, **extra):
    """Log a skipped extraction item."""
    log("extraction_skipped", session_id, action=action, reason=reason, **extra)


def _parse_due(task, action, session_id):
    """Return the task's due date as YYYY-MM-DD, or "" if it has none. A due
    date that isn't a date is left out and logged; the rest of the item stands."""
    due = _field(task, "due")
    if not due:
        return ""
    try:
        return date.fromisoformat(due).isoformat()
    except ValueError:
        log("extraction_field_dropped", session_id, action=action, field="due", value=due[:40])
        return ""


def _parse_id(value, field_name, action, session_id):
    """Parse an integer ID field. Returns int or None (with logging)."""
    if value is None:
        _skip(session_id, action, f"missing {field_name}")
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        _skip(session_id, action, f"invalid {field_name}", value=str(value))
        return None


def _field(item, key):
    """Return *item*[*key*] as stripped text; a missing or null value gives ""."""
    return str(item.get(key) or "").strip()


# Kana, CJK ideographs, Thai, Lao, Khmer and Myanmar are written without spaces
# between words, so quotes in them are matched character by character;
# otherwise a whole clause would be one word.
_CJK = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"  # kana, CJK
_SEA = (
    "\u0e00-\u0eff"  # Thai, Lao
    "\u1000-\u109f\ua9e0-\ua9ff\uaa60-\uaa7f"  # Myanmar
    "\u1780-\u17ff\u19e0-\u19ff"  # Khmer
)
_UNSPACED = _CJK + _SEA
# An apostrophe splits a word for matching, so "my team" is found in "my
# team's CI"; for counting, a contraction ("I'm", "don't") is one word.
_TOKEN = re.compile(f"[{_UNSPACED}]|[^\\W{_UNSPACED}]+")
_COUNTED = re.compile(f"[{_CJK}]|[^\\W{_UNSPACED}]+(?:'[^\\W{_UNSPACED}]+)*")
_SEA_LETTER = re.compile(f"[{_SEA}]")
# A CJK character is about a word; in Thai, Lao, Khmer and Myanmar a word is
# several letters, vowel and tone marks aside.
_SEA_LETTERS_PER_WORD = 3
_APOSTROPHES = str.maketrans({c: "'" for c in "\u2018\u2019\u02bc`"})


def _normalize(text):
    return unicodedata.normalize("NFKC", text or "").casefold().translate(_APOSTROPHES)


def _words(text):
    """Return the lowercase words of *text*, ignoring punctuation and spacing."""
    return " ".join(_TOKEN.findall(_normalize(text)))


def _word_count(text):
    """Roughly how many words *text* has (see _COUNTED and _SEA_LETTERS_PER_WORD)."""
    text = _normalize(text)
    letters = sum(
        1 for c in _SEA_LETTER.findall(text) if not unicodedata.category(c).startswith("M")
    )
    return len(_COUNTED.findall(text)) + letters / _SEA_LETTERS_PER_WORD


def _quoted_in(quote, texts):
    """True when *quote* appears word for word in one of *texts*."""
    words = _words(quote)
    return bool(words) and any(f" {words} " in f" {_words(t)} " for t in texts)


def _as_list(texts):
    """Return *texts* as a list: one message (a string) or several."""
    return [texts] if isinstance(texts, str) else list(texts or [])


def _parse_provenance(item, action, session_id, user_texts, agent_texts):
    """Check an item's source, kind and evidence.

    The evidence must appear in one of the source's messages, and an
    assessment also needs the user's endorsement. Returns (source, kind) or
    None (with logging).
    """
    source = _field(item, "source").lower()
    if source not in VALID_SOURCE:
        _skip(session_id, action, "invalid source", source=source)
        return None
    kind = _field(item, "kind").lower()
    if kind not in VALID_KIND:
        _skip(session_id, action, "invalid kind", kind=kind)
        return None
    if (source, kind) not in ALLOWED_SOURCING:
        _skip(session_id, action, "kind not allowed from source", source=source, kind=kind)
        return None
    about = _field(item, "about").lower()
    if about not in VALID_ABOUT:
        _skip(session_id, action, "invalid about", about=about)
        return None
    if source == "agent" and kind == "fact" and about == "user":
        _skip(session_id, action, "agent claim about the user")
        return None
    # These checks prove only that the quotes are in the conversation, not that
    # they support the claim; the minimum length stops a stray word passing.
    evidence = _field(item, "evidence")
    if not _quoted_in(evidence, user_texts if source == "user" else agent_texts):
        _skip(session_id, action, f"evidence not in {source} text", evidence=evidence[:80])
        return None
    if _word_count(evidence) < MIN_EVIDENCE_WORDS:
        _skip(session_id, action, "evidence too short", evidence=evidence[:80])
        return None
    if kind == "assessment":
        endorsement = _field(item, "endorsement")
        if not _quoted_in(endorsement, user_texts):
            _skip(session_id, action, "assessment not endorsed by user")
            return None
        if _word_count(endorsement) < MIN_ENDORSEMENT_WORDS:
            _skip(session_id, action, "endorsement too short", endorsement=endorsement[:80])
            return None
    return source, kind


def process_knowledge(conn, knowledge_list, session_id=None, user_texts=(), agent_texts=()):
    """Process extracted knowledge into topics+statements tables.

    Handles 4 actions: add_topic, add_statement, supersede_statement and
    update_topic. Each statement written gets a source, kind and session_id.
    A superseded statement is kept, pointing at the one that replaced it.
    *user_texts* and *agent_texts* are the messages (a list, or one string)
    the items came from, used to check each item's evidence.
    Never deletes data — retirement is done via updates, not removal.
    Returns (topics_inserted, stmts_inserted, stmts_superseded, topics_updated).
    """
    if not knowledge_list or not has_table(conn, "topics"):
        return 0, 0, 0, 0
    user_texts = _as_list(user_texts)
    agent_texts = _as_list(agent_texts)
    threshold = get_config()["dedup_threshold"]

    topics_inserted = 0
    stmts_inserted = 0
    stmts_superseded = 0
    topics_updated = 0

    for item in knowledge_list:
        action = _field(item, "action").lower()
        if action not in (
            "add_topic",
            "add_statement",
            "supersede_statement",
            "update_topic",
        ):
            continue

        if action == "update_topic":
            topic_id = _parse_id(item.get("topic_id"), "topic_id", action, session_id)
            if topic_id is None:
                continue
            if not conn.execute(
                "SELECT 1 FROM topics WHERE id = ?", (topic_id,)
            ).fetchone():
                _skip(session_id, action, "topic not found", topic_id=topic_id)
                continue
            title = _field(item, "topic_title")
            domain = _field(item, "domain").lower()
            if domain and domain not in VALID_DOMAINS:
                _skip(session_id, action, "invalid domain", domain=domain)
                continue
            tags = _field(item, "tags")
            if title:
                conn.execute(
                    "UPDATE topics SET title = ?, domain = COALESCE(?, domain), "
                    "tags = COALESCE(?, tags), updated = datetime('now') WHERE id = ?",
                    (title, domain or None, tags or None, topic_id),
                )
                topics_updated += 1
            continue

        if action == "supersede_statement":
            stmt_id = _parse_id(
                item.get("statement_id"), "statement_id", action, session_id
            )
            if stmt_id is None:
                continue
            claim = _field(item, "claim")
            if not claim:
                _skip(session_id, action, "missing claim")
                continue
            provenance = _parse_provenance(item, action, session_id, user_texts, agent_texts)
            if provenance is None:
                continue
            old = conn.execute(
                "SELECT topic_id, claim, superseded_at FROM statements WHERE id = ?",
                (stmt_id,),
            ).fetchone()
            if not old:
                _skip(session_id, action, "statement not found", statement_id=stmt_id)
                continue
            if old["superseded_at"]:
                _skip(session_id, action, "statement already superseded", statement_id=stmt_id)
                continue
            if old["claim"] == claim:
                continue
            # The new claim may already be current in the same topic (a legacy
            # correction stored next to what it corrects): point at it instead
            # of a second copy.
            existing = conn.execute(
                "SELECT id FROM current_statements WHERE claim = ? AND topic_id = ? "
                "AND id != ? ORDER BY id DESC LIMIT 1",
                (claim, old["topic_id"], stmt_id),
            ).fetchone()
            if existing:
                new_id = existing["id"]
            else:
                new_id = conn.execute(
                    "INSERT INTO statements (topic_id, claim, source, kind, session_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (old["topic_id"], claim, *provenance, session_id),
                ).lastrowid
            conn.execute(
                "UPDATE statements SET superseded_by = ?, superseded_at = datetime('now') "
                "WHERE id = ?",
                (new_id, stmt_id),
            )
            stmts_superseded += 1
            continue

        if action == "add_statement":
            topic_id = _parse_id(item.get("topic_id"), "topic_id", action, session_id)
            if topic_id is None:
                continue
            claim = _field(item, "claim")
            if not claim:
                _skip(session_id, action, "missing claim")
                continue
            provenance = _parse_provenance(item, action, session_id, user_texts, agent_texts)
            if provenance is None:
                continue
            if not conn.execute(
                "SELECT 1 FROM topics WHERE id = ?", (topic_id,)
            ).fetchone():
                _skip(session_id, action, "topic not found", topic_id=topic_id)
                continue
            # Exact-match dedup safety net
            if conn.execute(
                "SELECT 1 FROM current_statements WHERE topic_id = ? AND claim = ?",
                (topic_id, claim),
            ).fetchone():
                continue
            dup = duplicate_statement(conn, claim, threshold, topic_id)
            if dup:
                _skip(session_id, action, "near_duplicate", statement_id=dup["id"])
                continue
            conn.execute(
                "INSERT INTO statements (topic_id, claim, source, kind, session_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (topic_id, claim, *provenance, session_id),
            )
            stmts_inserted += 1
            continue

        # add_topic: new topic + first statement
        claim = _field(item, "claim")
        if not claim:
            _skip(session_id, action, "missing claim")
            continue
        topic_title = _field(item, "topic_title")
        if not topic_title:
            _skip(session_id, action, "missing topic_title")
            continue
        domain = _field(item, "domain").lower()
        if not domain or domain not in VALID_DOMAINS:
            _skip(session_id, action, "invalid domain", domain=domain)
            continue
        tags = _field(item, "tags")
        if not tags:
            _skip(session_id, action, "missing tags")
            continue
        provenance = _parse_provenance(item, action, session_id, user_texts, agent_texts)
        if provenance is None:
            continue

        # Exact-match dedup on claim
        if conn.execute(
            "SELECT 1 FROM current_statements WHERE claim = ?", (claim,)
        ).fetchone():
            continue
        dup = duplicate_statement(conn, claim, threshold)
        if dup:
            _skip(session_id, action, "near_duplicate", statement_id=dup["id"])
            continue

        # A reworded title of an existing topic: the statement goes there.
        same_topic = duplicate_topic(conn, topic_title, threshold)
        if same_topic:
            log("topic_reused", session_id, topic_id=same_topic["id"], title=topic_title[:80])
            topic_id = same_topic["id"]
        else:
            topic_id = conn.execute(
                "INSERT INTO topics (title, domain, tags) VALUES (?, ?, ?)",
                (topic_title, domain, tags),
            ).lastrowid
            topics_inserted += 1
        conn.execute(
            "INSERT INTO statements (topic_id, claim, source, kind, session_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (topic_id, claim, *provenance, session_id),
        )
        stmts_inserted += 1

    return topics_inserted, stmts_inserted, stmts_superseded, topics_updated


def process_tasks(conn, tasks_list, session_id=None):
    """Process extracted tasks into tasks+updates tables.

    Handles 3 actions: add_task, add_update, update_task.
    Returns (tasks_inserted, updates_inserted, tasks_updated).
    """
    if not tasks_list or not has_table(conn, "tasks"):
        return 0, 0, 0

    tasks_inserted = 0
    updates_inserted = 0
    tasks_updated = 0
    threshold = get_config()["dedup_threshold"]

    for task in tasks_list:
        action = _field(task, "action").lower()
        if action not in ("add_task", "add_update", "update_task"):
            continue

        if action == "add_update":
            task_id = _parse_id(task.get("task_id"), "task_id", action, session_id)
            if task_id is None:
                continue
            content = _field(task, "content")
            if not content:
                _skip(session_id, action, "missing content")
                continue
            if not conn.execute(
                "SELECT 1 FROM tasks WHERE id = ?", (task_id,)
            ).fetchone():
                _skip(session_id, action, "task not found", task_id=task_id)
                continue
            # Exact-match dedup safety net
            if conn.execute(
                "SELECT 1 FROM updates WHERE task_id = ? AND content = ?",
                (task_id, content),
            ).fetchone():
                continue
            # Touches the task (db.TOUCH_TRIGGER).
            conn.execute(
                "INSERT INTO updates (task_id, content) VALUES (?, ?)",
                (task_id, content),
            )
            updates_inserted += 1
            continue

        if action == "update_task":
            task_id = _parse_id(task.get("task_id"), "task_id", action, session_id)
            if task_id is None:
                continue
            content = _field(task, "content")
            if not conn.execute(
                "SELECT 1 FROM tasks WHERE id = ?", (task_id,)
            ).fetchone():
                _skip(session_id, action, "task not found", task_id=task_id)
                continue

            # Build SET clause — skip invalid enum values
            sets = []
            params = []
            status = _field(task, "status").lower()
            if status:
                if status not in VALID_STATUS:
                    _skip(session_id, action, "invalid status", status=status)
                    continue
                sets.append("status = ?")
                params.append(status)
            priority = _field(task, "priority").lower()
            if priority:
                if priority not in VALID_PRIORITY:
                    _skip(session_id, action, "invalid priority", priority=priority)
                    continue
                sets.append("priority = ?")
                params.append(priority)
            horizon = _field(task, "horizon").lower()
            if horizon:
                if horizon not in VALID_HORIZON:
                    _skip(session_id, action, "invalid horizon", horizon=horizon)
                    continue
                sets.append("horizon = ?")
                params.append(horizon)
            title = _field(task, "title")
            if title:
                sets.append("title = ?")
                params.append(title)
            due = _parse_due(task, action, session_id)
            if due:
                sets.append("due = ?")
                params.append(due)

            if sets:
                sets.append("updated = datetime('now')")
                params.append(task_id)
                conn.execute(
                    f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?",
                    params,
                )
                tasks_updated += 1

            # Record the reason as an update entry
            if content:
                # Exact-match dedup safety net
                if not conn.execute(
                    "SELECT 1 FROM updates WHERE task_id = ? AND content = ?",
                    (task_id, content),
                ).fetchone():
                    conn.execute(
                        "INSERT INTO updates (task_id, content) VALUES (?, ?)",
                        (task_id, content),
                    )
                    updates_inserted += 1
            continue

        # add_task: new task
        title = _field(task, "title")
        if not title:
            _skip(session_id, action, "missing title")
            continue
        domain = _field(task, "domain").lower()
        if not domain or domain not in VALID_DOMAINS:
            _skip(session_id, action, "invalid domain", domain=domain)
            continue
        priority = _field(task, "priority").lower()
        if not priority or priority not in VALID_PRIORITY:
            _skip(session_id, action, "invalid priority", priority=priority)
            continue
        horizon = _field(task, "horizon").lower()
        if not horizon or horizon not in VALID_HORIZON:
            _skip(session_id, action, "invalid horizon", horizon=horizon)
            continue
        due = _parse_due(task, action, session_id)

        # Dedup: skip if a task with this title exists in ANY status. An open match
        # is a duplicate; a 'dropped' or 'done' match is a tombstone — re-adding it
        # would resurrect retired work. Reopening must go through update_task, never
        # an automatic insert. (Prompt enforces this fuzzily; this is the hard floor.)
        if conn.execute(
            "SELECT 1 FROM tasks WHERE title = ? AND status IN ('open', 'dropped', 'done')",
            (title,),
        ).fetchone():
            continue
        # Same for a reworded title.
        dup = duplicate_task(conn, title, threshold)
        if dup:
            _skip(session_id, action, "near_duplicate", task_id=dup["id"], status=dup["status"])
            continue

        metadata = json.dumps({"source_session_id": session_id}) if session_id else None
        conn.execute(
            "INSERT INTO tasks (title, domain, priority, horizon, metadata, due) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (title, domain, priority, horizon, metadata, due or None),
        )
        tasks_inserted += 1

    return tasks_inserted, updates_inserted, tasks_updated


def store_tags(conn, session_id, tags):
    """Replace session tags with the model's consolidated list (deduped)."""
    if not tags:
        return  # Empty model response -> keep existing tags

    # Case-insensitive dedup, preserving model's ordering (most relevant first)
    seen = set()
    deduped = []
    for t in tags:
        t_clean = str(t).strip()
        if t_clean and t_clean.lower() not in seen:
            deduped.append(t_clean)
            seen.add(t_clean.lower())

    if not deduped:
        return

    conn.execute(
        "UPDATE sessions SET tags = ? WHERE id = ?",
        (", ".join(deduped), session_id),
    )


# ---------------------------------------------------------------------------
# Extraction state: which messages are done, and who is working on a session
# ---------------------------------------------------------------------------


def _lease_token():
    """A lease value: the UTC time it was taken (as datetime('now') writes it) and our pid."""
    return f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} {os.getpid()}"


def _lease_free(column="extract_lease"):
    return f"({column} IS NULL OR {column} < datetime('now', '-{LEASE_MINUTES} minutes'))"


def claim_session(conn, session_id):
    """Take the session's extraction lease.

    Returns the lease token, or None if another worker holds a live one or
    the session has no row.
    """
    token = _lease_token()
    cur = conn.execute(
        f"UPDATE sessions SET extract_lease = ? WHERE id = ? AND {_lease_free()}",
        (token, session_id),
    )
    conn.commit()
    return token if cur.rowcount == 1 else None


def renew_claim(conn, session_id, token):
    """Extend a lease we hold. Returns the new token, or None if it was lost."""
    new = _lease_token()
    cur = conn.execute(
        "UPDATE sessions SET extract_lease = ? WHERE id = ? AND extract_lease = ?",
        (new, session_id, token),
    )
    conn.commit()
    return new if cur.rowcount == 1 else None


def release_claim(conn, session_id, token):
    """Give up a lease we hold."""
    conn.execute(
        "UPDATE sessions SET extract_lease = NULL WHERE id = ? AND extract_lease = ?",
        (session_id, token),
    )
    conn.commit()


def relabel_agent_prompts(conn, session_id, texts):
    """Mark the session's unextracted 'user' messages whose text Claude Code or
    an agent wrote (*texts*, from transcript.agent_prompts) as 'system'.

    The Stop hook does this for its own turn, but extraction can run before
    it does: at PreCompact, after an interrupted turn, or for a crashed
    session. Words the user added mid-turn are left alone.
    """
    if not texts:
        return 0
    rows = conn.execute(
        "SELECT m.id, m.content FROM messages m JOIN sessions s ON s.id = m.session_id "
        "WHERE m.session_id = ? AND m.role = 'user' "
        "AND m.id > COALESCE(s.extracted_through, 0) "
        "AND NOT COALESCE(CASE WHEN json_valid(m.metadata) "
        "THEN json_extract(m.metadata, '$.mid_turn') END, 0)",
        (session_id,),
    ).fetchall()
    ids = [r[0] for r in rows if (r[1] or "").strip() in texts]
    for mid in ids:
        conn.execute("UPDATE messages SET role = 'system' WHERE id = ?", (mid,))
    conn.commit()
    return len(ids)


def answered(messages):
    """Leave out the messages after the last agent reply: a prompt that is
    still being answered is extracted later, with its answer."""
    for i in range(len(messages) - 1, -1, -1):
        if messages[i][1] == "assistant":
            return messages[: i + 1]
    return []


def pending_messages(conn, session_id):
    """Return the session's user and agent messages not yet extracted.

    Each is an (id, role, content) tuple, oldest first.
    """
    return [
        tuple(r)
        for r in conn.execute(
            "SELECT m.id, m.role, m.content FROM messages m "
            "JOIN sessions s ON s.id = m.session_id "
            "WHERE m.session_id = ? AND m.role IN ('user', 'assistant') "
            "AND m.id > COALESCE(s.extracted_through, 0) "
            "ORDER BY m.id",
            (session_id,),
        )
    ]


def pending_sessions(conn, exclude=None, idle_minutes=CATCH_UP_IDLE_MINUTES):
    """Return the ids of sessions with messages not yet extracted, oldest first.

    Leaves out *exclude* (the session that is starting), sessions with a
    message in the last *idle_minutes*, and sessions another worker holds.
    """
    rows = conn.execute(
        "SELECT s.id FROM sessions s JOIN messages m ON m.session_id = s.id "
        "WHERE m.role IN ('user', 'assistant') AND s.id IS NOT ? "
        f"AND m.id > COALESCE(s.extracted_through, 0) AND {_lease_free('s.extract_lease')} "
        "GROUP BY s.id HAVING MAX(m.timestamp) < datetime('now', ?) "
        "ORDER BY MAX(m.id)",
        (exclude, f"-{idle_minutes} minutes"),
    ).fetchall()
    return [r[0] for r in rows]


def chunk_messages(messages, budget=None):
    """Split messages into chunks whose text in the prompt fits *budget*
    (CHUNK_CHARS by default).

    A chunk that ends on user messages (a prompt, and what the user added
    while it was answered) hands them to the next chunk, up to half a chunk's
    worth, so a question and its answer stay together.
    """
    budget = budget or CHUNK_CHARS

    def length(message):
        return min(len(message[2] or ""), MESSAGE_CHARS)

    chunks, current, size = [], [], 0
    for message in messages:
        if current and size + length(message) > budget:
            carry, carried = [], 0
            while (len(current) > 1 and current[-1][1] == "user"
                   and carried + length(current[-1]) <= budget // 2):
                carried += length(current[-1])
                carry.insert(0, current.pop())
            chunks.append(current)
            current, size = carry, carried
        current.append(message)
        size += length(message)
    if current:
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _summary(parts, empty):
    return ", ".join(parts) if parts else empty


def _store(session_id, token, chunk, result, cfg):
    """Write one chunk's results, watermark and system message in one transaction.

    Returns the counts written, or None if *token* no longer holds the lease.
    """
    user_texts = [content or "" for _, role, content in chunk if role == "user"]
    agent_texts = [content or "" for _, role, content in chunk if role == "assistant"]

    with open_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT extract_lease FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row or row[0] != token:
            conn.rollback()
            return None

        # Knowledge (topics + statements)
        topics_ins = stmts_ins = stmts_sup = topics_upd = 0
        if cfg["knowledge_extraction"]:
            topics_ins, stmts_ins, stmts_sup, topics_upd = process_knowledge(
                conn, result.get("knowledge", []), session_id, user_texts, agent_texts
            )

        # Tasks
        tasks_ins = updates_ins = tasks_upd = 0
        if cfg["task_tracking"]:
            tasks_ins, updates_ins, tasks_upd = process_tasks(
                conn, result.get("tasks", []), session_id
            )

        # Session tags
        if cfg["session_tags"]:
            session_tags = result.get("session_tags", [])
            if isinstance(session_tags, list):
                store_tags(conn, session_id, session_tags)

        conn.execute(
            "UPDATE sessions SET extracted_through = ?, extracted_at = datetime('now') "
            "WHERE id = ?",
            (chunk[-1][0], session_id),
        )

        k_parts = []
        if topics_ins:
            k_parts.append(f"{topics_ins} topics")
        if stmts_ins:
            k_parts.append(f"{stmts_ins} statements")
        if stmts_sup:
            k_parts.append(f"{stmts_sup} superseded")
        if topics_upd:
            k_parts.append(f"{topics_upd} topics updated")
        t_parts = []
        if tasks_ins:
            t_parts.append(f"{tasks_ins} tasks")
        if updates_ins:
            t_parts.append(f"{updates_ins} updates")
        if tasks_upd:
            t_parts.append(f"{tasks_upd} modified")
        record_message(
            conn,
            session_id,
            "system",
            f"Extraction: knowledge={_summary(k_parts, 'no changes')}, "
            f"{_summary(t_parts, 'no tasks')}",
        )
    return topics_ins, stmts_ins, stmts_sup, topics_upd, tasks_ins, updates_ins, tasks_upd


def extract_chunk(session_id, token, chunk, part, cfg):
    """Extract one chunk of a session and store the results.

    The results, the session's new watermark and an "Extraction:" system
    message are written in one transaction, and only while *token* still holds
    the session's lease. Returns True if they were stored.
    """
    with open_db() as conn:
        day = conn.execute(
            "SELECT date(timestamp, 'localtime') FROM messages WHERE id = ?", (chunk[0][0],)
        ).fetchone()[0]
    try:
        result, _ = asyncio.run(
            call_model(
                build_extraction_prompt(chunk, session_id, part, day),
                allowed_tools=["Bash"],
                output_format={"type": "json_schema", "schema": EXTRACTION_SCHEMA},
            )
        )
    except Exception as e:
        log("extraction_error", session_id, context="SDK call", error=str(e))
        return False

    if not isinstance(result, dict):
        log(
            "extraction_error",
            session_id,
            context="unexpected type",
            error=str(type(result)),
        )
        return False

    try:
        counts = _store(session_id, token, chunk, result, cfg)
    except Exception as e:
        log("extraction_error", session_id, context="store", error=str(e))
        return False
    if counts is None:
        log("extraction_skipped", session_id, reason="lease lost")
        return False
    topics_ins, stmts_ins, stmts_sup, topics_upd, tasks_ins, updates_ins, tasks_upd = counts

    if topics_ins or stmts_ins or stmts_sup or topics_upd:
        k_data = {}
        if topics_ins:
            k_data["topics_inserted"] = topics_ins
        if stmts_ins:
            k_data["stmts_inserted"] = stmts_ins
        if stmts_sup:
            k_data["stmts_superseded"] = stmts_sup
        if topics_upd:
            k_data["topics_updated"] = topics_upd
        log("knowledge", session_id, **k_data)

    if tasks_ins or updates_ins or tasks_upd:
        t_data = {}
        if tasks_ins:
            t_data["inserted"] = tasks_ins
        if updates_ins:
            t_data["updates_inserted"] = updates_ins
        if tasks_upd:
            t_data["tasks_updated"] = tasks_upd
        log("tasks", session_id, **t_data)

    analysis_data = {"messages": len(chunk), "part": f"{part[0]}/{part[1]}"}
    if result.get("session_tags"):
        tags = result["session_tags"]
        analysis_data["session_tags"] = tags if isinstance(tags, list) else [tags]
    if result.get("tasks"):
        analysis_data["tasks"] = len(result["tasks"])
    log("analysis", session_id, **analysis_data)
    return True


def _claim(session_id, wait):
    """Take the session's lease, waiting up to LEASE_MINUTES for a running
    worker to let go when *wait*. Returns the token, or None."""
    deadline = time.monotonic() + (LEASE_MINUTES * 60 + CLAIM_POLL_SECONDS if wait else 0)
    while True:
        with open_db() as conn:
            token = claim_session(conn, session_id)
            if token is not None:
                return token
            if not conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone():
                return None  # nothing recorded: no session to extract
        if time.monotonic() >= deadline:
            log("extraction_skipped", session_id, reason="session already being extracted")
            return None
        time.sleep(CLAIM_POLL_SECONDS)


def extract_session(session_id, cfg=None, transcript_path=None, final=True, wait=False):
    """Extract the messages the session recorded since its last extraction.

    Takes the session's lease first, so two workers never extract the same
    session at once; with *wait*, it waits for a running worker to finish
    instead of leaving. Works on the messages pending when it starts, in one
    pass. Unless *final*, a prompt still being answered is left for later.
    Prompts in *transcript_path* that the user didn't write are relabelled
    first. Returns the number of chunks stored.
    """
    cfg = cfg or get_config()
    token = _claim(session_id, wait)
    if token is None:
        return 0

    stored = 0
    try:
        with open_db() as conn:
            relabel_agent_prompts(conn, session_id, agent_prompts(transcript_path))
            messages = pending_messages(conn, session_id)
        if not final:
            messages = answered(messages)
        chunks = chunk_messages(messages)
        for n, chunk in enumerate(chunks, 1):
            with open_db() as conn:
                token = renew_claim(conn, session_id, token)
            if token is None or not extract_chunk(
                session_id, token, chunk, (n, len(chunks)), cfg
            ):
                break
            stored += 1
        return stored
    finally:
        if token is not None:
            with open_db() as conn:
                release_claim(conn, session_id, token)


def mark_extracted(conn, session_id):
    """Count everything the session recorded as extracted, without a model call."""
    conn.execute(
        "UPDATE sessions SET extracted_through = MAX(COALESCE(extracted_through, 0), "
        "COALESCE((SELECT MAX(id) FROM messages WHERE session_id = ?), 0)), "
        "extracted_at = datetime('now') WHERE id = ?",
        (session_id, session_id),
    )
    conn.commit()


def _transcript(data, session_id):
    """Path of *session_id*'s transcript: Claude Code keeps a project's
    transcripts side by side, named after the session."""
    path = data.get("transcript_path")
    if not path:
        return None
    path = os.path.join(os.path.dirname(path), f"{session_id}.jsonl")
    return path if os.path.exists(path) else None


def _recently_written(path, minutes):
    try:
        return time.time() - os.path.getmtime(path) < minutes * 60
    except (OSError, TypeError):
        return False


def _schema_ready(session_id):
    """Make sure the DB has this version's schema, migrating it if the
    session's own start couldn't (another session held the DB)."""
    with open_db() as conn:
        if get_schema_version(conn) == SCHEMA_VERSION:
            return True
    import preflight

    with contextlib.redirect_stdout(io.StringIO()):
        status = preflight.ensure_schema()
    if status not in ("fresh", "current", "migrated"):
        log("extraction_error", session_id, context="schema",
            error=f"database not at schema v{SCHEMA_VERSION} ({status})")
        return False
    return True


def _run(data):
    """Detached worker: extract the hook's session, or catch up earlier ones.

    SessionEnd extracts everything, waiting for a PreCompact worker that is
    still running; PreCompact leaves the prompt being answered for later.
    SessionStart passes ``catch_up`` with the session that is starting, which
    is left alone, as is any session whose transcript is still being written.
    With analysis off, the session is marked extracted, so turning it back on
    doesn't extract everything recorded in between.
    """
    session_id = data.get("session_id")
    if not _schema_ready(session_id):
        return
    cfg = get_config()
    if not cfg["analysis"]:
        if session_id and not data.get("catch_up"):
            with open_db() as conn:
                mark_extracted(conn, session_id)
        return

    if data.get("catch_up"):
        with open_db() as conn:
            pending = pending_sessions(conn, exclude=session_id)
        for sid in pending:
            path = _transcript(data, sid)
            if _recently_written(path, CATCH_UP_IDLE_MINUTES):
                continue
            extract_session(sid, cfg, path)
    elif session_id:
        precompact = data.get("hook_event_name") == "PreCompact"
        extract_session(
            session_id, cfg, data.get("transcript_path"),
            final=not precompact, wait=not precompact,
        )


if __name__ == "__main__":
    run_detached_or_inline(__file__, _run)
