"""Print a snapshot of the catalog: counts, breakdowns, recent activity,
and useful flags (already-owned wishlist entries, missing metadata,
currently-borrowed-from-Libby items past their due date).

Usage:
    python3 scripts/report.py
    python3 scripts/report.py --db library.db
    python3 scripts/report.py --json     # machine-readable output
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DEFAULT_DB, open_db


def gather(conn) -> dict:
    cur = conn.cursor()
    totals = {
        "works":          cur.execute("SELECT COUNT(*) FROM works").fetchone()[0],
        "editions":       cur.execute("SELECT COUNT(*) FROM editions").fetchone()[0],
        "copies":         cur.execute("SELECT COUNT(*) FROM copies").fetchone()[0],
        "wishlist_open":  cur.execute("SELECT COUNT(*) FROM wishlist WHERE status='wanted'").fetchone()[0],
    }

    by_medium = {r["medium"]: r["n"] for r in cur.execute(
        "SELECT medium, COUNT(*) AS n FROM copies GROUP BY medium ORDER BY n DESC"
    )}
    by_status = {r["status"]: r["n"] for r in cur.execute(
        "SELECT status, COUNT(*) AS n FROM copies GROUP BY status ORDER BY n DESC"
    )}
    by_format = {r["format"]: r["n"] for r in cur.execute(
        "SELECT format, COUNT(*) AS n FROM copies c JOIN editions e ON e.id=c.edition_id "
        "GROUP BY format ORDER BY n DESC"
    )}
    by_location = [{"location": r["loc"], "count": r["n"]} for r in cur.execute(
        "SELECT COALESCE(l.name, '(no location)') AS loc, COUNT(*) AS n "
        "FROM copies c LEFT JOIN locations l ON l.id=c.location_id "
        "WHERE c.medium='physical' "
        "GROUP BY l.name ORDER BY n DESC, loc"
    )]
    by_vendor = [{"vendor": r["v"], "count": r["n"]} for r in cur.execute(
        "SELECT COALESCE(v.name, '(no vendor)') AS v, COUNT(*) AS n "
        "FROM copies c LEFT JOIN vendors v ON v.id=c.vendor_id "
        "WHERE c.medium IN ('digital','audio') "
        "GROUP BY v.name ORDER BY n DESC, v"
    )]

    recent = [dict(r) for r in cur.execute("""
        SELECT w.id, w.title, w.author_display, w.original_year,
               c.created_at, c.medium,
               COALESCE(l.name, v.name) AS where_at
        FROM copies c JOIN editions e ON e.id=c.edition_id JOIN works w ON w.id=e.work_id
        LEFT JOIN locations l ON l.id=c.location_id
        LEFT JOIN vendors   v ON v.id=c.vendor_id
        ORDER BY c.id DESC LIMIT 10
    """)]

    flags = {}

    flags["wishlist_already_owned"] = [dict(r) for r in cur.execute("""
        SELECT wl.id, wl.title_raw, wl.author_raw, w.title AS matched_title
        FROM wishlist wl
        JOIN works    w ON w.id = wl.work_id
        JOIN editions e ON e.work_id = wl.work_id
        JOIN copies   c ON c.edition_id = e.id
        WHERE wl.status = 'wanted' AND c.status = 'owned'
        GROUP BY wl.id
    """)]

    flags["loaned_out"] = [dict(r) for r in cur.execute("""
        SELECT c.id AS copy_id, w.title, c.notes
        FROM copies c JOIN editions e ON e.id=c.edition_id JOIN works w ON w.id=e.work_id
        WHERE c.status = 'loaned-out'
    """)]

    today = dt.date.today().isoformat()
    flags["overdue_borrows"] = [dict(r) for r in cur.execute("""
        SELECT c.id AS copy_id, w.title, c.borrowed_until, v.name AS vendor
        FROM copies c JOIN editions e ON e.id=c.edition_id JOIN works w ON w.id=e.work_id
        LEFT JOIN vendors v ON v.id=c.vendor_id
        WHERE c.status = 'borrowed' AND c.borrowed_until IS NOT NULL
          AND c.borrowed_until < ?
    """, (today,))]

    flags["works_missing_year"] = cur.execute(
        "SELECT COUNT(*) FROM works WHERE original_year IS NULL"
    ).fetchone()[0]

    flags["works_missing_isbn"] = cur.execute("""
        SELECT COUNT(*) FROM works w
        WHERE NOT EXISTS (SELECT 1 FROM editions e
                          WHERE e.work_id=w.id AND e.isbn_13 IS NOT NULL)
    """).fetchone()[0]

    return {
        "totals": totals,
        "by_medium": by_medium,
        "by_status": by_status,
        "by_format": by_format,
        "by_location": by_location,
        "by_vendor": by_vendor,
        "recent": recent,
        "flags": flags,
    }


def render_text(d: dict) -> str:
    out = []
    t = d["totals"]
    out.append(f"  Works         {t['works']:>5}")
    out.append(f"  Editions      {t['editions']:>5}")
    out.append(f"  Copies        {t['copies']:>5}")
    out.append(f"  Wishlist open {t['wishlist_open']:>5}")
    out.append("")

    if d["by_medium"]:
        out.append("By medium:")
        for k, v in d["by_medium"].items():
            out.append(f"  {k:12s} {v}")
        out.append("")
    if d["by_status"]:
        out.append("By status:")
        for k, v in d["by_status"].items():
            out.append(f"  {k:12s} {v}")
        out.append("")
    if d["by_format"]:
        out.append("By format:")
        for k, v in d["by_format"].items():
            out.append(f"  {k:14s} {v}")
        out.append("")
    if d["by_location"]:
        out.append("Physical copies by location:")
        for r in d["by_location"]:
            out.append(f"  {r['location']:40s} {r['count']}")
        out.append("")
    if d["by_vendor"]:
        out.append("Digital/audio copies by vendor:")
        for r in d["by_vendor"]:
            out.append(f"  {r['vendor']:30s} {r['count']}")
        out.append("")

    flags = d["flags"]
    out.append("Flags:")
    out.append(f"  Works missing original_year: {flags['works_missing_year']}")
    out.append(f"  Works missing ISBN-13:       {flags['works_missing_isbn']}")
    if flags["wishlist_already_owned"]:
        out.append(f"  Wishlist entries you ALREADY own: {len(flags['wishlist_already_owned'])}")
        for r in flags["wishlist_already_owned"][:5]:
            out.append(f"    - {r['title_raw'] or r['matched_title']}")
        if len(flags["wishlist_already_owned"]) > 5:
            out.append(f"    ...and {len(flags['wishlist_already_owned']) - 5} more")
    if flags["loaned_out"]:
        out.append(f"  Currently loaned out: {len(flags['loaned_out'])}")
        for r in flags["loaned_out"]:
            out.append(f"    - {r['title']}  (copy {r['copy_id']})")
    if flags["overdue_borrows"]:
        out.append(f"  Overdue borrows: {len(flags['overdue_borrows'])}")
        for r in flags["overdue_borrows"]:
            out.append(f"    - {r['title']}  from {r['vendor']}  due {r['borrowed_until']}")
    out.append("")

    out.append("Recent additions:")
    for r in d["recent"]:
        date = (r["created_at"] or "")[:10]
        out.append(f"  {date}  {r['title']}  ({r['where_at'] or '-'})")

    return "\n".join(out)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--json", action="store_true", help="Output JSON instead of text")
    args = p.parse_args()

    conn = open_db(args.db)
    data = gather(conn)
    conn.close()

    if args.json:
        json.dump(data, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        print(render_text(data))


if __name__ == "__main__":
    main()
