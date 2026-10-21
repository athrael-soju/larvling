# Larvling

> Your friendly memory companion. Every conversation is remembered.

Everything lives in `.claude/larvling.db` (SQLite, WAL mode).

## First Run

When the SessionStart context contains "Larvling - First Run", this is the very first time Larvling has been installed. You MUST welcome the user before doing anything else. Keep it warm, brief, and conversational - something like a friendly companion introducing itself. Include:
- That Larvling is now installed and will quietly remember their sessions
- That everything is automatic - no setup or extra effort needed
- Mention the available skills naturally: `/remember` to store knowledge, `/recall` to search it, `/forget` to remove it, `/sessions` to browse past sessions, `/summarize` for session summaries, `/export` to save a conversation as markdown, `/status` for a quick overview, `/tidy` to audit and consolidate knowledge, tasks, and sessions, `/query` for direct SQL access
- Do NOT list technical details, hook names, or internal architecture. Keep the magic behind the curtain.

## Update Notice

When the SessionStart context contains "Larvling update available", mention it once to the user at the start of the conversation. Keep it brief - one sentence is enough. Don't repeat it later in the session.

## Recording Health Warnings

When the injected context contains a **Larvling recording check** or **Larvling Recording Issue** warning, recording has silently failed — either the plugin's hooks stopped running (often a broken plugin cache) or a database write threw. **Surface this to the user immediately and plainly**, before other work: tell them recording stopped and that recent sessions/exchanges may not have been saved, and suggest they reinstall the plugin (`/plugin`) or run `/status` to check. This is the one warning you must not bury — the whole point is that the user finds out now, not days later. Mention it once per session.

## During a Session

Review the context Larvling injects at session start — it's your memory of what came before. Recording is automatic — just focus on the work.

### Session Start Menu

After reviewing the injected context, offer a welcome menu on the **first user message** using AskUserQuestion (type: Decision). Tailor the options to what's in the context:

- If there are **open tasks**, include: **Pick up a task** — resume work on an open task
- If there are **recent sessions**, include: **Continue where I left off** — pick up from the last session's topic
- Always include: **Start fresh** — begin something new

Keep the greeting brief (one sentence acknowledging the context), then present the menu. Skip the menu if the user's first message is already a specific question or command — don't interrupt them.

### Schema Migration

Most upgrades are applied automatically by `preflight.py` before the session starts. When the SessionStart context contains "Schema Migration Required", no automatic path existed (or it failed), and the database schema needs updating by hand.
Read the current and desired schemas provided, write and run the SQL to migrate (preserving all data), then bump the version in `larvling/db.py` with the provided command.
A backup of the database has already been created.

**SQLite migration rules (MUST follow):**

1. **Simple additions** (new column, new table, new index) — use `ALTER TABLE ADD COLUMN` or `CREATE TABLE/INDEX IF NOT EXISTS` directly. No table rebuild needed.
   - **`ADD COLUMN` defaults must be constants.** SQLite rejects non-constant defaults like `datetime('now')` or `(<expr>)` with `default value of column [x] is not constant`. Larvling's desired schema uses `DEFAULT CURRENT_TIMESTAMP` (a constant token allowed by `ADD COLUMN`, same output format as `datetime('now')`), so additive migrations can use the desired DDL verbatim. If you must add a column with a non-constant default, either use `CURRENT_TIMESTAMP` and rely on its equivalence, or do a full 12-step rebuild.
2. **Adding CHECK constraints to existing columns** — existing data may not conform to the new constraint. Always `SELECT DISTINCT <column>` first and `UPDATE` non-conforming values before rebuilding the table with the constraint. Use the 12-step rebuild pattern below.
3. **Column rename/type change/FK change** — SQLite requires the 12-step rebuild pattern:
   - `PRAGMA foreign_keys = OFF`
   - `BEGIN TRANSACTION`
   - `CREATE TABLE <new_table> (...)` with the correct schema
   - `INSERT INTO <new_table> SELECT ... FROM <old_table>`
   - `DROP VIEW` every view that reads the old table (e.g. `current_statements` reads `statements`) — otherwise the rename fails with `error in view ...: no such table`
   - `DROP TABLE <old_table>`
   - `ALTER TABLE <new_table> RENAME TO <old_table>`
   - Rebuild **every table** that has a FK referencing the rebuilt table (same pattern)
   - Rebuild any indexes/triggers on affected tables, and recreate the dropped views from the desired schema
   - `PRAGMA foreign_key_check` — must return zero rows
   - `COMMIT`
   - `PRAGMA foreign_keys = ON`
4. **Never leave temp tables behind** — no `_old`, `_backup`, `_temp` tables should exist after migration.
5. **Always verify FKs** — run `PRAGMA foreign_key_check` and confirm empty result.
6. **Verify with `get_current_schema()`** — after migration, compare live schema against `get_desired_schema()`. They must match (ignoring whitespace). **Equivalence note:** `DEFAULT CURRENT_TIMESTAMP` and `DEFAULT (datetime('now'))` are functionally identical (same stored format). Legacy v13 DBs carry the `(datetime('now'))` DDL even though the desired schema now uses `CURRENT_TIMESTAMP` — this cosmetic mismatch is acceptable and does not require a rebuild. Only fail the comparison on real structural differences (missing/extra columns, different types, different constraints).

### `/query` - Direct SQL Access

Use `/query` to run any SQL against larvling.db. Claude writes the SQL based on conversation context. Supports `--read-only` flag to restrict to SELECT/PRAGMA/EXPLAIN only (used by the extraction agent).

**Schema:**
- `sessions (id TEXT PK, started_at TEXT, ended_at TEXT, duration_min REAL, title TEXT, agent_summary TEXT, exchange_count INT, summary_at TEXT, summary_msg_count INT, tags TEXT, summary_offered INT DEFAULT 0, extracted_at TEXT, extracted_through INT, extract_lease TEXT)`
- `messages (id INT PK AUTO, session_id TEXT FK, timestamp TEXT, role TEXT [user|assistant|system], content TEXT, metadata TEXT)`
- `topics (id INTEGER PK AUTO, title TEXT NOT NULL, domain TEXT NOT NULL, tags TEXT NOT NULL, created TEXT, updated TEXT)`
- `statements (id INTEGER PK AUTO, topic_id INTEGER FK→topics(id), claim TEXT NOT NULL, created TEXT, updated TEXT, source TEXT [user|agent|doc|manual], kind TEXT [fact|decision|preference|assessment], session_id TEXT FK→sessions(id), superseded_by INTEGER FK→statements(id), superseded_at TEXT)`
- `current_statements` — view: the statements still in force (`superseded_at IS NULL`), same columns
- `statements_fts (claim, topic_title)`, `tasks_fts (title)` — FTS5 search index, rowid = statement/task id, kept in sync by triggers. Search through `search.py` rather than querying these directly. Without FTS5 in this Python's SQLite they are absent, or present but out of date (their triggers are dropped so writes keep working), and `search.py` uses `LIKE` instead. FTS5 creates and manages its own `*_fts_data`, `_idx`, `_content`, `_docsize` and `_config` tables; never create those by hand.
- `tasks (id INTEGER PK AUTO, title TEXT NOT NULL, domain TEXT NOT NULL, status TEXT DEFAULT 'open', priority TEXT DEFAULT 'medium', horizon TEXT DEFAULT 'later', metadata TEXT, created TEXT, updated TEXT [last touched], due TEXT [YYYY-MM-DD])`
- `updates (id INTEGER PK AUTO, task_id INTEGER FK→tasks(id), content TEXT NOT NULL, timestamp TEXT)`

**JSON metadata columns** (query with `json_extract(metadata, '$.field')`):

- `tasks.metadata` — optional. Populated on `add_task` with `{"source_session_id": "<sid>"}` when a real session id is known; NULL otherwise. Trace a task back to the session that produced it with `json_extract(metadata, '$.source_session_id')`. Task decay adds `due_promoted` (the due date it last moved the task to `now` for, or found it already there) so it moves a task once per due date.
- `messages.metadata` — populated by hooks on every turn:
  - user messages: `{"cwd", "permission_mode", "usage": {input_tokens_estimate}}`
  - assistant messages: `{"tool_calls": {<tool>: <count>}, "usage": {input_tokens, cache_creation_input_tokens, cache_read_input_tokens, output_tokens, ...}}`
  - system messages: analysis telemetry

**Let the question scope the query.** `query.py` is a pass-through executor — it runs whatever SQL you write, with no template or required shape, so choosing a sane one is on you. Before writing SQL, read the scope out of the question and put it in a `WHERE`:

- **A scoped question is a `WHERE`.** "Today / now" → `horizon='now'`. "This project" → `domain='…'`. "High priority" → `priority='high'`. The question usually hands you the filter for free — a bare `SELECT … WHERE status='open'` means you haven't decided what you're actually asking.
- **An overview is an aggregate.** "How many / what's the shape" → `COUNT`/`GROUP BY`, which returns a handful of rows. For "what's on my plate / agenda" your `now` tasks are **already in the SessionStart context** (listed in full, with a rollup of `soon`/`later`) — read those and synthesize; query only to drill past what it shows.
- **Detail is sized to the question.** Select only the columns you need (`substr(claim,1,160)` for long text) and filter with `WHERE`; let the question's scope decide how many rows come back. Add a `LIMIT` only when the answer would otherwise be excessive — not by reflex. "Show me the 3 newest" wants `LIMIT 3`; "list my open `now` tasks" wants all of them.
- **"Everything" is the only case for a full scan** — and then pass `--full` on purpose. The default table mode does **not** truncate: a query whose output would exceed ~16KB is **refused with an error** telling you to re-scope. You can never accidentally summarize a partial result, because no partial result is returned. Re-scope (WHERE/GROUP BY/LIMIT) or opt into `--full`.

**Examples:**

```
/query "SELECT horizon, priority, COUNT(*) AS n FROM tasks WHERE status='open' GROUP BY horizon, priority"   -- briefing: bounded rollup, can't truncate
/query "SELECT id, title, horizon FROM tasks WHERE status='open' AND horizon='now' ORDER BY priority"   -- drill into the 'now' slice: the scope bounds it, no LIMIT
/query "SELECT t.id, t.title, substr(s.claim,1,160) AS claim FROM topics t JOIN current_statements s ON s.topic_id=t.id WHERE s.claim LIKE '%deploy%' AND s.kind IS NOT 'assessment' LIMIT 10"   -- keyword search, narrowed cells
/query "SELECT id, title, agent_summary FROM sessions WHERE agent_summary IS NOT NULL ORDER BY started_at DESC LIMIT 5"
/query "SELECT id, role, substr(content,1,160) AS content FROM messages WHERE content LIKE '%auth%' LIMIT 10" --json
```

**Quick Column Reference:**

| Table | Key columns |
|-------|-------------|
| topics | id, title, domain, tags |
| statements | id, topic_id, claim, source, kind, session_id, superseded_by, superseded_at |
| current_statements (view) | same as statements, only those still in force |
| tasks | id, title, status, priority, horizon, due, updated, metadata |
| sessions | id, started_at, title, agent_summary |
| messages | id, session_id, role, content |
| updates | id, task_id, content, timestamp |

### Knowledge, Tasks & Unified Analysis

Larvling stores persistent knowledge in the `topics` + `statements` tables, and action items in the `tasks` + `updates` tables. Multiple mechanisms handle data extraction:

**Search and duplicates:** `search.py "<words>"` ranks current statements by any of the words, in claims and topic titles (bm25 over the FTS5 index); `--tasks` searches tasks in every status, `--assessments` includes assessments. `/recall`, `/remember` and the extraction agent search this way instead of writing `LIKE` queries. When SQLite lacks FTS5, `search.py` falls back to `LIKE`. Before adding a statement, topic or task, extraction compares it with the closest existing ones (a statement added to a topic, with that topic's): if the share of words they have in common (ignoring order, case, stopwords and plurals) reaches `dedup_threshold` in `.claude/larvling.config.json` (0 to 1, default `0.8`; `0` turns the check off), it is skipped as `near_duplicate`, and a new topic whose title rewords an existing one gets its statement added to that topic instead. Two texts that differ in a number, a date, a name or a negation are never duplicates ("Pay the March invoice" and "Pay the April invoice"). Tasks are compared in every status, so rewording a dropped task doesn't bring it back; to bring one back, reopen it.

**Supersession:** a statement is never rewritten in place. When a claim is corrected or goes out of date, a new statement replaces it: the old row gets `superseded_by` (the replacement's id) and `superseded_at`, and stays for history. A statement retired with nothing to replace it gets `superseded_at` alone. Read knowledge from the `current_statements` view, which leaves both out; query `statements` only for history, e.g. the chain behind a claim: `SELECT id, claim, superseded_at FROM statements WHERE superseded_by = <id>`.

**UserPromptSubmit → Knowledge Context (read):** The `## Knowledge Context` directive prints on every exchange with the `query.py` path and topic/statement counts. When the question depends on stored knowledge, recall it and weave it in — but keep reads bounded: prefer the `/recall <term>` skill, or a keyword-filtered query (`claim`/`title`/`tags LIKE`) sized to the question — add a `LIMIT` only if the keyword is broad enough to match a lot. Read from `current_statements`, not `statements`, unless the user asks how a claim changed. Don't run an unfiltered `SELECT ... FROM current_statements`, pull whole topics/domains, or dump the table to a file; narrow the keyword instead of widening the pull. Leave out `kind = 'assessment'` rows (`s.kind IS NOT 'assessment'`) unless the user asks for opinions or recommendations: they are agent judgements the user agreed with, not facts.

**SessionEnd / PreCompact → Session extraction (write):** `analyze.py` runs once per session, in a detached process started by the SessionEnd hook, and again before each compaction (leaving a prompt still being answered for later, so it is extracted with its answer). It reads the user and agent messages the session recorded since its last extraction (`sessions.extracted_through`), relabelling first any prompt the transcript shows Claude Code or an agent wrote, and calls `sdk.py` (`call_model()`, model from the `model` config key) with the whole conversation, so a claim the user corrects later in the session is stored only in its corrected form. A long session is split into chunks of about 60k characters, one call each. Each call is a Claude Code session of its own that loads none of the user's settings, CLAUDE.md, auto-memory, plugins, hooks or MCP servers, is offered only the Bash tool, and saves no transcript. The Stop hook only records: the reply, and as user messages anything the user added while the agent worked (queued messages, AskUserQuestion answers, feedback on a rejected tool call). At SessionStart, `session_start.py` catches up on earlier sessions that still have unextracted messages and have been quiet for 10 minutes, in the DB and in their transcript (a crash, a closed terminal, a failed call). A lease on the session (`sessions.extract_lease`) keeps two workers from extracting it at once; the SessionEnd worker waits for one that is still running. With `analysis` off, SessionEnd marks the session extracted, so turning it back on doesn't extract what was recorded in between. The extraction agent has Bash tool access to query all 6 tables for dedup — given the full schema, it decides what queries to run. Each call extracts multiple data types:
- **Knowledge** → `topics` + `statements` tables (hierarchical: topic groups related statements). Actions: `add_topic`, `add_statement`, `supersede_statement` (replaces a current statement that is wrong, out of date or less precise; the old one is kept as history), `update_topic`. Every statement records its provenance: `source` (`user` or `agent` for extraction, `manual` for `/remember`), `kind` (`fact`, `decision`, `preference`, `assessment`) and the `session_id` that produced it. Statements stored before provenance existed have NULL `source`/`kind`, and a `session_id` only where it could be recovered from the message log. Extraction enforces where knowledge may come from. Anything about the user, their work or the people they deal with comes from the user's own words: their prompt, plus what they add while the agent works (queued messages, AskUserQuestion answers, feedback on a rejected tool call). From the agent, only general domain facts are kept, plus judgements the user explicitly agreed with, stored as `assessment`. Each item must quote its evidence, and an item whose quote isn't in a message from the speaker it names is dropped. The check proves the quote is there, not that it supports the claim: the prompt asks for claims that say no more than their evidence, and that is all that stops an inference built on a real quote. Assessments stay out of the session-start briefing and default `/recall`.
- **Session tags** → `sessions.tags` column, comma-separated, consolidated at each extraction. The agent queries current tags itself.
- **Tasks** → `tasks` + `updates` tables. Actions: `add_task`, `add_update` (attach context/notes), `update_task` (modify status/priority/horizon/due with reason). A task gets a `due` date only when the user gives one. Every change moves `tasks.updated`, and so does every update entry, however it is written (a trigger on `updates`), so it means last touched. Invalid fields skip the item and log to JSONL — no silent coercion — except a `due` that isn't a date, which is left out (logged as `extraction_field_dropped`) so the rest of the item still lands.

**SessionStart → Task decay (write):** before the briefing, code (not the model) keeps the `now` list to work that is being done. An open task due within `due_soon_days` (default 7, overdue included) moves to `now`, once per due date, so a task the user moves back out stays where they put it; an open `now` task untouched for `decay_now_days` (default 7) moves to `soon`. Each move adds an update entry saying why, and the briefing notes `Moved automatically: ...`. Open `soon` tasks untouched for `decay_soon_days` (default 30) are listed under **Drop Candidates**; they are never dropped automatically. The thresholds live in `.claude/larvling.config.json`; `0` turns a rule off.

**Log format** — JSONL (one JSON object per line) in `.claude/larvling.jsonl`:
```jsonl
{"ts":"2026-02-24T16:16:35","event":"prompt","sid":"6801adcc","n":1}
{"ts":"...","event":"context","sid":"6801adcc","injected":["5 topics, 11 statements","stale summary hint"]}
{"ts":"...","event":"response","sid":"6801adcc","chars":20,"is_dup":false}
{"ts":"...","event":"skill","sid":"6801adcc","name":"/larvling:status"}
{"ts":"...","event":"knowledge","sid":"6801adcc","topics_inserted":1,"stmts_inserted":2}
{"ts":"...","event":"tasks","sid":"6801adcc","inserted":1,"updates_inserted":2,"tasks_updated":1}
{"ts":"...","event":"extraction_skipped","sid":"6801adcc","action":"add_task","reason":"invalid domain","domain":"foobar"}
{"ts":"...","event":"analysis","sid":"6801adcc","session_tags":["greeting"]}
{"ts":"...","event":"session_end","sid":"6801adcc","exchanges":1,"duration":0.2}
```

**Manual skills** (`/remember`, `/recall`, `/forget`) still work for explicit user-initiated knowledge management.

### Drop Candidates

When the SessionStart context lists **Drop Candidates**, mention them once, briefly, and offer to drop them with AskUserQuestion (type: Decision): **Drop them** (set `status = 'dropped'` with an update entry saying why), **Keep them** (add an update entry for each, which counts as touching it), or **Review one by one**. Don't raise them again in the session.

### Proactive Task Nudges

When the SessionStart context shows **Open Tasks** and the user's message relates to one of them, surface it proactively. Use AskUserQuestion (type: Decision):
- **Work on this task** — start or continue the matching task
- **Update task** — add a note or change status/priority
- **Not now** — dismiss and continue with the user's original request

Rules:
- Only nudge when there's a clear match between the user's message and an open task (shared keywords, same topic area)
- Maximum one nudge per session — don't nag
- Never nudge when the user is mid-flow on unrelated work
- If the user picks "Not now", don't nudge about that task again this session

### Skill Discovery Hints

When you notice the user doing something manually that a Larvling skill handles more efficiently, mention it briefly **after completing their request** — not before. Examples:

- User writes SQL against larvling.db → "Tip: `/query` can run that directly"
- User asks "what do you know about X?" → "Tip: `/recall X` searches stored knowledge"
- User says "remember that..." in conversation → "Tip: `/remember` stores knowledge permanently"
- User asks about past conversations → "Tip: `/sessions` can search past sessions by topic"

Rules:
- Only hint once per skill per session — after that, assume the user knows
- Keep hints to one short sentence, appended after your actual response
- Never hint when the user just used a skill (they already know)
- Don't hint about `/query` when the user is doing non-Larvling SQL work

### Skill Follow-Up Menus

All skills include a `## Final Step` section with a **REQUIRED** AskUserQuestion call. After presenting skill results, you MUST call the follow-up menu — do not end your response without it. The menus use type `Decision` with 2-3 concise options relevant to what was just shown.

AskUserQuestion automatically includes an "Other" option that lets the user type freeform input. If the user picks "Other", treat their text as a natural-language request and handle it accordingly — don't force them back into the menu.

### Session Summaries

`inject_context()` automatically prints a `## Summary` hint when a summary is needed. Thresholds:
- **No summary yet:** shown when the session reaches 10+ messages
- **Stale summary:** shown when 5+ new messages have been added since the last summary

When you see the `## Summary` hint, offer `/summarize` via AskUserQuestion. Keep the offer brief and non-intrusive — a single sentence is enough. The hint itself is clean (no agent directives); CLAUDE.md is the authority on what to do when you see it. Don't ask repeatedly if the user declines. The `summary-manager` agent handles the actual summarization work.

### Agents

**`summary-manager`** — Session summarization. Delegated to when the user accepts a `/summarize` offer or when the summary hint fires. Reads conversation pairs, writes a concise summary, and stores it.

**`knowledge-maintenance`** — Periodic audit and consolidation of knowledge (topics/statements), tasks (tasks/updates), and sessions. Delegated to when the user invokes `/tidy` or when the maintenance hint fires (50+ topics or 100+ statements). Identifies duplicates, stale entries, misclassified domains, contradictions, stale open tasks, and sessions missing titles/summaries. Proposes changes via AskUserQuestion, applies only what the user approves. Never deletes — merges knowledge via statement reassignment + topic renaming, retires tasks via `status = 'dropped'` + audit update, flags empty sessions for the user to handle.

### Progress Tracking with Tasks

Use **TaskCreate** and **TaskUpdate** to give the user visibility into multi-step operations. This is especially valuable for skills that involve several sequential actions.

**When to create tasks:**
- Multi-step skill operations (e.g., `/tidy` audit phases, batch `/export`, multi-session `/summarize`)
- Any user request that will take 3+ distinct steps
- When the user explicitly asks to track progress

**How to use:**
- Create tasks before starting work, with clear subjects in imperative form
- Mark each task `in_progress` when you start it (shows a spinner to the user)
- Mark `completed` as soon as you finish each step — don't batch completions
- Use `activeForm` for natural spinner text (e.g., "Auditing knowledge base")

**When NOT to create tasks:**
- Single-step skills (`/status`, simple `/recall`, `/query`)
- Trivial operations that complete in one tool call
- When the user is in a fast back-and-forth conversation (tasks would slow things down)

## Interaction Protocol

Use **AskUserQuestion** tool for structured input gathering:

| Type          | When to use                                    |
| ------------- | ---------------------------------------------- |
| Clarification | Inputs missing or ambiguous                    |
| Decision      | Multiple valid approaches exist                |
| Approval      | Stage work complete, need sign-off             |
| Summary       | Session summary is stale, offer update         |
| Maintenance   | Knowledge base is large, offer /tidy |
| Knowledge     | About to save or update knowledge               |

Menu format:
- 2-4 options per question
- Each option: short label (1-5 words) + description
- One option would be Claude's recommendation
- Tool auto-includes "Other" option

Use **plain text** for:
- Presenting completed outputs
- Explaining rationale
- Summarizing captured information

## Running Python Scripts

Skills reference `python3` in their examples, but the correct executable varies by platform. Before running any Larvling Python script, discover the right binary:

```bash
PY=$(for p in python3.13 python3.12 python3.11 python3.10 python3; do command -v "$p" >/dev/null 2>&1 && echo "$p" && break; done); PY=${PY:-python}
```

Then use `$PY` in place of `python3`:
```bash
$PY "${CLAUDE_PLUGIN_ROOT}/scripts/query.py" "<SQL>"
```

## Dependencies

- **Python 3.10+** — all scripts use standard library except `claude_agent_sdk`
- **claude-agent-sdk** 0.2.170 or later (`pip install -r requirements.txt`) — required for unified analysis (knowledge extraction, session tags, tasks). When it is missing or older, `preflight.py` installs it in the background at session start, since the download takes longer than the hook may run.
- Install via: `pip install -r "${CLAUDE_PLUGIN_ROOT}/requirements.txt"`

## Troubleshooting

**Larvling - Installing Dependency** / **Missing Dependency** — `claude-agent-sdk` is missing or older than Larvling needs. `preflight.py` installs it in the background and says so; if that failed, the next session start prints a `pip install` command and pip's output, and tries again. Run the command if it keeps failing. Without it, knowledge extraction, session tags, and task tracking are disabled (basic session logging still works).

**`python: command not found`** — Larvling requires Python 3.10+. Use the discovery pattern from the "Running Python Scripts" section above. Ensure at least `python3` (Unix) or `python` (Windows) is on the system PATH.

## Run End

- Session timing and exchange count are recorded automatically
- No action needed from the agent
