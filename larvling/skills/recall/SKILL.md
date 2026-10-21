---
name: recall
description: Search or list knowledge stored in Larvling's memory
argument-hint: "[search term]"
---

**Schema:**
- `topics (id INTEGER PK AUTO, title TEXT NOT NULL, domain TEXT NOT NULL, tags TEXT NOT NULL, created TEXT, updated TEXT)`
- `statements (id INTEGER PK AUTO, topic_id INTEGER FK→topics(id), claim TEXT NOT NULL, created TEXT, updated TEXT, source TEXT [user|agent|doc|manual], kind TEXT [fact|decision|preference|assessment], session_id TEXT FK→sessions(id), superseded_by INTEGER FK→statements(id), superseded_at TEXT)`
- `current_statements` — view: the statements still in force (`superseded_at IS NULL`), same columns

Search with `search.py`. It ranks matches on any of the words, in claims and topic titles, in one query (best match first), and only returns current statements: superseded or retired ones are left out.
```
$PY "${CLAUDE_PLUGIN_ROOT}/scripts/search.py" "<search terms>"
```
If the first search misses, try other wordings or related words before saying nothing is stored.

Assessments are left out by default: they are agent judgements the user agreed with, not established facts. Add `--assessments` only when the user asks for opinions, recommendations or assessments, or for everything; then mark each one `(assessment)`.

For anything search can't do — listing topics, history, aggregates — run SQL against `current_statements` (`kind IS NOT 'assessment'` keeps older statements whose `kind` is NULL). Only when the user asks how something changed, show the history from `statements`: the rows with `superseded_by = <id>` are the claims a statement replaced.
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
