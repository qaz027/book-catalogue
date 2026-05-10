"""Bulk-insert wishlist captures from a JSON list.

Input: JSON array on stdin or in a file. Each entry:
  {
    "title":         "Some Book",         # raw, as captured
    "author":        "Author Name",       # raw, as captured
    "isbn":          null,                # if visible in capture
    "source":        "X post by @user",   # human-readable provenance
    "source_url":    "https://x.com/...",
    "source_image":  "inbox/incoming/2026-05-09_x01.jpg",
    "priority":      0,                   # 0=whenever, higher=more wanted
    "notes":         null
  }

Behavior:
  - Try to match capture to an existing work by ISBN, then by title+author.
  - If matched: link wishlist row to the work_id (so 'do I own this?' lookup works).
  - If unmatched: leave work_id NULL; the capture stays in wishlist as raw text.
  - Always preserves title_raw/author_raw exactly as captured for review.

Usage:
    python3 scripts/add_to_wishlist.py < captures.json
    python3 scripts/add_to_wishlist.py --input captures.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    DEFAULT_DB, open_db, find_work_by_isbn, find_work_by_title_author,
)


def normalise_isbn_pair(isbn: str | None) -> tuple[str | None, str | None]:
    if not isbn:
        return None, None
    digits = "".join(c for c in str(isbn) if c.isalnum())
    if len(digits) == 13 and digits.isdigit():
        return digits, None
    if len(digits) == 10:
        return None, digits
    return None, None


def process_entry(conn, entry: dict) -> dict:
    title = (entry.get("title") or "").strip()
    author = (entry.get("author") or "").strip()
    if not title and not entry.get("source_image"):
        return {"ok": False, "reason": "need at least a title or a source_image",
                "entry": entry}

    isbn = entry.get("isbn")
    isbn_13, isbn_10 = normalise_isbn_pair(isbn)
    work_id = None
    if isbn_13 or isbn_10:
        work_id = find_work_by_isbn(conn, isbn_13, isbn_10, None)
    if work_id is None and title:
        work_id = find_work_by_title_author(conn, title, author or None)

    cur = conn.execute(
        """INSERT INTO wishlist(work_id, title_raw, author_raw, source,
                                source_url, source_image_path, priority, notes,
                                status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'wanted')""",
        (
            work_id,
            title or None,
            author or None,
            entry.get("source"),
            entry.get("source_url"),
            entry.get("source_image"),
            int(entry.get("priority") or 0),
            entry.get("notes"),
        ),
    )

    # If matched to a work the user already owns, flag it
    owned = False
    if work_id is not None:
        owned = conn.execute(
            """SELECT 1 FROM editions e
               JOIN copies c ON c.edition_id = e.id
               WHERE e.work_id = ? AND c.status = 'owned' LIMIT 1""",
            (work_id,),
        ).fetchone() is not None

    return {
        "ok": True,
        "wishlist_id": cur.lastrowid,
        "title": title,
        "matched_work_id": work_id,
        "already_owned": owned,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", type=Path)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    raw = args.input.read_text(encoding="utf-8") if args.input else sys.stdin.read()
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as e:
        json.dump({"ok": False, "error": f"invalid JSON: {e}"}, sys.stdout)
        sys.stdout.write("\n")
        sys.exit(1)
    if not isinstance(entries, list):
        json.dump({"ok": False, "error": "expected a JSON array"}, sys.stdout)
        sys.stdout.write("\n")
        sys.exit(1)

    conn = open_db(args.db)
    results = []
    stats = {"total": len(entries), "ok": 0, "failed": 0,
             "matched": 0, "already_owned": 0}

    for entry in entries:
        try:
            r = process_entry(conn, entry)
        except Exception as e:
            r = {"ok": False, "reason": str(e), "entry": entry}
        results.append(r)
        if r.get("ok"):
            stats["ok"] += 1
            if r.get("matched_work_id"): stats["matched"] += 1
            if r.get("already_owned"): stats["already_owned"] += 1
        else:
            stats["failed"] += 1

    if args.dry_run:
        conn.rollback()
    else:
        conn.commit()
    conn.close()

    json.dump({"results": results, "stats": stats, "dry_run": args.dry_run},
              sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
