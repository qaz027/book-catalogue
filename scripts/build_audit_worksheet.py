"""Build a one-row-per-work CSV worksheet for shelf-audit / decluttering passes.

Columns:
  decision         -- blank; user fills in KEEP / DONATE / SELL / KINDLE / UNSURE
  notes            -- blank; user free-text
  category         -- from works.subjects (populated by categorize_books.py)
  title            -- catalog title
  author           -- catalog author_display
  n_copies         -- how many physical copies you own
  my_rating        -- your Goodreads rating (1-5) if matched, blank otherwise
  goodreads_shelf  -- read / to-read / currently-reading / blank
  goodreads_title  -- the matched Goodreads title (helps you double-check)
  flag             -- cull-candidate priority + reason if surfaced by cull_candidates rules
  work_id          -- library.db works.id (so you can write back changes later)

Open in your spreadsheet of choice, sort by category, walk the shelves, fill in
the decision column. Re-import via a follow-up script when you're done.

Usage:
    python3 scripts/build_audit_worksheet.py
    python3 scripts/build_audit_worksheet.py --out reports/shelf_audit_2026-05-16.csv
"""
from __future__ import annotations

import argparse
import csv
import difflib
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO / "library.db"
DEFAULT_GR = REPO / "data/raw/goodreads_library_export.csv"
DEFAULT_OUT = REPO / "reports" / f"shelf_audit_worksheet_{date.today().isoformat()}.csv"


# --- matchers (kept in sync with book_ratings.py / cull_candidates.py) --------

def norm_title(s: str) -> str:
    if not s: return ""
    s = s.lower().split(":")[0]
    s = re.sub(r"\b(\d+e|\d+(st|nd|rd|th)\s*edition|second|third|fourth|fifth|"
               r"sixth|seventh|eighth|ninth|tenth|revised|updated|expanded|new|"
               r"completely|anniversary)\s*edition\b", " ", s)
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"^(the|a|an)\s+", "", s)

def last_name(a: str) -> str:
    if not a: return ""
    first = a.split(",")[0].strip().split(";")[0]
    parts = first.split()
    return parts[-1].lower() if parts else ""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--csv", type=Path, default=DEFAULT_GR)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = p.parse_args()

    # Load Goodreads
    by_pair, by_author = {}, defaultdict(list)
    if args.csv.exists():
        with open(args.csv, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                g = {
                    "title": r.get("Title", ""), "author": r.get("Author", ""),
                    "rating": int(r.get("My Rating") or 0),
                    "shelf":  r.get("Exclusive Shelf", ""),
                    "_nt": norm_title(r.get("Title", "")),
                    "_na": last_name(r.get("Author", "")),
                }
                by_pair.setdefault((g["_nt"], g["_na"]), []).append(g)
                if g["_na"]:
                    by_author[g["_na"]].append(g)

    def match(title, author):
        nt, na = norm_title(title), last_name(author or "")
        if (nt, na) in by_pair: return by_pair[(nt, na)][0]
        if na in by_author:
            best, score = None, 0.0
            for g in by_author[na]:
                r = difflib.SequenceMatcher(None, nt, g["_nt"]).ratio()
                if nt and (g["_nt"].startswith(nt) or nt.startswith(g["_nt"])):
                    r = max(r, 0.92)
                if r > score:
                    score, best = r, g
            if score >= 0.78:
                return best
        return None

    # --- flag map from cull_candidates.py rules (lightweight re-implementation) --
    BAD_DATA_PAT = re.compile(r"\?|\(cropped|\.\.\.\)|graph paper notebook|^modeling$", re.I)
    TEST_PREP_PAT = re.compile(r"cfa\b|caia\b|schweser|kaplan", re.I)
    YEAR_IN_TITLE = re.compile(r"\b(19|20)\d{2}\b")
    THIS_YEAR = date.today().year

    def flags_for(w_id, title, author, n_copies, m):
        flags = []
        priority = 0
        if BAD_DATA_PAT.search(title or ""):
            flags.append("bad/uncertain identification"); priority = max(priority, 5)
        if TEST_PREP_PAT.search(title or ""):
            ym = YEAR_IN_TITLE.search(title or "")
            if ym and (THIS_YEAR - int(ym.group(0))) >= 3:
                flags.append(f"dated test-prep ({ym.group(0)})"); priority = max(priority, 5)
            if re.search(r"schweser|kaplan|secret sauce", title or "", re.I):
                flags.append("exam crammer"); priority = max(priority, 4)
        if n_copies >= 2:
            flags.append(f"{n_copies} physical copies"); priority = max(priority, 3)
        if m and m["rating"] and 1 <= m["rating"] <= 2:
            flags.append(f"you rated {m['rating']}★"); priority = max(priority, 4)
        if m and m["rating"] == 3:
            flags.append("you rated 3★"); priority = max(priority, 2)
        if m and m["shelf"] == "read" and m["rating"] == 0:
            if re.search(r"habit|productivity|seven habits|getting things done|atomic|tiny",
                         title or "", re.I):
                flags.append("read but unrated (popular self-help)"); priority = max(priority, 2)
        return ("P" + str(priority) + " " + "; ".join(flags)) if flags else ""

    # Load works + copies
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    works = conn.execute(
        """SELECT w.id, w.title, w.author_display, w.subjects,
                  COUNT(c.id) AS n_copies
             FROM works w
             JOIN editions e ON e.work_id = w.id
             JOIN copies   c ON c.edition_id = e.id
            WHERE c.status = 'owned'
         GROUP BY w.id"""
    ).fetchall()

    # Build rows
    rows = []
    for w in works:
        m = match(w["title"], w["author_display"])
        rows.append({
            "decision": "",
            "notes":    "",
            "category": w["subjects"] or "(uncategorized)",
            "title":    w["title"],
            "author":   w["author_display"] or "",
            "n_copies": w["n_copies"],
            "my_rating": m["rating"] if (m and m["rating"]) else "",
            "goodreads_shelf": (m["shelf"] if m else "") or "",
            "goodreads_title": (m["title"] if m else ""),
            "flag":     flags_for(w["id"], w["title"], w["author_display"], w["n_copies"], m),
            "work_id":  w["id"],
        })

    # Sort: category, then title
    rows.sort(key=lambda r: (r["category"], r["title"].lower()))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)

    flagged = sum(1 for r in rows if r["flag"])
    rated = sum(1 for r in rows if r["my_rating"])
    print(f"Wrote {args.out}")
    print(f"  works: {len(rows)}")
    print(f"  pre-flagged for cull review: {flagged}")
    print(f"  with Goodreads rating: {rated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
