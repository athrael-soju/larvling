---
name: forget
description: Remove stored knowledge from Larvling's memory
argument-hint: "[topic/statement ID or keyword]"
disable-model-invocation: true
---

**Schema:**
- `topics (id INTEGER PK AUTO, title TEXT NOT NULL, domain TEXT NOT NULL, tags TEXT NOT NULL, created TEXT, updated TEXT)`
- `statements (id INTEGER PK AUTO, topic_id INTEGER FK→topics(id), claim TEXT NOT NULL, created TEXT, updated TEXT, source TEXT [user|agent|doc|manual], kind TEXT [fact|decision|preference|assessment], session_id TEXT FK→sessions(id))`

Find the knowledge to remove — by ID, keyword, or by asking the user to clarify. Show matching item(s) and use AskUserQuestion to confirm before deleting. When deleting the last statement under a topic, delete the topic too.

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

Remove this statement?
```

## Final Step

**REQUIRED:** You MUST call AskUserQuestion (type: Decision) with these options after deletion is executed. Do not end your response without this menu:
- **Forget more** — remove another item
- **View remaining** — show what's left in the affected topic
- **Done** — no further action needed
