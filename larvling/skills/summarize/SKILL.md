---
name: summarize
description: Generate or view a session summary
argument-hint: "[session-id or --list or all]"
---

**Schema:**
- `sessions (id TEXT PK, started_at TEXT, ended_at TEXT, duration_min REAL, title TEXT, agent_summary TEXT, exchange_count INT, summary_at TEXT, summary_msg_count INT, tags TEXT, summary_offered INT DEFAULT 0, extracted_at TEXT, extracted_through INT, extract_lease TEXT)`
- `messages (id INT PK AUTO, session_id TEXT FK, timestamp TEXT, role TEXT, content TEXT, metadata TEXT)`

Run SQL via:
```
$PY "${CLAUDE_PLUGIN_ROOT}/scripts/query.py" "<SQL>"
```

For batch summarization (e.g., "summarize all"), use TaskCreate to track progress per session.

Delegate to the `summary-manager` agent. Pass along the session ID if provided, or the current session if not.

Once the agent completes, present the summary briefly. Do not send follow-up messages to the agent after it finishes.

## Output Format

```
**Session Summary** (2026-04-01, 6.6m)
> Fixed Python discovery pattern across all skills. Updated CLAUDE.md with portable $PY approach.
```

## Final Step

**REQUIRED:** You MUST call AskUserQuestion (type: Decision) with these options after presenting the summary. Do not end your response without this menu:
- **Export this session** — save the full conversation as markdown
- **Summarize another** — generate a summary for a different session
- **Browse sessions** — view session list
