"""Change the status of one or more copies (loan-out, sold, lost, returned, etc.).

Same shape as move_copies.py — resolve by copy_id or by title (+author/+location),
return ok/ambiguous/not_found/same_status per entry, append a dated audit line
to copies.notes, and update copies.updated_at.

Input: JSON array on stdin or in a file. Each entry:
  {
    "copy_id":          42,                 # preferred: direct ID
    "title":            "Dune",             # fallback: lookup by title
    "author":           "Frank Herbert",
    "current_location": "living room shelf 3",
    "new_status":       "loaned-out",       # REQUIRED
    "borrowed_until":   "2026-06-15",       # for new_status='borrowed' (Libby etc.)
    "loaned_to":        "Sarah",            # for new_status='loaned-out'
    "note":             null,
    "source_image":     "inbox/incoming/..."
  }

Allowed new_status values (from schema CHECK):
  owned, borrowed, loaned-out, sold, returned, lost

Common transitions:
  - "I lent X to Y"         → new_status: 'loaned-out', loaned_to: 'Y'
  - "Y returned X to me"    → new_status: 'owned' (clears loaned_to in audit)
  - "I borrowed X from Libby" → new_status: 'borrowed', borrowed_until: date
  - "I returned X to library" → new_status: 'returned'
  - "Sold X"                → new_status: 'sold'
  - "Lost X"                → new_status: 'lost'
  - "Found X"               → new_status: 'owned'

Usage:
    python3 scripts/change_status.py < changes.json
    python3 scripts/change_status.py --input changes.json --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DEFAULT_DB, open_db, normalise_title

ALLOWED = ("owned", "borrowed", "loaned-out", "sold", "returned", "lost")


def find_candidates(conn, entry: dict) -> list[dict]:
    sql_base = """
      SELECT c.id AS copy_id, c.status, c.location_id, c.borrowed_until,
             c.medium, l.name AS current_location,
             e.id AS edition_id, e.format, w.id AS work_id,
             w.title, w.author_display
      FROM copies c
      JOIN editions e ON e.id = c.edition_id
      JOIN works    w ON w.id = e.work_id
      LEFT JOIN locations l ON l.id = c.location_id
      WHERE 1=1
    """
    if entry.get("copy_id"):
        rows = conn.execute(sql_base + " AND c.id = ?", (entry["copy_id"],)).fetchall()
        return [dict(r) for r in rows]

    title = (entry.get("title") or "").strip()
    if not title:
        return []
    target = normalise_title(title)
    author = (entry.get("author") or "").strip().lower()
    current = (entry.get("current_location") or "").strip().lower()
    all_rows = conn.execute(sql_base).fetchall()
    matches = []
    for r in all_rows:
        if normalise_title(r["title"] or "") != target:
            continue
        if author:
            ad = (r["author_display"] or "").lower()
            first_token = author.split(",")[0].split()[-1] if author.split() else ""
            if first_token and first_token not in ad:
                continue
        if current:
            cur = (r["current_location"] or "").lower()
            if current not in cur:
                continue
        matches.append(dict(r))
    return matches


def apply_change(conn, copy: dict, entry: dict) -> dict:
    new_status = entry["new_status"]
    old_status = copy["status"]
    if old_status == new_status:
        return {
            "status": "same_status", "copy_id": copy["copy_id"],
            "title": copy["title"], "current_status": old_status,
        }

    today = dt.date.today().isoformat()
    parts = [f"[{today}] {old_status} → {new_status}"]
    if entry.get("loaned_to"):
        parts.append(f"to {entry['loaned_to']}")
    if entry.get("borrowed_until"):
        parts.append(f"due {entry['borrowed_until']}")
    if entry.get("note"):
        parts.append(f"— {entry['note']}")
    if entry.get("source_image"):
        parts.append(f"(from {entry['source_image']})")
    audit = " ".join(parts)

    existing_notes = conn.execute(
        "SELECT notes FROM copies WHERE id = ?", (copy["copy_id"],)
    ).fetchone()[0]
    new_notes = f"{existing_notes}\n{audit}" if existing_notes else audit

    borrowed_until = entry.get("borrowed_until") if new_status == "borrowed" else None
    # If transitioning away from 'borrowed', clear the due date
    if old_status == "borrowed" and new_status != "borrowed":
        borrowed_until_value = None
    else:
        borrowed_until_value = borrowed_until

    conn.execute(
        """UPDATE copies
           SET status = ?, borrowed_until = ?, notes = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (new_status, borrowed_until_value, new_notes, copy["copy_id"]),
    )
    return {
        "status": "ok",
        "copy_id": copy["copy_id"],
        "title": copy["title"],
        "from": old_status,
        "to": new_status,
        "loaned_to": entry.get("loaned_to"),
        "borrowed_until": borrowed_until_value,
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
    stats = {"total": len(entries), "ok": 0, "ambiguous": 0,
             "not_found": 0, "same_status": 0, "errors": 0}

    for entry in entries:
        try:
            ns = entry.get("new_status")
            if ns not in ALLOWED:
                results.append({
                    "status": "error",
                    "reason": f"new_status must be one of {ALLOWED}",
                    "entry": entry,
                })
                stats["errors"] += 1
                continue
            candidates = find_candidates(conn, entry)
            if not candidates:
                results.append({"status": "not_found", "entry": entry})
                stats["not_found"] += 1
            elif len(candidates) > 1:
                results.append({
                    "status": "ambiguous", "entry": entry,
                    "candidates": [
                        {"copy_id": c["copy_id"], "title": c["title"],
                         "author": c["author_display"], "format": c["format"],
                         "current_location": c["current_location"],
                         "current_status": c["status"]}
                        for c in candidates
                    ],
                })
                stats["ambiguous"] += 1
            else:
                r = apply_change(conn, candidates[0], entry)
                results.append(r)
                if r["status"] == "ok":
                    stats["ok"] += 1
                elif r["status"] == "same_status":
                    stats["same_status"] += 1
        except Exception as e:
            results.append({"status": "error", "reason": str(e), "entry": entry})
            stats["errors"] += 1

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
