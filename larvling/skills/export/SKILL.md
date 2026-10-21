---
name: export
description: Export a session conversation to markdown
argument-hint: "[session-id or --list or --all]"
---

**Schema:** `sessions (id TEXT PK, started_at TEXT, ended_at TEXT, duration_min REAL, title TEXT, agent_summary TEXT, exchange_count INT, summary_at TEXT, summary_msg_count INT, tags TEXT, summary_offered INT DEFAULT 0, extracted_at TEXT, extracted_through INT, extract_lease TEXT)`

For `--all` or batch exports, use TaskCreate to track progress (one task per session being exported). For single exports, no tasks needed.

Run via:
```
$PY "${CLAUDE_PLUGIN_ROOT}/scripts/export.py" <session_id>           # prints to stdout
$PY "${CLAUDE_PLUGIN_ROOT}/scripts/export.py" <session_id> <outfile> # writes to file
$PY "${CLAUDE_PLUGIN_ROOT}/scripts/export.py" --list
$PY "${CLAUDE_PLUGIN_ROOT}/scripts/export.py" --all [<outdir>]       # default: .claude/exports/
```

## Output Format

After exporting, confirm briefly:

```
Exported session **2026-04-01** (6.6m, 12 messages) to `.claude/exports/2026-04-01_abc123.md`
```

For `--list`, show available sessions as a compact table. For `--all`, report the count and output directory.

## Final Step

**REQUIRED:** You MUST call AskUserQuestion (type: Decision) with these options after exporting. Do not end your response without this menu:
- **Export another** — export a different session
- **Export all** — export all sessions to the exports directory
- **Browse sessions** — view session list to pick one
