"""Bulk-insert books from a JSON list. Non-interactive — designed to be called
from automation (e.g. Claude processing the inbox).

Input: JSON array on stdin or in a file. Each entry:
  {
    "isbn":          "9780441013593",     # optional; takes priority for metadata
    "asin":          "B000R34YKM",        # optional
    "title":         "Dune",              # required if no isbn/asin lookup
    "subtitle":      null,
    "author":        "Frank Herbert",     # comma-separated for multiple
    "year":          1965,
    "publisher":     null,
    "pages":         688,
    "format":        "mass-market",       # see schema.sql for valid values
    "medium":        "physical",          # physical | digital | audio
    "location":      "Living room shelf 3",   # for physical
    "vendor":        "Amazon Kindle",         # for digital/audio
    "condition":     "good",
    "acquired_date": "2018-04-12",
    "acquired_price": 12.99,
    "acquired_source": "Powell's Books",
    "status":        "owned",
    "source_image":  "inbox/incoming/2026-05-09_shelf01.jpg",
    "notes":         null
  }

Behavior:
  - If isbn or asin provided, look up authoritative metadata via Open Library /
    Google Books and merge with the user-provided fields (user fields win on
    conflict so you can correct lookups).
  - upsert work, upsert edition, insert copy.
  - Inserts a copy unconditionally; dedupe is the caller's responsibility.
  - Logs source_image into copies.notes for traceability.
  - Returns a JSON summary on stdout: {results: [...], stats: {...}}.

Usage:
    python3 scripts/add_book_batch.py < batch.json
    python3 scripts/add_book_batch.py --input batch.json
    python3 scripts/add_book_batch.py --dry-run < batch.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    DEFAULT_DB, open_db, upsert_author, link_work_author,
    upsert_work, upsert_edition, vendor_id, location_id, parse_int, parse_year,
)


def fetch_metadata(isbn: str | None, asin: str | None,
                   title: str | None = None, author: str | None = None) -> dict:
    """Best-effort enrichment. Tries ISBN lookup first; falls back to
    title+author search (Open Library, then Google Books) when no ISBN.
    Returns a dict with normalised keys: title, subtitle, authors,
    publishers, published_year, pages, subjects, cover_url, isbn_13,
    isbn_10, source."""
    # ISBN path (most reliable)
    if isbn:
        try:
            from add_book import lookup_isbn
        except Exception:
            return {}
        return lookup_isbn(isbn) or {}
    # Title+author path
    if title:
        try:
            from enrich_metadata import lookup as lookup_by_title_author
        except Exception:
            return {}
        rec = lookup_by_title_author(title, author or None)
        if not rec:
            return {}
        return {
            "title":          rec.get("title"),
            "subtitle":       None,
            "authors":        rec.get("authors") or [],
            "publishers":     [rec["publisher"]] if rec.get("publisher") else [],
            "published_year": rec.get("first_publish_year"),
            "pages":          rec.get("pages"),
            "subjects":       rec.get("subjects") or [],
            "cover_url":      rec.get("cover_url"),
            "isbn_13":        rec.get("isbn_13"),
            "isbn_10":        rec.get("isbn_10"),
            "source":         rec.get("source"),
        }
    return {}


def normalise_isbn_pair(isbn: str | None) -> tuple[str | None, str | None]:
    if not isbn:
        return None, None
    digits = "".join(c for c in str(isbn) if c.isalnum())
    if len(digits) == 13 and digits.isdigit():
        return digits, None
    if len(digits) == 10:
        return None, digits
    return None, None


def process_entry(conn, entry: dict, dry_run: bool) -> dict:
    isbn = entry.get("isbn") or None
    asin = entry.get("asin") or None
    isbn_13, isbn_10 = normalise_isbn_pair(isbn)

    # Enrich from external lookup, but user-supplied fields override.
    # When no ISBN is provided, fall back to title+author search.
    looked_up = fetch_metadata(
        isbn_13 or isbn_10, asin,
        title=entry.get("title"),
        author=entry.get("author") or entry.get("authors"),
    )

    # Adopt looked-up ISBN if the user didn't supply one
    if not isbn_13 and looked_up.get("isbn_13"):
        isbn_13 = looked_up["isbn_13"]
    if not isbn_10 and looked_up.get("isbn_10"):
        isbn_10 = looked_up["isbn_10"]

    title = entry.get("title") or looked_up.get("title")
    if not title:
        return {"ok": False, "reason": "missing title and no ISBN match", "entry": entry}

    authors_raw = entry.get("author") or entry.get("authors")
    if isinstance(authors_raw, str):
        authors = [a.strip() for a in authors_raw.split(",") if a.strip()]
    elif isinstance(authors_raw, list):
        authors = [a.strip() for a in authors_raw if a and a.strip()]
    else:
        authors = looked_up.get("authors") or []

    author_display = ", ".join(authors) if authors else None
    publisher = entry.get("publisher") or (looked_up.get("publishers") or [None])[0]
    year = parse_year(entry.get("year")) or looked_up.get("published_year")
    pages = parse_int(entry.get("pages")) or parse_int(looked_up.get("pages"))
    subtitle = entry.get("subtitle") or looked_up.get("subtitle")
    subjects = ", ".join(looked_up.get("subjects") or []) or None
    cover_url = looked_up.get("cover_url")

    work_id, work_created = upsert_work(
        conn,
        title=title,
        subtitle=subtitle,
        author_display=author_display,
        original_year=year,
        subjects=subjects,
        isbn_13=isbn_13, isbn_10=isbn_10, asin=asin,
    )
    if work_created:
        for i, name in enumerate(authors):
            aid = upsert_author(conn, name)
            link_work_author(conn, work_id, aid, role="author", sort_order=i)

    fmt = entry.get("format") or "paperback"
    edition_id, ed_created = upsert_edition(
        conn,
        work_id=work_id,
        format=fmt,
        isbn_13=isbn_13, isbn_10=isbn_10, asin=asin,
        publisher=publisher,
        published_year=year,
        pages=pages,
        cover_url=cover_url,
    )

    medium = entry.get("medium") or "physical"
    if medium not in ("physical", "digital", "audio"):
        return {"ok": False, "reason": f"invalid medium '{medium}'", "entry": entry}

    loc_id = vend_id = None
    if entry.get("location"):
        loc_id = location_id(conn, entry["location"])
    if entry.get("vendor"):
        vend_id = vendor_id(conn, entry["vendor"])
        if vend_id is None:
            kind = "audio" if medium == "audio" else "digital"
            cur = conn.execute(
                "INSERT INTO vendors(name, kind) VALUES (?, ?)",
                (entry["vendor"], kind),
            )
            vend_id = cur.lastrowid

    notes = entry.get("notes")
    if entry.get("source_image"):
        trace = f"Imported from {entry['source_image']}"
        notes = f"{notes}\n{trace}" if notes else trace

    cur = conn.execute(
        """INSERT INTO copies(edition_id, medium, location_id, vendor_id, vendor_book_id,
                              file_path, condition, status, borrowed_until,
                              acquired_date, acquired_price, acquired_source, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            edition_id, medium, loc_id, vend_id,
            asin if asin and medium != "physical" else None,
            entry.get("file_path"),
            entry.get("condition"),
            entry.get("status") or "owned",
            entry.get("borrowed_until"),
            entry.get("acquired_date"),
            entry.get("acquired_price"),
            entry.get("acquired_source"),
            notes,
        ),
    )
    copy_id = cur.lastrowid
    return {
        "ok": True,
        "work_id": work_id,
        "edition_id": edition_id,
        "copy_id": copy_id,
        "title": title,
        "author": author_display,
        "format": fmt,
        "medium": medium,
        "work_created": work_created,
        "edition_created": ed_created,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", type=Path, help="JSON file path (default: stdin)")
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
             "works_created": 0, "editions_created": 0}

    for entry in entries:
        try:
            r = process_entry(conn, entry, args.dry_run)
        except Exception as e:
            r = {"ok": False, "reason": str(e), "entry": entry}
        results.append(r)
        if r.get("ok"):
            stats["ok"] += 1
            if r.get("work_created"): stats["works_created"] += 1
            if r.get("edition_created"): stats["editions_created"] += 1
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
