---
name: remember
description: Store knowledge that Larvling will remember across sessions
argument-hint: "[knowledge to remember]"
---

Store the given knowledge in Larvling's database. Do NOT delegate to any subagent — handle this directly.

## Database

SQLite database at `.claude/larvling.db`.

**Schema:**
- `topics (id INTEGER PK AUTO, title TEXT NOT NULL, domain TEXT NOT NULL, tags TEXT NOT NULL, created TEXT, updated TEXT)`
- `statements (id INTEGER PK AUTO, topic_id INTEGER FK→topics(id), claim TEXT NOT NULL, created TEXT, updated TEXT, source TEXT [user|agent|doc|manual], kind TEXT [fact|decision|preference|assessment], session_id TEXT FK→sessions(id), superseded_by INTEGER FK→statements(id), superseded_at TEXT)`
- `current_statements` — view: the statements still in force (`superseded_at IS NULL`), same columns

## Query Tool

```
$PY "${CLAUDE_PLUGIN_ROOT}/scripts/query.py" "<SQL>"
```

Append `--json` for JSON output.

## Domains

Classify knowledge into one of: `personal`, `professional`, `preferences`, `interests`, `knowledge`, `technical`, `workflow`

## Steps

1. **Search for overlap** — search existing knowledge for the key words of what to remember, and try a rewording too:
   ```
   $PY "${CLAUDE_PLUGIN_ROOT}/scripts/search.py" "<key words>" --assessments
   ```
2. **Decide** the right action:
   - **New topic + statement** — knowledge is genuinely new, no existing topic covers it
   - **Add statement to existing topic** — an existing topic covers this area, but the specific claim is new
   - **Supersede existing statement** — the new knowledge corrects, updates or refines an existing claim. Never rewrite a claim in place: add the new statement and mark the old one superseded, so its history is kept
   - **Skip** — already covered by existing statements
3. **Execute** the appropriate SQL. Every statement you write records its provenance: `source = 'manual'`, a `kind`, and this session. Pick `kind` from `fact` (something true about the world, the user or their work), `decision` (a choice the user has made), `preference` (how the user likes things done), or `assessment` (a judgement or recommendation rather than an established fact). Keep the `session_id` subquery exactly as written below.
   - New topic: `INSERT INTO topics (title, domain, tags) VALUES ('...', '...', '...')`, then add its first statement with `topic_id = (SELECT MAX(id) FROM topics WHERE title = '...')` in the new-statement SQL below
   - New statement: `INSERT INTO statements (topic_id, claim, source, kind, session_id) VALUES (N, '...', 'manual', '<kind>', (SELECT id FROM sessions WHERE id = '${CLAUDE_SESSION_ID}'))`
   - Supersede statement N: add the new statement to N's topic with the new-statement SQL above (`topic_id = (SELECT topic_id FROM statements WHERE id = N)`), then mark N superseded by it. The guards make this a no-op when N is already superseded or the new statement wasn't stored: `UPDATE statements SET superseded_by = (SELECT MAX(id) FROM current_statements WHERE claim = '...' AND id != N), superseded_at = datetime('now') WHERE id = N AND superseded_at IS NULL AND EXISTS (SELECT 1 FROM current_statements WHERE claim = '...' AND id != N)`. If it reports `0 row(s) affected`, find out why before retrying
   - Update topic: `UPDATE topics SET title = '...', tags = '...', updated = datetime('now') WHERE id = N`
4. **Confirm** — use AskUserQuestion (type: Knowledge) to show what was stored:
   ```
   Stored in **[Topic Title]** ([domain]):
   > "[claim text]"
   ```
   For updates, show the before/after briefly.

## Guidelines

- Never delete data — supersede claims or retire topic titles instead
- Keep claims concise and self-contained — each should make sense without context
- Tags should be lowercase, comma-separated, 2-5 per topic
- When in doubt about whether something is worth storing, store it — knowledge is cheap

## Final Step

**REQUIRED:** You MUST call AskUserQuestion (type: Decision) with these options after confirming what was stored. Do not end your response without this menu:
- **Remember more** — store additional knowledge
- **View topic** — show all statements under the topic that was just updated
- **Done** — no further action needed
