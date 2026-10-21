---
name: tidy
description: Audit and consolidate Larvling's knowledge base, tasks, and sessions
---

**Schema:**
- `topics (id INTEGER PK AUTO, title TEXT NOT NULL, domain TEXT NOT NULL, tags TEXT NOT NULL, created TEXT, updated TEXT)`
- `statements (id INTEGER PK AUTO, topic_id INTEGER FK→topics(id), claim TEXT NOT NULL, created TEXT, updated TEXT, source TEXT [user|agent|doc|manual], kind TEXT [fact|decision|preference|assessment], session_id TEXT FK→sessions(id), superseded_by INTEGER FK→statements(id), superseded_at TEXT)`
- `current_statements` — view: the statements still in force (`superseded_at IS NULL`), same columns
- `tasks (id INTEGER PK AUTO, title TEXT NOT NULL, domain TEXT NOT NULL, status TEXT [open|done|dropped], priority TEXT [low|medium|high], horizon TEXT [now|soon|later], metadata TEXT, created TEXT, updated TEXT [last touched], due TEXT [YYYY-MM-DD])`
- `updates (id INTEGER PK AUTO, task_id INTEGER FK→tasks(id), content TEXT NOT NULL, timestamp TEXT)`
- `sessions (id TEXT PK, started_at TEXT, ended_at TEXT, duration_min REAL, title TEXT, agent_summary TEXT, exchange_count INT, tags TEXT, ...)`

Run SQL via:
```
$PY "${CLAUDE_PLUGIN_ROOT}/scripts/query.py" "<SQL>"
```

Create tasks to track the audit phases so the user sees progress:
1. **Audit knowledge base** (activeForm: "Auditing knowledge base") — topics + statements
2. **Audit tasks** (activeForm: "Auditing tasks") — open tasks, duplicates, stale items
3. **Audit sessions** (activeForm: "Auditing sessions") — untitled, empty, missing summaries
4. **Apply approved changes** (activeForm: "Applying changes") — mark complete after agent finishes

Delegate the full audit to the `knowledge-maintenance` agent, telling it this session's ID (`${CLAUDE_SESSION_ID}`) for the statements it writes. It will inspect each area, identify issues (duplicates, stale entries, misclassifications, contradictions, orphaned or empty rows), propose changes via AskUserQuestion, and apply only what the user approves.

Once the agent completes, present a brief before/after summary. Do not send follow-up messages to the agent after it finishes.

## Output Format

```
**Maintenance Complete**
- Knowledge: merged 2 duplicate topics, updated 3 classifications
- Tasks: dropped 4 stale open tasks, merged 1 duplicate
- Sessions: retitled 2, flagged 1 empty for cleanup
- Before: 12 topics, 45 statements, 18 tasks (9 open), 30 sessions
- After: 10 topics, 45 statements, 18 tasks (5 open), 30 sessions
```

## Final Step

**REQUIRED:** You MUST call AskUserQuestion (type: Decision) with these options after presenting the maintenance summary. Do not end your response without this menu:
- **View knowledge** — recall the updated knowledge base
- **Run again** — re-audit after changes were applied
- **Done** — no further action needed
