---
name: sessions
description: Browse and search past sessions
argument-hint: "[date, keyword, or topic]"
---

**Schema:**
- `sessions (id TEXT PK, started_at TEXT, ended_at TEXT, duration_min REAL, title TEXT, agent_summary TEXT, exchange_count INT, summary_at TEXT, summary_msg_count INT, tags TEXT, summary_offered INT DEFAULT 0, extracted_at TEXT, extracted_through INT, extract_lease TEXT)`
- `messages (id INT PK AUTO, session_id TEXT FK, timestamp TEXT, role TEXT, content TEXT, metadata TEXT)`

Search sessions by date, keyword, topic, or any available context. Search across session titles, summaries, tags, and message content as needed.

Run SQL via:
```
$PY "${CLAUDE_PLUGIN_ROOT}/scripts/query.py" "<SQL>"
```

## Output Format

Present as a compact list, most recent first:

```
**Sessions** (3 found)

| Date       | Duration | Summary                         | Tags          |
|------------|----------|---------------------------------|---------------|
| 2026-04-01 | 6.6m     | Fixed Python discovery pattern  | python, hooks |
| 2026-03-31 | 12.2m    | Schema migration for tasks      | sqlite, dev   |
| 2026-03-30 | 3.1m     | Quick status check              | greeting      |
```

For detailed view of a single session, show the summary and message highlights.

## Final Step

**REQUIRED:** You MUST call AskUserQuestion (type: Decision) with these options after presenting results. Do not end your response without this menu:
- **View details** — show full summary and message highlights for a specific session
- **Export session** — save a session as markdown
- **Summarize session** — generate or update a session summary
