"""
Larvling Query - run arbitrary SQL against larvling.db.

Usage:
    python query.py "SQL"               # table output; REFUSES (errors) if the result exceeds ~16KB
    python query.py "SQL" --json        # JSON output (full, uncapped)
    python query.py "SQL" --full        # table output, no cell or size cap (prints everything)
    python query.py "SQL" --read-only   # reject non-SELECT statements

The default table mode does not truncate. If a query would return more than
~16KB of table, query.py refuses and tells you to re-scope (WHERE/GROUP BY/
LIMIT) or pass --full — a truncated table reads like a complete answer and
invites summarizing a partial result, so we error instead of trimming.
"""

import json
import sys

from db import open_db, require_db, reconfigure_stdout


MAX_CELL_CHARS = 200
MAX_TABLE_CHARS = 16000


def format_table(rows, max_cell: int | None = MAX_CELL_CHARS):
    """Format rows as an aligned text table.

    Each value is collapsed to one line and truncated at ``max_cell`` chars
    (pass ``None`` to disable) so one oversized value can't pad every row out
    to its width.

    There is no row cap — every row is rendered. Callers that care about total
    size measure ``len()`` of the result and decide whether to refuse (see
    ``main()``). We deliberately never drop rows: a truncated table reads like
    a complete answer and invites the caller to summarize a partial result.
    """
    if not rows:
        return "No rows returned."

    keys = rows[0].keys()
    # Compute column widths
    widths = {k: len(k) for k in keys}
    str_rows = []
    for row in rows:
        str_row = {}
        for k in keys:
            val = row[k]
            s = "" if val is None else str(val)
            # Collapse whitespace/newlines so multi-line values stay aligned.
            s = " ".join(s.split())
            if max_cell is not None and len(s) > max_cell:
                s = s[: max_cell - 1] + "…"
            str_row[k] = s
            widths[k] = max(widths[k], len(s))
        str_rows.append(str_row)

    header = "  ".join(k.ljust(widths[k]) for k in keys)
    sep = "  ".join("-" * widths[k] for k in keys)
    lines = [header, sep]
    for sr in str_rows:
        lines.append("  ".join(sr[k].ljust(widths[k]) for k in keys))
    lines.append(f"\n({len(str_rows)} rows)")
    return "\n".join(lines)


def main():
    reconfigure_stdout()

    if len(sys.argv) < 2:
        print(__doc__.strip(), file=sys.stderr)
        sys.exit(1)

    sql = sys.argv[1]
    as_json = "--json" in sys.argv
    read_only = "--read-only" in sys.argv
    full = "--full" in sys.argv

    require_db()

    with open_db() as conn:
        if read_only:
            conn.execute("PRAGMA query_only = ON")
        try:
            cursor = conn.execute(sql)
        except Exception as e:
            print(f"SQL error: {e}", file=sys.stderr)
            sys.exit(1)

        # Detect if this is a SELECT (has results) or a write statement
        if cursor.description:
            rows = cursor.fetchall()
            if as_json:
                print(json.dumps([dict(r) for r in rows], indent=2, default=str))
            elif full:
                print(format_table(rows, max_cell=None))
            else:
                table = format_table(rows)
                # Refuse rather than truncate. A truncated table looks like a
                # complete answer; an error can't be mistaken for one. The
                # caller must re-scope (WHERE/GROUP BY/LIMIT) or opt into --full.
                if len(table) > MAX_TABLE_CHARS:
                    print(
                        f"Query returned {len(rows)} rows (~{len(table) // 1000}KB formatted), "
                        f"over the ~{MAX_TABLE_CHARS // 1000}KB output cap -- too much to read usefully.\n"
                        f"No rows printed. Re-scope the query instead of working from a partial result:\n"
                        f"  - Filter to what you need:    add WHERE (e.g. horizon='now', domain='...')\n"
                        f"  - For an overview, aggregate: SELECT col, COUNT(*) ... GROUP BY col\n"
                        f"  - Or bound it:                add LIMIT\n"
                        f"  - Need literally every row:   re-run with --full",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                print(table)
        else:
            conn.commit()
            print(f"{cursor.rowcount} row(s) affected.")


if __name__ == "__main__":
    main()
