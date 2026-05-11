"""Move existing physical copies to a new location.

Use this when a book that's already catalogued has physically moved —
shelf to box, box to a different shelf, etc. It's an UPDATE on
copies.location_id, NOT a new copy.

Input: JSON array on stdin or in a file. Each entry:
  {
    "copy_id":      42,                   # preferred: direct ID
    "title":        "Dune",               # fallback: lookup by title (+ author)
    "author":       "Frank Herbert",
    "current_location": "living room shelf 3",  # optional disambiguator
    "destination":  "Storage Box A",      # REQUIRED — new location name
    "source_image": "inbox/incoming/...", # optional, for audit trail
    "note":         null                  # optional free-text note
  }

Resolution:
  - If `copy_id` is given, that wins; we just confirm it exists.
  - Otherwise: look up copies by title (+ author, + current_location).
  - Returns one of:
      ok           — moved successfully
      not_found    — no matching copy
      ambiguous    — multiple matches; caller must re-submit with copy_id
      same_location — already at destination; no-op

Side-effects:
  - Creates the destination `locations` row if it doesn't exist.
  - Sets copies.updated_at = now.
  - Appends a dated audit line to copies.notes:
      "[YYYY-MM-DD] moved 'old' → 'new'"
  - Does NOT delete or recreate the copy; copy_id is stable across moves.

Usage:
    python3 scripts/move_copies.py < moves.json
    python3 scripts/move_copies.py --input moves.json --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DEFAULT_DB, open_db, location_id, normalise_title


def find_candidates(conn, entry: dict) -> list[dict]:
    """Return matching copies as plain dicts."""
    sql_base = """
      SELECT c.id AS copy_id, c.location_id, l.name AS current_location,
             c.medium, c.status,
             e.id AS edition_id, e.format, w.id AS work_id,
             w.title, w.author_display
      FROM copies c
      JOIN editions e ON e.id = c.edition_id
      JOIN works    w ON w.id = e.work_id
      LEFT JOIN locations l ON l.id = c.location_id
      WHERE c.medium = 'physical'
    """
    if entry.get("copy_id"):
        rows = conn.execute(sql_base + " AND c.id = ?", (entry["copy_id"],)).fetchall()
        return [dict(r) for r in rows]

    title = (entry.get("title") or "").strip()
    if not title:
        return []
    target_norm = normalise_title(title)
    author = (entry.get("author") or "").strip().lower()
    current = (entry.get("current_location") or "").strip().lower()

    all_rows = conn.execute(sql_base).fetchall()
    matches = []
    for r in all_rows:
        if normalise_title(r["title"] or "") != target_norm:
            continue
        if author:
            ad = (r["author_display"] or "").lower()
            # first author surname coarse match
            first_token = author.split(",")[0].split()[-1] if author.split() else ""
            if first_token and first_token not in ad:
                continue
        if current:
            cur = (r["current_location"] or "").lower()
            if current not in cur:
                continue
        matches.append(dict(r))
    return matches


def apply_move(conn, copy_row: dict, destination: str, note: str | None,
               source_image: str | None) -> dict:
    current = (copy_row.get("current_location") or "").strip()
    if current and current.lower() == destination.strip().lower():
        return {
            "status": "same_location",
            "copy_id": copy_row["copy_id"],
            "title": copy_row["title"],
            "location": current,
        }

    dest_id = location_id(conn, destination)
    today = dt.date.today().isoformat()
    audit = f"[{today}] moved '{current or '(none)'}' → '{destination}'"
    if source_image:
        audit += f" (from {source_image})"
    if note:
        audit += f" — {note}"

    existing_notes = conn.execute(
        "SELECT notes FROM copies WHERE id = ?", (copy_row["copy_id"],)
    ).fetchone()["notes"]
    new_notes = f"{existing_notes}\n{audit}" if existing_notes else audit

    conn.execute(
        """UPDATE copies
           SET location_id = ?, notes = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (dest_id, new_notes, copy_row["copy_id"]),
    )
    return {
        "status": "ok",
        "copy_id": copy_row["copy_id"],
        "title": copy_row["title"],
        "from": current or None,
        "to": destination,
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
    stats = {"total": len(entries), "moved": 0, "ambiguous": 0,
             "not_found": 0, "same_location": 0, "errors": 0}

    for entry in entries:
        try:
            dest = (entry.get("destination") or "").strip()
            if not dest:
                results.append({"status": "error", "reason": "missing destination",
                                "entry": entry})
                stats["errors"] += 1
                continue
            candidates = find_candidates(conn, entry)
            if not candidates:
                results.append({"status": "not_found", "entry": entry})
                stats["not_found"] += 1
            elif len(candidates) > 1:
                results.append({
                    "status": "ambiguous",
                    "entry": entry,
                    "candidates": [
                        {"copy_id": c["copy_id"], "title": c["title"],
                         "author": c["author_display"], "format": c["format"],
                         "current_location": c["current_location"]}
                        for c in candidates
                    ],
                })
                stats["ambiguous"] += 1
            else:
                r = apply_move(conn, candidates[0], dest,
                               entry.get("note"), entry.get("source_image"))
                results.append(r)
                if r["status"] == "ok":
                    stats["moved"] += 1
                elif r["status"] == "same_location":
                    stats["same_location"] += 1
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
