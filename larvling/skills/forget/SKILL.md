---
name: forget
description: Remove stored knowledge from Larvling's memory
argument-hint: "[topic/statement ID or keyword]"
disable-model-invocation: true
---

**Schema:**
- `topics (id INTEGER PK AUTO, title TEXT NOT NULL, domain TEXT NOT NULL, tags TEXT NOT NULL, created TEXT, updated TEXT)`
- `statements (id INTEGER PK AUTO, topic_id INTEGER FK→topics(id), claim TEXT NOT NULL, created TEXT, updated TEXT, source TEXT [user|agent|doc|manual], kind TEXT [fact|decision|preference|assessment], session_id TEXT FK→sessions(id), superseded_by INTEGER FK→statements(id), superseded_at TEXT)`
- `current_statements` — view: the statements still in force (`superseded_at IS NULL`), same columns

Find the knowledge to remove — by ID, keyword, or by asking the user to clarify. Search `current_statements` first; search `statements` too when the user names something that is only in the history.

A statement that corrected an earlier one keeps the earlier versions behind it, and forgetting a fact usually means its history too. For each match N, list the versions it replaced:
```sql
WITH RECURSIVE older(id) AS (SELECT id FROM statements WHERE superseded_by = N UNION SELECT s.id FROM statements s JOIN older o ON s.superseded_by = o.id) SELECT s.id, s.claim, s.superseded_at FROM statements s JOIN older USING (id)
```
Show the matches with their earlier versions and use AskUserQuestion to confirm before deleting, offering to delete the earlier versions too (the default). Delete them together: `DELETE FROM statements WHERE id IN (N, <earlier ids>)`. Deleting only N leaves the version before it retired (its `superseded_by` becomes NULL, `superseded_at` stays). When deleting the last current statement under a topic, delete the topic too, together with its superseded statements: `DELETE FROM statements WHERE topic_id = N`, then the topic.

Run SQL via:
```
$PY "${CLAUDE_PLUGIN_ROOT}/scripts/query.py" "<SQL>"
```

## Output Format

Show what will be removed clearly before confirming:

```
Found 1 match:

**Topic: Python Discovery** (technical)
  - [16] `python3` breaks Windows, `python` breaks macOS
    - earlier version [9]: `python3` breaks Windows

Remove this statement and its earlier version?
```

## Final Step

**REQUIRED:** You MUST call AskUserQuestion (type: Decision) with these options after deletion is executed. Do not end your response without this menu:
- **Forget more** — remove another item
- **View remaining** — show what's left in the affected topic
- **Done** — no further action needed
