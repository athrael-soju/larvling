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
# A search uses at most this many words, each cut to MAX_TERM_CHARS.
MAX_TERMS = 16
MAX_TERM_CHARS = 200
# Most similar existing rows compared when checking for a duplicate.
DUPLICATE_CANDIDATES = 5
# A text with this many words the other lacks says something more: a long
# claim with a fact added at the end isn't a duplicate of the claim.
NEW_WORDS = 3
# Words that change what a text says when only one of two texts has them.
NEGATION = re.compile(r"\b(?:not|no|never|none|nor|cannot|without)\b|n['\u2019]t\b", re.I)
DATE_WORDS = {
    "january", "february", "march", "april", "june", "july", "august", "september",
    "october", "november", "december", "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday", "today", "tomorrow", "yesterday",
}


def words(text):
    """Return the distinct lowercase words of *text*, leaving out stopwords
    unless nothing else is left."""
    found = re.findall(r"\w+", (text or "").lower())
    kept = [w for w in found if w not in STOPWORDS] or found
    return list(dict.fromkeys(kept))


def terms(text):
    """The words of *text* a search looks for."""
    return [w[:MAX_TERM_CHARS] for w in words(text)[:MAX_TERMS]]


def fts_query(text):
    """Return an FTS5 query matching any word of *text*, or None if it has none."""
    found = terms(text)
    return " OR ".join(f'"{t}"' for t in found) if found else None


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


def _names(text):
    """Stems of the words of *text* capitalised mid-sentence: names, mostly."""
    found = set()
    for sentence in re.split(r"[.!?:;\n]+", text or ""):
        for word in re.findall(r"\w+", sentence)[1:]:
            if word[0].isupper():
                found.add(_stem(word.lower()))
    return found


def _first_word(text):
    found = re.findall(r"\w+", text or "")
    return found[0] if found else ""


def differ_in_kind(a, b):
    """True if a word only one of *a* and *b* has changes what it says: a
    number, a date, a name, or a negation. "Pay the March invoice" and "Pay the
    April invoice" overlap a lot, but aren't the same task. Two texts opening
    with capitalised words the other lacks ("Alice opts out", "Bobby opts
    out") may be about different people, so they differ too."""
    if bool(NEGATION.search(a or "")) != bool(NEGATION.search(b or "")):
        return True
    sa = {_stem(w) for w in words(a)}
    sb = {_stem(w) for w in words(b)}
    only = sa ^ sb
    if any(any(c.isdigit() for c in w) or w in DATE_WORDS for w in only):
        return True
    fa, fb = _first_word(a), _first_word(b)
    if (fa[:1].isupper() and fb[:1].isupper()
            and _stem(fa.lower()) in sa - sb and _stem(fb.lower()) in sb - sa):
        return True
    return bool(only & ((_names(a) - sb) | (_names(b) - sa)))


def _like_hits(columns, terms):
    """SQL counting how many *terms* match any of *columns*, and its parameters."""
    match = " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in columns)
    sql = " + ".join(f"({match})" for _ in terms)
    params = [f"%{escape_like(t)}%" for t in terms for _ in columns]
    return f"({sql})", params


def _fts_ready(conn, table):
    return fts5_available() and has_table(conn, table)


def search_statements(conn, text, limit=20, assessments=False, topic_id=None):
    """Return current statements matching *text*, best match first.

    Each row has id, topic_id, topic, domain, claim and kind. Assessments are
    left out unless *assessments* is true; *topic_id* keeps to one topic.
    """
    query = fts_query(text)
    if not query:
        return []
    where = "" if assessments else "AND s.kind IS NOT 'assessment' "
    extra = ()
    if topic_id is not None:
        where += "AND s.topic_id = ? "
        extra = (topic_id,)
    if _fts_ready(conn, "statements_fts"):
        try:
            return conn.execute(
                "SELECT s.id, s.topic_id, t.title AS topic, t.domain, s.claim, s.kind "
                "FROM statements_fts "
                "JOIN current_statements s ON s.id = statements_fts.rowid "
                "JOIN topics t ON t.id = s.topic_id "
                f"WHERE statements_fts MATCH ? {where}"
                "ORDER BY bm25(statements_fts, 1.0, 0.5) LIMIT ?",
                (query, *extra, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            pass  # index unreadable: fall back to LIKE
    hits, params = _like_hits(("s.claim", "t.title"), terms(text))
    return conn.execute(
        "SELECT s.id, s.topic_id, t.title AS topic, t.domain, s.claim, s.kind "
        "FROM current_statements s JOIN topics t ON t.id = s.topic_id "
        f"WHERE {hits} > 0 {where}ORDER BY {hits} DESC, s.id DESC LIMIT ?",
        (*params, *extra, *params, limit),
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
    hits, params = _like_hits(("t.title",), terms(text))
    return conn.execute(
        "SELECT t.id, t.title, t.status, t.priority, t.horizon FROM tasks t "
        f"WHERE {hits} > 0 ORDER BY {hits} DESC, t.id DESC LIMIT ?",
        (*params, *params, limit),
    ).fetchall()


def adds_words(text, other):
    """True if *text* has NEW_WORDS or more words that *other* lacks."""
    return len({_stem(w) for w in words(text)} - {_stem(w) for w in words(other)}) >= NEW_WORDS


def near_duplicate(rows, text, field, threshold):
    """Return the row among *rows* whose *field* is most similar to *text*, if
    that similarity reaches *threshold* (0 < threshold <= 1), *text* adds
    fewer than NEW_WORDS words to it, and they don't differ in a number, date,
    name or negation; else None."""
    if not 0 < threshold <= 1:
        return None
    best, score = None, 0.0
    for row in rows:
        if differ_in_kind(text, row[field]) or adds_words(text, row[field]):
            continue
        s = similarity(text, row[field])
        if s > score:
            best, score = row, s
    return best if score >= threshold else None


def duplicate_statement(conn, claim, threshold, topic_id=None):
    """Return the current statement *claim* rewords, or None; under
    *topic_id* only, when given."""
    rows = search_statements(
        conn, claim, DUPLICATE_CANDIDATES, assessments=True, topic_id=topic_id
    )
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
            limit = 0
        if limit < 1:
            print("--limit needs a number of at least 1", file=sys.stderr)
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
