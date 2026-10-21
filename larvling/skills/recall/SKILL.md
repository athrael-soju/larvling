---
name: recall
description: Search or list knowledge stored in Larvling's memory
argument-hint: "[search term]"
---

**Schema:**
- `topics (id INTEGER PK AUTO, title TEXT NOT NULL, domain TEXT NOT NULL, tags TEXT NOT NULL, created TEXT, updated TEXT)`
- `statements (id INTEGER PK AUTO, topic_id INTEGER FK→topics(id), claim TEXT NOT NULL, created TEXT, updated TEXT, source TEXT [user|agent|doc|manual], kind TEXT [fact|decision|preference|assessment], session_id TEXT FK→sessions(id))`

Search for relevant knowledge by keyword, topic, or any available context. Use JOINs to show topic context with statements.

Run SQL via:
```
$PY "${CLAUDE_PLUGIN_ROOT}/scripts/query.py" "<SQL>"
```

## Output Format

Group results by topic with clear hierarchy:

```
**Topic: Python Discovery** (technical) — tags: python, portability
  - [13] Larvling skills use `$PY` instead of hardcoded python3
  - [16] `python3` breaks Windows, `python` breaks macOS

**Topic: Migration Strategy** (technical) — tags: sqlite, schema
  - [6] Simple additive changes only require bumping SCHEMA_VERSION
```

If no argument given, list all topics with statement counts. If no results found, say so briefly.

## Final Step

**REQUIRED:** You MUST call AskUserQuestion (type: Decision) with these options after presenting results. Do not end your response without this menu:
- **Narrow search** — refine with a more specific keyword
- **Remember something** — store new knowledge related to these results
- **Forget an item** — remove a statement or topic from the results
