"""
Larvling Search - ranked full-text search over stored knowledge and tasks.

Usage:
    python search.py "words"                # current statements, best match first
    python search.py "words" --assessments  # include assessments
    python search.py "words" --tasks        # tasks, in every status
    python search.py "words" --limit N      # at most N results (default 20)
    python search.py "words" --json         # JSON output

Matches any of the words, in a statement's claim or its topic's title, and
ranks with bm25 over the FTS5 index. When this SQLite has no FTS5 it falls
back to LIKE, ranking by how many of the words match.
"""

import json
import re
import sqlite3
import sys

from db import escape_like, fts5_available, has_table, open_db, reconfigure_stdout, require_db

# Words too common to say anything about a match.
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
    "i", "in", "is", "it", "its", "my", "of", "on", "or", "our", "so", "that", "the",
    "their", "this", "to", "was", "we", "were", "with", "you", "your",
}
MAX_TERMS = 16
# Most similar existing rows compared when checking for a duplicate.
DUPLICATE_CANDIDATES = 5


def words(text):
    """Return the distinct lowercase words of *text*, leaving out stopwords
    unless nothing else is left."""
    found = re.findall(r"\w+", (text or "").lower())
    kept = [w for w in found if w not in STOPWORDS] or found
    return list(dict.fromkeys(kept))[:MAX_TERMS]


def fts_query(text):
    """Return an FTS5 query matching any word of *text*, or None if it has none."""
    terms = words(text)
    return " OR ".join(f'"{t}"' for t in terms) if terms else None


def _stem(word):
    """Strip a plural or -ing/-ed ending, so "modules" matches "module"."""
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    for suffix in ("ing", "ed"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def similarity(a, b):
    """Word overlap of two texts, 0 to 1: shared stemmed words over all of them.

    Word order, case, punctuation, stopwords and plurals don't count, so a
    reworded duplicate scores 1; one different word in four scores 0.6.
    """
    sa = {_stem(w) for w in words(a)}
    sb = {_stem(w) for w in words(b)}
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def _like_hits(columns, terms):
    """SQL counting how many *terms* match any of *columns*, and its parameters."""
    match = " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in columns)
    sql = " + ".join(f"({match})" for _ in terms)
    params = [f"%{escape_like(t)}%" for t in terms for _ in columns]
    return f"({sql})", params


def _fts_ready(conn, table):
    return fts5_available() and has_table(conn, table)


def search_statements(conn, text, limit=20, assessments=False):
    """Return current statements matching *text*, best match first.

    Each row has id, topic_id, topic, domain, claim and kind. Assessments are
    left out unless *assessments* is true.
    """
    query = fts_query(text)
    if not query:
        return []
    kind = "" if assessments else "AND s.kind IS NOT 'assessment'"
    if _fts_ready(conn, "statements_fts"):
        try:
            return conn.execute(
                "SELECT s.id, s.topic_id, t.title AS topic, t.domain, s.claim, s.kind "
                "FROM statements_fts "
                "JOIN current_statements s ON s.id = statements_fts.rowid "
                "JOIN topics t ON t.id = s.topic_id "
                f"WHERE statements_fts MATCH ? {kind} "
                "ORDER BY bm25(statements_fts, 1.0, 0.5) LIMIT ?",
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            pass  # index unreadable: fall back to LIKE
    hits, params = _like_hits(("s.claim", "t.title"), words(text))
    return conn.execute(
        "SELECT s.id, s.topic_id, t.title AS topic, t.domain, s.claim, s.kind "
        "FROM current_statements s JOIN topics t ON t.id = s.topic_id "
        f"WHERE {hits} > 0 {kind} ORDER BY {hits} DESC, s.id DESC LIMIT ?",
        (*params, *params, limit),
    ).fetchall()


def search_tasks(conn, text, limit=20):
    """Return tasks in any status matching *text*, best match first.

    Each row has id, title, status, priority and horizon.
    """
    query = fts_query(text)
    if not query:
        return []
    if _fts_ready(conn, "tasks_fts"):
        try:
            return conn.execute(
                "SELECT t.id, t.title, t.status, t.priority, t.horizon "
                "FROM tasks_fts JOIN tasks t ON t.id = tasks_fts.rowid "
                "WHERE tasks_fts MATCH ? ORDER BY bm25(tasks_fts) LIMIT ?",
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            pass
    hits, params = _like_hits(("t.title",), words(text))
    return conn.execute(
        "SELECT t.id, t.title, t.status, t.priority, t.horizon FROM tasks t "
        f"WHERE {hits} > 0 ORDER BY {hits} DESC, t.id DESC LIMIT ?",
        (*params, *params, limit),
    ).fetchall()


def near_duplicate(rows, text, field, threshold):
    """Return the row among *rows* whose *field* is most similar to *text*, if
    that similarity reaches *threshold* (0 < threshold <= 1); else None."""
    if not 0 < threshold <= 1:
        return None
    best, score = None, 0.0
    for row in rows:
        s = similarity(text, row[field])
        if s > score:
            best, score = row, s
    return best if score >= threshold else None


def duplicate_statement(conn, claim, threshold):
    """Return the current statement *claim* rewords, or None."""
    rows = search_statements(conn, claim, DUPLICATE_CANDIDATES, assessments=True)
    return near_duplicate(rows, claim, "claim", threshold)


def duplicate_task(conn, title, threshold):
    """Return the task, in any status, that *title* rewords, or None."""
    return near_duplicate(search_tasks(conn, title, DUPLICATE_CANDIDATES), title, "title", threshold)


def duplicate_topic(conn, title, threshold):
    """Return the topic *title* rewords, or None. Topics are few, so all are compared."""
    rows = conn.execute("SELECT id, title FROM topics").fetchall()
    return near_duplicate(rows, title, "title", threshold)


def main():
    reconfigure_stdout()
    args = sys.argv[1:]
    flags = {a for a in args if a.startswith("--")}
    limit = 20
    if "--limit" in args:
        i = args.index("--limit")
        try:
            limit = int(args[i + 1])
        except (IndexError, ValueError):
            print("--limit needs a number", file=sys.stderr)
            sys.exit(1)
        args = args[:i] + args[i + 2:]
    text = " ".join(a for a in args if not a.startswith("--"))
    if not words(text):
        print(__doc__.strip(), file=sys.stderr)
        sys.exit(1)

    require_db()
    with open_db() as conn:
        if "--tasks" in flags:
            rows = search_tasks(conn, text, limit)
        else:
            rows = search_statements(conn, text, limit, "--assessments" in flags)

    rows = [dict(r) for r in rows]
    if "--json" in flags:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    from query import format_table

    print(format_table(rows) if rows else "No matches.")


if __name__ == "__main__":
    main()
