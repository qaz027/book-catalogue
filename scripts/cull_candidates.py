"""Surface candidate books to donate, given a shelf-capacity constraint.

Inputs:
  - library.db works/editions/copies (single source of truth)
  - data/raw/goodreads_library_export.csv (for personal ratings + shelf membership)

Heuristics (each adds a "reason" tag):
  - Test-prep textbooks dated >=5 years before the current year (CFA / CAIA editions become stale fast)
  - Bad-data rows (title contains "?", "(cropped", "..." or matches obvious garbage)
  - Multiple physical copies of the same work (donate at least one)
  - Books rated 1-3 stars on your Goodreads "read" shelf (you've read it and didn't love it)
  - Books matched in Goodreads as "read" but unrated AND title looks like a popular self-help
    (you read it, didn't bother to rate — often a sign it didn't stick)
  - Books with companion editions in catalog (e.g. textbook + study guide + solutions manual:
    if you have all three of a series, the study guide and solutions manual are often donatable
    once you've used them)

The script does NOT modify the catalog. It writes reports/cull_candidates.md you
can review before deciding what to physically pull off the shelf.

Usage:
    python3 scripts/cull_candidates.py
    python3 scripts/cull_candidates.py --target 150   # show enough to cut to ~150 books
"""
from __future__ import annotations

import argparse
import csv
import difflib
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO / "library.db"
DEFAULT_CSV = REPO / "data/raw/goodreads_library_export.csv"
DEFAULT_MD = REPO / "reports/cull_candidates.md"


# Reuse the matcher logic from book_ratings.py
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


BAD_DATA_PAT = re.compile(r"\?|\(cropped|\.\.\.\)|graph paper notebook|^modeling$", re.I)
TEST_PREP_PAT = re.compile(r"cfa\b|caia\b|schweser|kaplan", re.I)
YEAR_IN_TITLE = re.compile(r"\b(19|20)\d{2}\b")
THIS_YEAR = date.today().year


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    p.add_argument("--md", type=Path, default=DEFAULT_MD)
    p.add_argument("--target", type=int, default=None,
                   help="target physical-copy count after culling (e.g. 150)")
    args = p.parse_args()

    # --- load Goodreads ---
    gr_by_pair: dict = {}
    gr_by_author: dict = defaultdict(list)
    if args.csv.exists():
        with open(args.csv, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                g = {
                    "title": r.get("Title", ""),
                    "author": r.get("Author", ""),
                    "rating": int(r.get("My Rating") or 0),
                    "shelf": r.get("Exclusive Shelf", ""),
                    "_nt": norm_title(r.get("Title", "")),
                    "_na": last_name(r.get("Author", "")),
                }
                gr_by_pair.setdefault((g["_nt"], g["_na"]), []).append(g)
                if g["_na"]:
                    gr_by_author[g["_na"]].append(g)

    def match(title, author):
        nt, na = norm_title(title), last_name(author or "")
        if (nt, na) in gr_by_pair:
            return gr_by_pair[(nt, na)][0]
        if na in gr_by_author:
            best, score = None, 0.0
            for g in gr_by_author[na]:
                r = difflib.SequenceMatcher(None, nt, g["_nt"]).ratio()
                if nt and (g["_nt"].startswith(nt) or nt.startswith(g["_nt"])):
                    r = max(r, 0.92)
                if r > score:
                    score, best = r, g
            if score >= 0.78:
                return best
        return None

    # --- load catalog ---
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    works = conn.execute(
        """SELECT w.id, w.title, w.author_display, w.subjects,
                  COUNT(c.id) AS n_copies
             FROM works w
             JOIN editions e ON e.work_id = w.id
             JOIN copies   c ON c.edition_id = e.id
            WHERE c.status = 'owned'
         GROUP BY w.id
         ORDER BY w.title"""
    ).fetchall()

    # --- rules / flags ---
    candidates: dict[int, dict] = {}    # work_id -> {reasons:[...], priority:int, ...}

    def flag(w, reason: str, priority: int):
        e = candidates.setdefault(w["id"], {
            "id": w["id"], "title": w["title"], "author": w["author_display"] or "",
            "category": w["subjects"] or "(uncategorized)",
            "n_copies": w["n_copies"],
            "reasons": [], "priority": 0,
        })
        e["reasons"].append(reason)
        e["priority"] = max(e["priority"], priority)

    for w in works:
        # P5 highest — strong, near-automatic
        # 1. Bad-data rows (not real books — clean these up regardless)
        if BAD_DATA_PAT.search(w["title"]):
            flag(w, "bad/uncertain identification — clean up DB row", priority=5)

        # 2. Outdated dated test-prep textbooks (CFA / CAIA with year)
        if TEST_PREP_PAT.search(w["title"]):
            ym = YEAR_IN_TITLE.search(w["title"])
            if ym and (THIS_YEAR - int(ym.group(0))) >= 3:
                flag(w, f"test-prep textbook {ym.group(0)} (curriculum is {THIS_YEAR - int(ym.group(0))} years old)",
                     priority=5)
            # Schweser/Kaplan secret sauce specifically — pure exam crammers, low long-term value
            if re.search(r"schweser|kaplan|secret sauce", w["title"], re.I):
                flag(w, "exam crammer (Schweser / Kaplan) — low long-term reference value",
                     priority=4)

        # 3. Multiple physical copies (donate at least one)
        if w["n_copies"] >= 2:
            flag(w, f"{w['n_copies']} physical copies — donate at least 1",
                 priority=3)

        # 4. Goodreads-rated low (you've read it and didn't love it)
        m = match(w["title"], w["author_display"])
        if m and m["rating"] and 1 <= m["rating"] <= 2:
            flag(w, f"you rated {m['rating']}★ on Goodreads", priority=4)
        if m and m["rating"] == 3:
            flag(w, "you rated 3★ on Goodreads (lukewarm)", priority=2)

        # 5. Read but unrated AND title looks popular self-help / habits
        if m and m["shelf"] == "read" and m["rating"] == 0:
            if re.search(r"habit|productivity|7 habits|four agreements|how to|"
                         r"steal like|getting things done|atomic|tiny",
                         w["title"], re.I):
                flag(w, "read on Goodreads but never rated — popular self-help (easy Kindle replacement)",
                     priority=2)

    # 6. Series companions — if you have textbook + study guide + solutions manual,
    # the latter two are often donatable after the course is over.
    title_lc = {w["id"]: (w["title"] or "").lower() for w in works}
    for w in works:
        t = title_lc[w["id"]]
        if "solutions manual" in t or "study guide" in t or "workbook" in t:
            # find a base book by trimming the suffix
            base = re.sub(r"\b(solutions manual|study guide|workbook)\b.*", "", t).strip(" ,:-")
            if base:
                base_norm = norm_title(base)
                for other in works:
                    if other["id"] == w["id"]: continue
                    if norm_title(other["title"]) == base_norm:
                        flag(w, f"companion to existing textbook (\"{other['title']}\") — keep textbook, donate companion if done",
                             priority=3)
                        break

    # --- build markdown report ---
    sorted_cands = sorted(candidates.values(), key=lambda x: (-x["priority"], x["category"], x["title"]))

    by_cat = defaultdict(list)
    for w in works:
        by_cat[w["subjects"] or "(uncategorized)"].append(w)

    total_copies = sum(w["n_copies"] for w in works)
    flagged_copies = sum(c["n_copies"] for c in sorted_cands)

    lines = []
    lines.append("# Cull Candidates — Donation Recommendations\n")
    lines.append(f"**Total physical copies:** {total_copies}  ")
    lines.append(f"**Total flagged for donation review:** {flagged_copies} copies "
                 f"across {len(sorted_cands)} works\n")
    if args.target:
        need_to_cut = max(0, total_copies - args.target)
        lines.append(f"**Target after culling:** {args.target} copies → "
                     f"need to remove **{need_to_cut}** copies\n")
    lines.append("\n## Category sizes (sorted)\n")
    lines.append("| Category | Works | Notes |")
    lines.append("|----------|-------|-------|")
    for cat, ws in sorted(by_cat.items(), key=lambda kv: -len(kv[1])):
        n_flag = sum(1 for c in sorted_cands if c["category"] == cat)
        lines.append(f"| {cat} | {len(ws)} | {n_flag} flagged |")

    # Group flagged by category and priority
    lines.append("\n## Flagged books, grouped by category\n")
    lines.append("Priority key: **P5 = strongest (clean-up / clearly outdated)**, "
                 "P4 = strong (low rating / exam crammer), P3 = moderate "
                 "(duplicate copy / companion you've finished using), "
                 "P2 = consider (lukewarm rating / easy Kindle replacement)\n")
    cands_by_cat = defaultdict(list)
    for c in sorted_cands:
        cands_by_cat[c["category"]].append(c)
    for cat in sorted(cands_by_cat, key=lambda c: -len(cands_by_cat[c])):
        lines.append(f"\n### {cat}\n")
        lines.append("| Priority | Title | Author | Copies | Why |")
        lines.append("|----------|-------|--------|--------|-----|")
        for c in cands_by_cat[cat]:
            why = "; ".join(c["reasons"])
            lines.append(f"| P{c['priority']} | {c['title']} | {c['author']} | "
                         f"{c['n_copies']} | {why} |")

    lines.append("\n---\n")
    lines.append("\n## Suggested next actions\n")
    lines.append("1. **Pull P5 items first** — they're either bad data or clearly outdated. "
                 "Bad-data rows should be deleted from library.db via "
                 "`python3 -c \"import sqlite3; c=sqlite3.connect('library.db'); "
                 "c.execute('DELETE FROM copies WHERE edition_id IN (SELECT id FROM editions WHERE work_id=?)', (ID,)); ...\"`.\n")
    lines.append("2. **CFA/CAIA test-prep** is the largest cleanly-donatable cluster. "
                 f"You have {sum(1 for c in sorted_cands if 'test-prep' in ' '.join(c['reasons']))} dated copies. "
                 "The local library will take these or Half Price Books will pay a small amount.\n")
    lines.append("3. **Companion editions (study guides, solutions manuals, workbooks)** — keep the main textbook, "
                 "donate the companions once you've finished the related course.\n")
    lines.append("4. **Donate-don't-sell heuristic:** if it's been on your shelf for >2 years "
                 "and isn't a reference you've cracked open in the last 6 months, donate.\n")
    args.md.parent.mkdir(parents=True, exist_ok=True)
    args.md.write_text("\n".join(lines))

    print(f"Wrote {args.md}")
    print()
    print(f"Total physical copies: {total_copies}")
    print(f"Flagged for donation review: {flagged_copies} ({len(sorted_cands)} works)")
    print()
    print("Priority distribution:")
    pri_counts = Counter(c["priority"] for c in sorted_cands)
    for p in sorted(pri_counts, reverse=True):
        print(f"  P{p}: {pri_counts[p]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
