"""
Unified exchange analysis — Stop command hook.

Reads the transcript, parses the last exchange, calls Sonnet (via sdk.py)
to identify knowledge, session tags, and tasks in a single
SDK call, then writes results to SQLite.
"""

import asyncio
import json
import os
import sys

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
# Extraction only sees the two speakers; 'doc' and 'manual' sources are
# written outside extraction (e.g. /remember writes 'manual').
VALID_SOURCE = {"user", "agent"}
VALID_KIND = {"fact", "decision", "preference", "assessment"}


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

- **Knowledge candidates**: durable facts worth remembering across sessions.
  - From USER: personal info, professional info, preferences, interests \
(asking about ANY topic = an interest), decisions, opinions, workflow habits
  - From AGENT: key domain knowledge shared with the user (science, history, \
concepts) — NOT code-level implementation details
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

Each knowledge item: {{"topic_title": "...", "claim": "...", "domain": "...", "tags": "...", "source": "user|agent", "kind": "fact|decision|preference|assessment", "action": "add_topic|add_statement|update_statement|update_topic", "topic_id": N, "statement_id": N}}
- Domains: personal, professional, preferences, interests, knowledge, technical, workflow
- Tags: short topic label (e.g. "octopuses", "physics", "python")
- source: who said it in this exchange: "user" (the USER's message) or "agent" (the AGENT's response)
- kind: "fact" (something true about the world, the user or their work), "decision" \
(a choice the user has made), "preference" (how the user likes things done), \
"assessment" (a judgement, recommendation or guess rather than an established fact)

Each task item: {{"title": "...", "domain": "...", "priority": "low|medium|high", "horizon": "now|soon|later", "status": "open|done|dropped", "action": "add_task|add_update|update_task", "task_id": N, "content": "..."}}
- "content" is used for add_update (the fact/note) and update_task (the reason for the change)
- domain: same as knowledge domains
- priority: how important (low/medium/high)
- horizon: when to act (now/soon/later)
- status: open (default), done (completed), dropped (no longer relevant)

Return JSON:
{{
  "knowledge": [{{"topic_title": "...", "claim": "...", "domain": "...", "tags": "...", "source": "user", "kind": "fact", "action": "add_topic"}}],
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


def _parse_provenance(item, action, session_id):
    """Validate an item's source and kind. Returns (source, kind) or None (with logging)."""
    source = item.get("source", "").strip().lower()
    if source not in VALID_SOURCE:
        _skip(session_id, action, "invalid source", source=source)
        return None
    kind = item.get("kind", "").strip().lower()
    if kind not in VALID_KIND:
        _skip(session_id, action, "invalid kind", kind=kind)
        return None
    return source, kind


def process_knowledge(conn, knowledge_list, session_id=None):
    """Process extracted knowledge into topics+statements tables.

    Handles 4 actions: add_topic, add_statement, update_statement, update_topic.
    Every statement written carries its provenance: source, kind and the
    session that produced it (an updated claim takes the updater's provenance).
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
        action = item.get("action", "").strip().lower()
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
            title = item.get("topic_title", "").strip()
            domain = item.get("domain", "").strip().lower()
            if domain and domain not in VALID_DOMAINS:
                _skip(session_id, action, "invalid domain", domain=domain)
                continue
            tags = item.get("tags", "").strip()
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
            claim = item.get("claim", "").strip()
            if not claim:
                _skip(session_id, action, "missing claim")
                continue
            provenance = _parse_provenance(item, action, session_id)
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
            claim = item.get("claim", "").strip()
            if not claim:
                _skip(session_id, action, "missing claim")
                continue
            provenance = _parse_provenance(item, action, session_id)
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
        claim = item.get("claim", "").strip()
        if not claim:
            _skip(session_id, action, "missing claim")
            continue
        topic_title = item.get("topic_title", "").strip()
        if not topic_title:
            _skip(session_id, action, "missing topic_title")
            continue
        domain = item.get("domain", "").strip().lower()
        if not domain or domain not in VALID_DOMAINS:
            _skip(session_id, action, "invalid domain", domain=domain)
            continue
        tags = item.get("tags", "").strip()
        if not tags:
            _skip(session_id, action, "missing tags")
            continue
        provenance = _parse_provenance(item, action, session_id)
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
        action = task.get("action", "").strip().lower()
        if action not in ("add_task", "add_update", "update_task"):
            continue

        if action == "add_update":
            task_id = _parse_id(task.get("task_id"), "task_id", action, session_id)
            if task_id is None:
                continue
            content = task.get("content", "").strip()
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
            content = task.get("content", "").strip()
            if not conn.execute(
                "SELECT 1 FROM tasks WHERE id = ?", (task_id,)
            ).fetchone():
                _skip(session_id, action, "task not found", task_id=task_id)
                continue

            # Build SET clause — skip invalid enum values
            sets = []
            params = []
            status = task.get("status", "").strip().lower()
            if status:
                if status not in VALID_STATUS:
                    _skip(session_id, action, "invalid status", status=status)
                    continue
                sets.append("status = ?")
                params.append(status)
            priority = task.get("priority", "").strip().lower()
            if priority:
                if priority not in VALID_PRIORITY:
                    _skip(session_id, action, "invalid priority", priority=priority)
                    continue
                sets.append("priority = ?")
                params.append(priority)
            horizon = task.get("horizon", "").strip().lower()
            if horizon:
                if horizon not in VALID_HORIZON:
                    _skip(session_id, action, "invalid horizon", horizon=horizon)
                    continue
                sets.append("horizon = ?")
                params.append(horizon)
            title = task.get("title", "").strip()
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
        title = task.get("title", "").strip()
        if not title:
            _skip(session_id, action, "missing title")
            continue
        domain = task.get("domain", "").strip().lower()
        if not domain or domain not in VALID_DOMAINS:
            _skip(session_id, action, "invalid domain", domain=domain)
            continue
        priority = task.get("priority", "").strip().lower()
        if not priority or priority not in VALID_PRIORITY:
            _skip(session_id, action, "invalid priority", priority=priority)
            continue
        horizon = task.get("horizon", "").strip().lower()
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

    # Get user text and agent text
    user_text = parse_last_user_text(transcript_path)
    agent_text, _ = parse_last_turn(transcript_path)

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
                conn, knowledge, session_id
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
