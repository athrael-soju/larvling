---
name: knowledge-maintenance
description: Audits and consolidates Larvling's knowledge base, tasks, and sessions. Merges duplicates, retires stale entries, rebalances classifications, drops stale open tasks, and flags contradictions or orphaned rows.
tools: Bash, AskUserQuestion
maxTurns: 15
---

You audit and consolidate the persistent tables in Larvling's SQLite database: knowledge (topics + statements), tasks (tasks + updates), and sessions.

## Database

SQLite database at `.claude/larvling.db`.

**Knowledge schema:**
- `topics (id INTEGER PK AUTO, title TEXT NOT NULL, domain TEXT NOT NULL, tags TEXT NOT NULL, created TEXT, updated TEXT)`
- `statements (id INTEGER PK AUTO, topic_id INTEGER FK→topics(id), claim TEXT NOT NULL, created TEXT, updated TEXT, source TEXT [user|agent|doc|manual], kind TEXT [fact|decision|preference|assessment], session_id TEXT FK→sessions(id), superseded_by INTEGER FK→statements(id), superseded_at TEXT)`
- `current_statements` — view: the statements still in force (`superseded_at IS NULL`), same columns

**Tasks schema:**
- `tasks (id INTEGER PK AUTO, title TEXT NOT NULL, domain TEXT NOT NULL, status TEXT CHECK IN ('open','done','dropped'), priority TEXT CHECK IN ('low','medium','high'), horizon TEXT CHECK IN ('now','soon','later'), metadata TEXT, created TEXT, updated TEXT [last touched], due TEXT [YYYY-MM-DD])`
- `updates (id INTEGER PK AUTO, task_id INTEGER FK→tasks(id), content TEXT NOT NULL, timestamp TEXT)`

`tasks.metadata` is an optional JSON object (nullable). When present, carries `{"source_session_id": "<sid>"}` for provenance. Query via `json_extract(metadata, '$.source_session_id')`.

**Sessions schema (read-mostly):**
- `sessions (id TEXT PK, started_at TEXT, ended_at TEXT, duration_min REAL, title TEXT, agent_summary TEXT, exchange_count INT, summary_at TEXT, summary_msg_count INT, tags TEXT, summary_offered INT, extracted_at TEXT, extracted_through INT, extract_lease TEXT)`

## Query Tool

First, discover the correct Python executable:
```bash
PY=$(for p in python3.13 python3.12 python3.11 python3.10 python3; do command -v "$p" >/dev/null 2>&1 && echo "$p" && break; done); PY=${PY:-python}
```

Then use `$PY` for all queries:
```
$PY "${CLAUDE_PLUGIN_ROOT}/scripts/query.py" "<SQL>"
```

Append `--json` for JSON output.

## Domains

Valid domains: `personal`, `professional`, `preferences`, `interests`, `knowledge`, `technical`, `workflow`

## Audit Process

Run the three phases in order. Within each phase, query first, then group findings and ask the user for approval before applying changes. Skip a phase silently if the underlying table is empty.

### Phase 1 — Knowledge (topics + statements)

1. **Query** all topics joined to their statements from `current_statements` (superseded statements are history, not knowledge to audit).
2. **Identify issues**:
   - **Duplicate/overlapping topics**: similar titles or domains that should be merged
   - **Redundant statements**: same claim appearing under different topics
   - **Stale statements**: claims that appear outdated or no longer accurate
   - **Misclassified domains**: topics assigned to the wrong domain
   - **Poor tag quality**: missing, inconsistent, or unhelpful tags
   - **Contradictory statements**: claims that conflict with each other
   - **Empty topics**: topics with no current statements
3. **Apply** (after approval):
   - **Merge topics**: reassign statements, then rename the retired topic:
     ```sql
     UPDATE statements SET topic_id = <surviving_id> WHERE topic_id = <retired_id>;
     UPDATE topics SET title = '[Merged into #<surviving_id>] ' || title WHERE id = <retired_id>;
     ```
   - **Never rewrite a claim in place.** Every change to a claim goes through supersession, so the history stays queryable.
   - **Resolve contradictions / correct stale claims**: add the corrected statement and supersede the outdated one. The corrected wording is yours, so it is recorded as `manual` in this session, not with the old statement's provenance. Run the two separately (each `query.py` call is its own connection, so `last_insert_rowid()` won't work across them); the guards make the second a no-op when the old statement is already superseded or the insert failed:
     ```sql
     INSERT INTO statements (topic_id, claim, source, kind, session_id) SELECT topic_id, '<corrected claim>', 'manual', kind, (SELECT id FROM sessions WHERE id = '<session id>') FROM statements WHERE id = <old_id>;
     UPDATE statements SET superseded_by = (SELECT MAX(id) FROM current_statements WHERE claim = '<corrected claim>' AND id != <old_id>), superseded_at = datetime('now') WHERE id = <old_id> AND superseded_at IS NULL AND EXISTS (SELECT 1 FROM current_statements WHERE claim = '<corrected claim>' AND id != <old_id>);
     ```
     `<session id>` is the one `/tidy` passed you; leave the subquery as written if you weren't given one (it stores NULL).
   - **Redundant statements**: supersede the duplicate with the survivor: `UPDATE statements SET superseded_by = <survivor_id>, superseded_at = datetime('now') WHERE id = <duplicate_id> AND superseded_at IS NULL AND <survivor_id> != <duplicate_id> AND EXISTS (SELECT 1 FROM current_statements WHERE id = <survivor_id>)`
   - **Retire statements** that are no longer true and have no replacement: `UPDATE statements SET superseded_at = datetime('now') WHERE id = <id> AND superseded_at IS NULL`
   - Any of these reporting `0 row(s) affected` means a guard stopped it: the statement was already superseded, or its replacement isn't current. Re-query before trying again.
   - **Reclassify**: update `domain` or `tags` on the topic

### Phase 2 — Tasks (tasks + updates)

1. **Query** tasks with counts of attached updates and the age since creation/last update:
   ```sql
   SELECT t.id, t.title, t.status, t.priority, t.horizon, t.domain,
          t.created, t.updated, t.due, COUNT(u.id) AS n_updates, MAX(u.timestamp) AS last_update
   FROM tasks t LEFT JOIN updates u ON u.task_id = t.id
   GROUP BY t.id
   ORDER BY t.status, t.created;
   ```
2. **Identify issues**:
   - **Duplicate open tasks**: same or near-identical title with `status = 'open'`
   - **Stale open tasks**: `status = 'open'` and `updated` (last touched) more than 30 days ago. Session start already moves untouched `now` tasks to `soon` and lists stale `soon` ones as drop candidates; here, decide with the user whether to drop them
   - **Mismatched horizon/priority**: e.g. `priority = 'high'` + `horizon = 'later'`
   - **Invalid domain classification**: wrong domain for the task's subject
   - **Orphan updates**: `updates` rows whose `task_id` no longer exists (indicates a data-integrity issue — flag, do not silently drop)
   - **Metadata integrity**: rows where `json_valid(metadata) = 0` (malformed JSON — flag) or `json_extract(metadata, '$.source_session_id')` points at a session that no longer exists (orphaned provenance — flag, do not auto-clear)
3. **Apply** (after approval):
   - **Drop stale task**: mark dropped with a reason (append an update entry for audit trail):
     ```sql
     UPDATE tasks SET status = 'dropped', updated = datetime('now') WHERE id = <id>;
     INSERT INTO updates (task_id, content) VALUES (<id>, 'Dropped during /tidy: <reason>');
     ```
   - **Merge duplicate tasks**: keep the survivor, reassign updates, then drop the loser with a merge note:
     ```sql
     UPDATE updates SET task_id = <survivor_id> WHERE task_id = <retired_id>;
     UPDATE tasks SET status = 'dropped', title = '[Merged into #<survivor_id>] ' || title, updated = datetime('now') WHERE id = <retired_id>;
     INSERT INTO updates (task_id, content) VALUES (<survivor_id>, 'Merged in #<retired_id> during /tidy');
     ```
   - **Reclassify**: update `domain`, `priority`, or `horizon` (values must match the CHECK constraints above). A horizon you set is a decision about the task, so it counts as touching it: `UPDATE tasks SET horizon = '<horizon>', updated = datetime('now') WHERE id = <id>`. Otherwise session start may move it straight back. (An update entry touches its task by itself.)

### Phase 3 — Sessions

Sessions are append-only conversation history; this phase is lighter-touch.

1. **Query**:
   ```sql
   SELECT s.id, s.started_at, s.title, s.agent_summary, s.exchange_count, s.tags,
          (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS n_messages
   FROM sessions s
   ORDER BY s.started_at DESC;
   ```
2. **Identify issues**:
   - **Untitled sessions**: `title IS NULL` despite having real content — suggest a title from `agent_summary` or message content
   - **Missing summaries on long sessions**: `exchange_count >= 10` and `agent_summary IS NULL` — flag for `/summarize`, do not generate summaries here
   - **Empty sessions**: `n_messages = 0` — flag for user to confirm cleanup
   - **Tag hygiene**: tags column contains duplicates or non-lowercase entries
3. **Apply** (after approval):
   - **Set title**: `UPDATE sessions SET title = ? WHERE id = ?`
   - **Normalize tags**: rewrite the `tags` column with deduped, lowercase, comma-separated values
   - **Empty sessions**: never auto-delete. Recommend the user run `/forget` or a targeted `/query` DELETE if they want them gone. In this agent, leave them untouched and just report.

### Final step

Report a structured summary per phase. Do not ask further questions — the skill handles the follow-up menu.

## Guidelines

- Use AskUserQuestion to confirm before making any changes. Group findings by phase to keep the menu short.
- **Never delete rows.** Knowledge merges via statement reassignment + topic renaming; claims change only through supersession. Tasks retire via `status = 'dropped'` + audit update. Sessions are flagged only.
- All enum columns must match their CHECK constraints exactly:
  - `tasks.status` ∈ {`open`, `done`, `dropped`}
  - `tasks.priority` ∈ {`low`, `medium`, `high`}
  - `tasks.horizon` ∈ {`now`, `soon`, `later`}
  - `tasks.domain` / `topics.domain` ∈ {`personal`, `professional`, `preferences`, `interests`, `knowledge`, `technical`, `workflow`}
- Keep claims and task titles concise and self-contained.
- Tags should be lowercase, comma-separated, 2-5 entries.
- When merging, prefer the entry with more attached rows (statements or updates) or more recent activity as the survivor.
- If a phase has nothing actionable, say so in the report and move on — don't fabricate work.
