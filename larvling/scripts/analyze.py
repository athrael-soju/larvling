"""
Unified exchange analysis — Stop command hook.

Reads the transcript, parses the last exchange, calls Sonnet (via sdk.py)
to identify knowledge, session tags, and tasks in a single
SDK call, then writes results to SQLite.
"""

import asyncio
import json
import os
import re
import sys
import unicodedata

from config import get_config
from db import (
    open_db,
    has_table,
    ensure_session,
    record_message,
    log,
)
from hooks_util import run_detached_or_inline
from sdk import call_model
from transcript import parse_last_user_text, parse_last_turn, wait_for_transcript_stable


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


# ---------------------------------------------------------------------------
# Analysis prompt and schema
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """\
Analyze this conversation exchange and extract structured data.

USER said: {user_text}

AGENT responded: {agent_text}

## Database access

Query the database to check for existing data before deciding actions:

{python} "{query_script}" "<SQL>" --read-only

Six tables in 3 parent→child pairs:
- `topics` (id INTEGER PK, title, domain, tags, created, updated)
- `statements` (id INTEGER PK, topic_id INTEGER FK→topics(id), claim, created, updated, source, kind, session_id)
- `tasks` (id INTEGER PK, title, domain, status, priority, horizon, metadata, created)
- `updates` (id INTEGER PK, task_id INTEGER FK→tasks(id), content, timestamp)
- `sessions` (id TEXT PK, started_at, ended_at, duration_min, title, agent_summary, exchange_count, summary_at, summary_msg_count, tags)
- `messages` (id INTEGER PK, session_id TEXT FK, timestamp, role, content, metadata)

## Workflow — follow these three phases in order

### Phase 1 — Identify candidates

Read the exchange and list what is worth extracting. Do NOT assign actions yet.

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
  - Ask: "Is this useful?" If not, do not include it.
  - Prefer fewer, higher-quality items over many low-value ones.
- **Task candidates**: durable commitments and TODOs that outlive this exchange, \
plus progress on — or completion of — existing tasks. If the exchange shows an open \
task was finished, that is an `update_task → done` candidate; record it so completed \
work leaves the open list instead of lingering until the next tidy.
- **Session tags**: topics/themes from this exchange (1-4 words each, max ~8 tags).

### Phase 2 — Dedup check (mandatory)

For EACH knowledge candidate, run at least one query against topics and statements \
to check for overlap. Use LIKE queries on title and claim columns.

For EACH task candidate, run at least one query against tasks and their updates \
to check for overlap — and query ALL statuses, not just open ones \
(e.g. `SELECT id, title, status FROM tasks WHERE title LIKE '%...%'`). A task with \
`status='dropped'` was deliberately retired, and one with `status='done'` is already \
complete: treat both as tombstones. If a candidate resembles a dropped or done task, \
`skip` it — never recreate or reopen retired work unless the user explicitly asked for \
it again in this exchange.

For session tags, query the current session's tags:
`SELECT tags FROM sessions WHERE id = '{session_id}'`

This step is mandatory — skipping it creates duplicates. You must run the queries \
before proceeding to Phase 3.

### Phase 3 — Decide actions

Based on your query results from Phase 2, assign an action for each candidate.

**Knowledge actions:**
- **add_topic**: no existing overlap — create a new topic with its first statement
- **add_statement**: related topic exists — add a new statement to it (include "topic_id")
- **update_statement**: existing statement needs refinement (include "statement_id")
- **update_topic**: existing topic title/domain/tags need updating (include "topic_id")
- **skip**: knowledge already exists unchanged — do NOT include it in the output

Never delete data. To retire knowledge, update the statement or topic instead.

**Task actions:**
- **add_task**: a durable commitment that outlives this exchange and is not tracked \
in ANY status — create a new task. Do NOT mint a task for a passing aside or an \
intention that was already resolved within this same exchange, and never create one \
that matches a `dropped` or `done` task (that resurrects retired work — `skip` instead)
- **add_update**: genuinely new context, progress, or notes about an existing task (include "task_id"). If the same fact is already recorded in updates, skip instead.
- **update_task**: change status/priority/horizon on an existing task (include "task_id"); also records the reason as an update entry
- **skip**: task already tracked and no new information — do NOT include it in the output

**Session tags:** return the FULL updated list — merge similar tags, drop tags no longer \
relevant, and add new tags from this exchange. If current tags is empty, just return \
new tags from this exchange.

## Output schema

Each knowledge item: {{"topic_title": "...", "claim": "...", "domain": "...", "tags": "...", "source": "user|agent", "kind": "fact|decision|preference|assessment", "about": "user|domain", "evidence": "...", "endorsement": "...", "action": "add_topic|add_statement|update_statement|update_topic", "topic_id": N, "statement_id": N}}
- Domains: personal, professional, preferences, interests, knowledge, technical, workflow
- Tags: short topic label (e.g. "octopuses", "physics", "python")
- source: whose words the claim comes from: "user" (the USER's message) or "agent" (the AGENT's response)
- kind: "fact" (something true: about the user, their work or the people they deal \
with, or general domain knowledge), "decision" (a choice the user has made), \
"preference" (how the user likes things done, or their opinion), "assessment" (an \
AGENT judgement the USER explicitly agreed with — nothing else)
- about: "user" (the user, their work, or the people they deal with) or "domain" \
(general knowledge, true no matter who is asking)
- evidence: a span of at least three words copied word for word from the source's \
message that supports the claim. Do not paraphrase: an item whose evidence is not in \
that message, or is shorter than three words, is discarded.
- endorsement: for kind "assessment" only, the USER's words agreeing with it, at least \
two words copied word for word; otherwise ""
- Allowed: source "user" with kind fact, decision or preference; source "agent" with \
kind "fact" and about "domain", or kind "assessment" with an endorsement. Anything else \
is discarded.

Each task item: {{"title": "...", "domain": "...", "priority": "low|medium|high", "horizon": "now|soon|later", "status": "open|done|dropped", "action": "add_task|add_update|update_task", "task_id": N, "content": "..."}}
- "content" is used for add_update (the fact/note) and update_task (the reason for the change)
- domain: same as knowledge domains
- priority: how important (low/medium/high)
- horizon: when to act (now/soon/later)
- status: open (default), done (completed), dropped (no longer relevant)

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
                            "update_statement",
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
                },
                "required": ["action"],
            },
        },
    },
    "required": ["knowledge", "session_tags", "tasks"],
}


def build_extraction_prompt(user_text, agent_text, session_id=""):
    """Format the extraction prompt with the exchange text."""
    query_script = os.path.join(os.path.dirname(__file__), "query.py")
    return EXTRACTION_PROMPT.format(
        user_text=user_text,
        agent_text=agent_text,
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


# Kana and CJK ideographs are written without spaces, so each character counts
# as a word; otherwise a whole clause would be one word.
_UNSPACED = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_WORD = re.compile(f"[{_UNSPACED}]|[^\\W{_UNSPACED}]+")


def _words(text):
    """Return the lowercase words of *text*, ignoring punctuation and spacing."""
    return " ".join(_WORD.findall(unicodedata.normalize("NFKC", text or "").casefold()))


def _quoted_in(quote, text):
    """True when *quote* appears word for word in *text*."""
    words = _words(quote)
    return bool(words) and f" {words} " in f" {_words(text)} "


def _parse_provenance(item, action, session_id, user_text, agent_text):
    """Check an item's source, kind and evidence.

    The evidence must appear in the source's message, and an assessment also
    needs the user's endorsement. Returns (source, kind) or None (with logging).
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
    # These checks prove only that the quotes are in the exchange, not that
    # they support the claim; the minimum length stops a stray word passing.
    evidence = _field(item, "evidence")
    if not _quoted_in(evidence, user_text if source == "user" else agent_text):
        _skip(session_id, action, f"evidence not in {source} text", evidence=evidence[:80])
        return None
    if len(_words(evidence).split()) < MIN_EVIDENCE_WORDS:
        _skip(session_id, action, "evidence too short", evidence=evidence[:80])
        return None
    if kind == "assessment":
        endorsement = _field(item, "endorsement")
        if not _quoted_in(endorsement, user_text):
            _skip(session_id, action, "assessment not endorsed by user")
            return None
        if len(_words(endorsement).split()) < MIN_ENDORSEMENT_WORDS:
            _skip(session_id, action, "endorsement too short", endorsement=endorsement[:80])
            return None
    return source, kind


def process_knowledge(conn, knowledge_list, session_id=None, user_text="", agent_text=""):
    """Process extracted knowledge into topics+statements tables.

    Handles 4 actions: add_topic, add_statement, update_statement, update_topic.
    Each statement written gets a source, kind and session_id; updating a
    statement replaces them.
    *user_text* and *agent_text* are the exchange the items came from, used
    to check each item's evidence.
    Never deletes data — retirement is done via updates, not removal.
    Returns (topics_inserted, stmts_inserted, stmts_updated, topics_updated).
    """
    if not knowledge_list or not has_table(conn, "topics"):
        return 0, 0, 0, 0

    topics_inserted = 0
    stmts_inserted = 0
    stmts_updated = 0
    topics_updated = 0

    for item in knowledge_list:
        action = _field(item, "action").lower()
        if action not in (
            "add_topic",
            "add_statement",
            "update_statement",
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

        if action == "update_statement":
            stmt_id = _parse_id(
                item.get("statement_id"), "statement_id", action, session_id
            )
            if stmt_id is None:
                continue
            claim = _field(item, "claim")
            if not claim:
                _skip(session_id, action, "missing claim")
                continue
            provenance = _parse_provenance(item, action, session_id, user_text, agent_text)
            if provenance is None:
                continue
            if not conn.execute(
                "SELECT 1 FROM statements WHERE id = ?", (stmt_id,)
            ).fetchone():
                _skip(session_id, action, "statement not found", statement_id=stmt_id)
                continue
            conn.execute(
                "UPDATE statements SET claim = ?, source = ?, kind = ?, session_id = ?, "
                "updated = datetime('now') WHERE id = ?",
                (claim, *provenance, session_id, stmt_id),
            )
            stmts_updated += 1
            continue

        if action == "add_statement":
            topic_id = _parse_id(item.get("topic_id"), "topic_id", action, session_id)
            if topic_id is None:
                continue
            claim = _field(item, "claim")
            if not claim:
                _skip(session_id, action, "missing claim")
                continue
            provenance = _parse_provenance(item, action, session_id, user_text, agent_text)
            if provenance is None:
                continue
            if not conn.execute(
                "SELECT 1 FROM topics WHERE id = ?", (topic_id,)
            ).fetchone():
                _skip(session_id, action, "topic not found", topic_id=topic_id)
                continue
            # Exact-match dedup safety net
            if conn.execute(
                "SELECT 1 FROM statements WHERE topic_id = ? AND claim = ?",
                (topic_id, claim),
            ).fetchone():
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
        provenance = _parse_provenance(item, action, session_id, user_text, agent_text)
        if provenance is None:
            continue

        # Exact-match dedup on claim
        if conn.execute(
            "SELECT 1 FROM statements WHERE claim = ?", (claim,)
        ).fetchone():
            continue

        cur = conn.execute(
            "INSERT INTO topics (title, domain, tags) VALUES (?, ?, ?)",
            (topic_title, domain, tags),
        )
        topic_id = cur.lastrowid
        conn.execute(
            "INSERT INTO statements (topic_id, claim, source, kind, session_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (topic_id, claim, *provenance, session_id),
        )
        topics_inserted += 1
        stmts_inserted += 1

    return topics_inserted, stmts_inserted, stmts_updated, topics_updated


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

        # Dedup: skip if a task with this title exists in ANY status. An open match
        # is a duplicate; a 'dropped' or 'done' match is a tombstone — re-adding it
        # would resurrect retired work. Reopening must go through update_task, never
        # an automatic insert. (Prompt enforces this fuzzily; this is the hard floor.)
        if conn.execute(
            "SELECT 1 FROM tasks WHERE title = ? AND status IN ('open', 'dropped', 'done')",
            (title,),
        ).fetchone():
            continue

        if session_id:
            metadata = json.dumps({"source_session_id": session_id})
            conn.execute(
                "INSERT INTO tasks (title, domain, priority, horizon, metadata) VALUES (?, ?, ?, ?, ?)",
                (title, domain, priority, horizon, metadata),
            )
        else:
            conn.execute(
                "INSERT INTO tasks (title, domain, priority, horizon) VALUES (?, ?, ?, ?)",
                (title, domain, priority, horizon),
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
# Main
# ---------------------------------------------------------------------------


def _run(data):
    """Detached worker — called by run_detached_or_inline after payload parsing."""
    if data.get("stop_hook_active"):
        return  # Prevent recursive hook firing

    cfg = get_config()
    if not cfg["analysis"]:
        return

    session_id = data.get("session_id")
    transcript_path = data.get("transcript_path")

    # Wait for transcript to finish writing before parsing
    wait_for_transcript_stable(transcript_path)

    # Get user text and agent text. A turn the user didn't start (a task
    # notification) has no user text.
    user_text = parse_last_user_text(transcript_path) or ""
    agent_text = parse_last_turn(transcript_path)[0] or ""

    if not user_text and not agent_text:
        log("extraction_skipped", session_id, reason="no text found")
        return

    try:
        prompt = build_extraction_prompt(user_text, agent_text, session_id)
        result, usage_info = asyncio.run(
            call_model(
                prompt,
                allowed_tools=["Bash"],
                output_format={"type": "json_schema", "schema": EXTRACTION_SCHEMA},
            )
        )
    except Exception as e:
        log("extraction_error", session_id, context="SDK call", error=str(e))
        return

    if not isinstance(result, dict):
        log(
            "extraction_error",
            session_id,
            context="unexpected type",
            error=str(type(result)),
        )
        return

    with open_db() as conn:
        # Ensure session row exists before writing session-scoped data
        if session_id:
            ensure_session(conn, session_id)

        # Knowledge (topics + statements)
        topics_ins = stmts_ins = stmts_upd = topics_upd = 0
        if cfg["knowledge_extraction"]:
            knowledge = result.get("knowledge", [])
            topics_ins, stmts_ins, stmts_upd, topics_upd = process_knowledge(
                conn, knowledge, session_id, user_text, agent_text
            )

        # Tasks
        tasks_ins = updates_ins = tasks_upd = 0
        if cfg["task_tracking"]:
            tasks_list = result.get("tasks", [])
            tasks_ins, updates_ins, tasks_upd = process_tasks(
                conn, tasks_list, session_id
            )

        # Session tags
        if cfg["session_tags"]:
            session_tags = result.get("session_tags", [])
            if session_id and isinstance(session_tags, list):
                store_tags(conn, session_id, session_tags)

        # Record extraction as a system message
        if session_id:
            k_parts = []
            if topics_ins:
                k_parts.append(f"{topics_ins} topics")
            if stmts_ins:
                k_parts.append(f"{stmts_ins} statements")
            if stmts_upd or topics_upd:
                k_parts.append(f"{stmts_upd + topics_upd} updated")
            k_summary = ", ".join(k_parts) if k_parts else "no changes"

            t_parts = []
            if tasks_ins:
                t_parts.append(f"{tasks_ins} tasks")
            if updates_ins:
                t_parts.append(f"{updates_ins} updates")
            if tasks_upd:
                t_parts.append(f"{tasks_upd} modified")
            t_summary = ", ".join(t_parts) if t_parts else "no tasks"

            sys_content = f"Extraction: knowledge={k_summary}, {t_summary}"
            sys_meta = None
            record_message(conn, session_id, "system", sys_content, sys_meta)

        conn.commit()

    if topics_ins or stmts_ins or stmts_upd or topics_upd:
        k_data = {}
        if topics_ins:
            k_data["topics_inserted"] = topics_ins
        if stmts_ins:
            k_data["stmts_inserted"] = stmts_ins
        if stmts_upd:
            k_data["stmts_updated"] = stmts_upd
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

    analysis_data = {}
    if result.get("session_tags"):
        tags = result["session_tags"]
        analysis_data["session_tags"] = tags if isinstance(tags, list) else [tags]
    if result.get("tasks"):
        analysis_data["tasks"] = len(result["tasks"])

    log("analysis", session_id, **analysis_data)


if __name__ == "__main__":
    run_detached_or_inline(__file__, _run)
