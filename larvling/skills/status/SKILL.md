---
name: status
description: Show a quick overview of Larvling's state
---

**Schema:**
- `sessions (id TEXT PK, started_at TEXT, ended_at TEXT, duration_min REAL, title TEXT, agent_summary TEXT, exchange_count INT, summary_at TEXT, summary_msg_count INT, tags TEXT, summary_offered INT DEFAULT 0, extracted_at TEXT, extracted_through INT, extract_lease TEXT)`
- `messages (id INT PK AUTO, session_id TEXT FK, timestamp TEXT, role TEXT, content TEXT, metadata TEXT)`
- `topics (id INTEGER PK AUTO, title TEXT NOT NULL, domain TEXT NOT NULL, tags TEXT NOT NULL, created TEXT, updated TEXT)`
- `statements (id INTEGER PK AUTO, topic_id INTEGER FK→topics(id), claim TEXT NOT NULL, created TEXT, updated TEXT, source TEXT [user|agent|doc|manual], kind TEXT [fact|decision|preference|assessment], session_id TEXT FK→sessions(id), superseded_by INTEGER FK→statements(id), superseded_at TEXT)`
- `current_statements` — view: the statements still in force (`superseded_at IS NULL`), same columns
- `tasks (id INTEGER PK AUTO, title TEXT NOT NULL, domain TEXT NOT NULL, status TEXT DEFAULT 'open', priority TEXT DEFAULT 'medium', horizon TEXT DEFAULT 'later', metadata TEXT, created TEXT, updated TEXT [last touched], due TEXT [YYYY-MM-DD])`
- `updates (id INTEGER PK AUTO, task_id INTEGER FK→tasks(id), content TEXT NOT NULL, timestamp TEXT)`

Gather and present a brief overview: session count, message count, topic count, statement count (current, from `current_statements`, plus how many were superseded), task count (open/done), DB file size, and plugin version (from `${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json`).

Use `COUNT`/`GROUP BY` aggregates for every metric — e.g. `SELECT status, COUNT(*) FROM tasks GROUP BY status`. Never `SELECT *` a table and tally the rows yourself; on a large table that scan is refused by the output cap (and it's wasteful regardless).

Run SQL via:
```
$PY "${CLAUDE_PLUGIN_ROOT}/scripts/query.py" "<SQL>"
```

## Output Format

Present as a compact dashboard:

```
**Larvling Status**

| Metric     | Value            |
|------------|------------------|
| Sessions   | 42               |
| Messages   | 318              |
| Topics     | 5 (3 domains)    |
| Statements | 22 (3 superseded) |
| Tasks      | 3 open, 1 done   |
| DB size    | 156 KB           |
| Version    | 0.1.21           |
```

## Final Step

**REQUIRED:** You MUST call AskUserQuestion (type: Decision) with these options after presenting the dashboard. Do not end your response without this menu:
- **Browse sessions** — view recent session history
- **View knowledge** — recall stored topics and statements
- **Check tasks** — list open tasks and recent updates
