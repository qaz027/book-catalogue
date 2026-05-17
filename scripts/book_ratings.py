"""Cross-reference the catalog (library.db) against a Goodreads export to surface
which owned books you've rated, how high, and which aren't tracked at all.

Goodreads exports only carry **your** ratings (`My Rating`), not the community
average. So this script answers questions like:
  - "Which of my owned books did I rate <= 2 stars?" (strong cull candidates)
  - "Which owned books did I rate 5?" (keep / re-read)
  - "Which owned books aren't in Goodreads at all?" (never logged — likely
     unread reference material or impulse buys)

It does NOT hit any external APIs; everything is local. Re-run any time after
re-exporting from Goodreads and dropping the CSV into data/raw/.

Usage:
    python3 scripts/book_ratings.py
    python3 scripts/book_ratings.py --csv data/raw/goodreads_library_export.csv
    python3 scripts/book_ratings.py --md-out reports/ratings.md
    python3 scripts/book_ratings.py --min-rating 0 --max-rating 2   # culls only
"""
from __future__ import annotations

import argparse
import csv
import difflib
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO / "library.db"
DEFAULT_CSV = REPO / "data/raw/goodreads_library_export.csv"
DEFAULT_CSV_OUT = REPO / "reports/ratings.csv"
DEFAULT_MD_OUT = REPO / "reports/ratings.md"


# --- normalisation helpers (same convention as the dedupe step) ---------------

def norm_title(s: str) -> str:
    if not s:
        return ""
    s = s.lower().split(":")[0]
    s = re.sub(r"\b(\d+e|\d+(st|nd|rd|th)\s*edition|second|third|fourth|fifth|"
               r"sixth|seventh|eighth|ninth|tenth|revised|updated|expanded|new|"
               r"completely|anniversary)\s*edition\b", " ", s)
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"^(the|a|an)\s+", "", s)


def last_name(author: str) -> str:
    if not author:
        return ""
    first = author.split(",")[0].strip().split(";")[0]
    parts = first.split()
    return parts[-1].lower() if parts else ""


# --- core matcher -------------------------------------------------------------

def build_goodreads_index(csv_path: Path) -> tuple[list[dict], dict, dict]:
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append({
                "title":   r.get("Title", "").strip(),
                "author":  r.get("Author", "").strip(),
                "rating":  int(r.get("My Rating") or 0),
                "shelf":   r.get("Exclusive Shelf", "").strip(),
                "shelves": r.get("Bookshelves", "").strip(),
                "date_read": r.get("Date Read", "").strip(),
                "_nt": norm_title(r.get("Title", "")),
                "_na": last_name(r.get("Author", "")),
            })
    by_pair = {}      # (nt, last) -> [entries]
    by_author = {}    # last -> [entries]
    for g in rows:
        by_pair.setdefault((g["_nt"], g["_na"]), []).append(g)
        if g["_na"]:
            by_author.setdefault(g["_na"], []).append(g)
    return rows, by_pair, by_author


def match(title: str, author: str, by_pair: dict, by_author: dict, threshold: float = 0.78):
    nt, na = norm_title(title), last_name(author or "")
    if (nt, na) in by_pair:
        return by_pair[(nt, na)][0], "exact"
    if na in by_author:
        best, score = None, 0.0
        for g in by_author[na]:
            r = difflib.SequenceMatcher(None, nt, g["_nt"]).ratio()
            if nt and (g["_nt"].startswith(nt) or nt.startswith(g["_nt"])):
                r = max(r, 0.92)
            if r > score:
                score, best = r, g
        if score >= threshold:
            return best, f"fuzzy({score:.2f})"
    return None, "no_match"


# --- report -------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    p.add_argument("--csv-out", type=Path, default=DEFAULT_CSV_OUT)
    p.add_argument("--md-out", type=Path, default=DEFAULT_MD_OUT)
    p.add_argument("--min-rating", type=int, default=None,
                   help="only include rows with rating >= this (1..5)")
    p.add_argument("--max-rating", type=int, default=None,
                   help="only include rows with rating <= this (1..5)")
    p.add_argument("--unmatched-only", action="store_true",
                   help="only output books not found in Goodreads")
    args = p.parse_args()

    if not args.csv.exists():
        sys.exit(f"Goodreads CSV not found at {args.csv}")

    _, by_pair, by_author = build_goodreads_index(args.csv)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    works = conn.execute(
        """SELECT w.id, w.title, w.author_display,
                  COUNT(c.id) AS n_copies
             FROM works w
             JOIN editions e ON e.work_id = w.id
             JOIN copies   c ON c.edition_id = e.id
            WHERE c.status = 'owned'
         GROUP BY w.id
         ORDER BY w.title"""
    ).fetchall()

    rows_out = []
    stats = Counter()
    shelf_counts = Counter()
    rating_counts = Counter()
    for w in works:
        m, how = match(w["title"], w["author_display"], by_pair, by_author)
        rating = m["rating"] if m else None
        shelf = m["shelf"] if m else None
        gr_title = m["title"] if m else ""
        date_read = m["date_read"] if m else ""
        stats["total"] += 1
        if m:
            stats["matched"] += 1
            shelf_counts[shelf or "(blank)"] += 1
            rating_counts[rating] += 1
        else:
            stats["unmatched"] += 1
        if args.unmatched_only and m:
            continue
        if args.min_rating is not None and (rating is None or rating < args.min_rating):
            continue
        if args.max_rating is not None and (rating is None or rating > args.max_rating):
            continue
        rows_out.append({
            "work_id": w["id"],
            "title": w["title"],
            "author": w["author_display"] or "",
            "n_copies": w["n_copies"],
            "my_rating": rating if rating is not None else "",
            "goodreads_shelf": shelf or "",
            "date_read": date_read,
            "match_how": how,
            "goodreads_title": gr_title,
        })

    args.csv_out.parent.mkdir(parents=True, exist_ok=True)
    args.md_out.parent.mkdir(parents=True, exist_ok=True)

    with open(args.csv_out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows_out[0].keys()) if rows_out
                                          else ["work_id","title","author","n_copies",
                                                "my_rating","goodreads_shelf","date_read",
                                                "match_how","goodreads_title"])
        w.writeheader()
        for r in rows_out:
            w.writerow(r)

    # Build markdown report
    lines = []
    lines.append("# Book Ratings Report\n")
    lines.append(f"**Catalog works (owned):** {stats['total']}  ")
    lines.append(f"**Matched to Goodreads:** {stats['matched']} "
                 f"({100*stats['matched']/max(stats['total'],1):.1f}%)  ")
    lines.append(f"**Unmatched (no Goodreads entry):** {stats['unmatched']}\n")
    lines.append("## Match shelf distribution\n")
    for k, v in shelf_counts.most_common():
        lines.append(f"- **{k}**: {v}")
    lines.append("\n## Rating distribution (My Rating)\n")
    for r in [5, 4, 3, 2, 1, 0]:
        n = rating_counts.get(r, 0)
        label = "★" * r + "☆" * (5-r) if r else "unrated (0)"
        lines.append(f"- **{label}**: {n}")

    def section(title: str, predicate):
        lines.append(f"\n## {title}\n")
        sub = [r for r in rows_out if predicate(r)]
        if not sub:
            lines.append("_(none)_\n")
            return
        lines.append("| Rating | Title | Author | Copies | Shelf |")
        lines.append("|--------|-------|--------|--------|-------|")
        for r in sorted(sub, key=lambda x: (-(x["my_rating"] or 0), x["title"])):
            rating = r["my_rating"]
            stars = "★" * rating if rating else "—"
            lines.append(f"| {stars} | {r['title']} | {r['author']} | "
                         f"{r['n_copies']} | {r['goodreads_shelf']} |")

    # Sections useful for culling
    section("⚠️ Rated 1–2 stars (strong cull candidates)",
            lambda r: isinstance(r["my_rating"], int) and 1 <= r["my_rating"] <= 2)
    section("Rated 3 stars (lukewarm — consider)",
            lambda r: r["my_rating"] == 3)
    section("Rated 5 stars (keepers)",
            lambda r: r["my_rating"] == 5)
    section("Rated 4 stars",
            lambda r: r["my_rating"] == 4)
    section("On 'to-read' shelf but already owned",
            lambda r: r["goodreads_shelf"] == "to-read")
    section("'Read' on Goodreads but no rating given",
            lambda r: r["goodreads_shelf"] == "read" and r["my_rating"] == 0)

    args.md_out.write_text("\n".join(lines))

    print(f"Wrote {args.csv_out}")
    print(f"Wrote {args.md_out}")
    print()
    print(f"Total owned works:   {stats['total']}")
    print(f"In Goodreads:        {stats['matched']} ({100*stats['matched']/max(stats['total'],1):.1f}%)")
    print(f"Not in Goodreads:    {stats['unmatched']}")
    print()
    print("Rating breakdown of matched books:")
    for r in [5, 4, 3, 2, 1, 0]:
        n = rating_counts.get(r, 0)
        label = ("★" * r) if r else "unrated"
        print(f"  {label:7s}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
