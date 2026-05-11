"""Backfill missing metadata (year, publisher, pages, ISBN, cover) on works
and editions by searching Open Library + Google Books with title + author.

Safe to run repeatedly: only fills NULL/empty fields, never overwrites
existing values. Works already enriched (have original_year AND any edition
has isbn_13) are skipped.

Usage:
    python3 scripts/enrich_metadata.py
    python3 scripts/enrich_metadata.py --limit 5 --dry-run
    python3 scripts/enrich_metadata.py --work-id 17
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DEFAULT_DB, open_db, normalise_title

OPENLIB_SEARCH = "https://openlibrary.org/search.json"
GOOGLE_VOLUMES = "https://www.googleapis.com/books/v1/volumes"
USER_AGENT = "Book_Catalogue/0.1 (+personal use)"


def _http_json(url: str, retries: int = 2) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                wait = 8 * (attempt + 1)
                sys.stderr.write(f"  (429 — sleeping {wait}s) ")
                sys.stderr.flush()
                time.sleep(wait)
                attempt += 1
                continue
            sys.stderr.write(f"  (HTTP error: {e})\n")
            return None
        except Exception as e:
            sys.stderr.write(f"  (HTTP error: {e})\n")
            return None


def first_or_none(seq):
    if not seq:
        return None
    for v in seq:
        if v:
            return v
    return None


def _author_surname(name: str) -> str:
    name = re.sub(r"[^\w\s]", " ", name or "").strip()
    if not name:
        return ""
    parts = name.split()
    return parts[-1].lower() if parts else ""


def search_openlibrary(title: str, author: str | None) -> dict | None:
    params = {"title": title, "limit": 5}
    if author:
        params["author"] = author
    url = f"{OPENLIB_SEARCH}?{urllib.parse.urlencode(params)}"
    data = _http_json(url)
    if not data or not data.get("docs"):
        return None
    target = normalise_title(title)
    author_surname = _author_surname(author or "")
    for doc in data["docs"]:
        doc_title = doc.get("title") or ""
        if normalise_title(doc_title) != target:
            continue
        if author_surname:
            doc_authors = " ".join(doc.get("author_name") or []).lower()
            if author_surname not in doc_authors:
                continue
        isbns = doc.get("isbn") or []
        isbn_13 = first_or_none([i for i in isbns if len(i) == 13])
        isbn_10 = first_or_none([i for i in isbns if len(i) == 10])
        cover_i = doc.get("cover_i")
        cover_url = f"https://covers.openlibrary.org/b/id/{cover_i}-M.jpg" if cover_i else None
        return {
            "source": "Open Library",
            "title": doc_title,
            "authors": doc.get("author_name") or [],
            "first_publish_year": doc.get("first_publish_year"),
            "publisher": first_or_none(doc.get("publisher") or []),
            "isbn_13": isbn_13,
            "isbn_10": isbn_10,
            "pages": doc.get("number_of_pages_median"),
            "subjects": (doc.get("subject") or [])[:8],
            "cover_url": cover_url,
            "language": first_or_none(doc.get("language") or []),
        }
    return None


def search_googlebooks(title: str, author: str | None) -> dict | None:
    q = f'intitle:"{title}"'
    if author:
        q += f' inauthor:"{author}"'
    url = f"{GOOGLE_VOLUMES}?{urllib.parse.urlencode({'q': q, 'maxResults': 5})}"
    data = _http_json(url)
    if not data or not data.get("items"):
        return None
    target = normalise_title(title)
    author_surname = _author_surname(author or "")
    for item in data["items"]:
        info = item.get("volumeInfo") or {}
        if normalise_title(info.get("title") or "") != target:
            continue
        if author_surname:
            doc_authors = " ".join(info.get("authors") or []).lower()
            if author_surname not in doc_authors:
                continue
        ids = info.get("industryIdentifiers") or []
        isbn_13 = next((i["identifier"] for i in ids if i.get("type") == "ISBN_13"), None)
        isbn_10 = next((i["identifier"] for i in ids if i.get("type") == "ISBN_10"), None)
        return {
            "source": "Google Books",
            "title": info.get("title"),
            "authors": info.get("authors") or [],
            "first_publish_year": _parse_year(info.get("publishedDate")),
            "publisher": info.get("publisher"),
            "isbn_13": isbn_13,
            "isbn_10": isbn_10,
            "pages": info.get("pageCount"),
            "subjects": info.get("categories") or [],
            "cover_url": (info.get("imageLinks") or {}).get("thumbnail"),
            "language": info.get("language"),
        }
    return None


def _parse_year(s: str | None) -> int | None:
    if not s:
        return None
    m = re.search(r"(\d{4})", str(s))
    return int(m.group(1)) if m else None


def lookup(title: str, author: str | None) -> dict | None:
    return search_openlibrary(title, author) or search_googlebooks(title, author)


def enrich_work(conn, work_row: dict, dry_run: bool) -> dict:
    title = work_row["title"]
    author = (work_row["author_display"] or "").split(",")[0].strip() or None
    sys.stderr.write(f"  Looking up: {title} — {author or '?'} ... ")
    sys.stderr.flush()
    meta = lookup(title, author)
    if not meta:
        sys.stderr.write("no match\n")
        return {"work_id": work_row["id"], "title": title, "status": "no_match"}
    sys.stderr.write(f"matched via {meta['source']}\n")

    changes = {"work": {}, "edition": {}, "source": meta["source"]}

    # Update work fields (only fill NULLs)
    if work_row["original_year"] is None and meta["first_publish_year"]:
        changes["work"]["original_year"] = meta["first_publish_year"]
    if not work_row.get("subjects") and meta.get("subjects"):
        changes["work"]["subjects"] = ", ".join(meta["subjects"])
    if not work_row.get("original_language") and meta.get("language"):
        changes["work"]["original_language"] = meta["language"]

    if changes["work"] and not dry_run:
        cols = ", ".join(f"{k} = ?" for k in changes["work"])
        conn.execute(
            f"UPDATE works SET {cols}, updated_at = datetime('now') WHERE id = ?",
            (*changes["work"].values(), work_row["id"]),
        )

    # Update the first edition that's missing data
    ed_row = conn.execute(
        "SELECT id, isbn_13, isbn_10, publisher, published_year, pages, cover_url "
        "FROM editions WHERE work_id = ? ORDER BY id LIMIT 1",
        (work_row["id"],),
    ).fetchone()
    if ed_row:
        ed_changes = {}
        if ed_row["isbn_13"] is None and meta["isbn_13"]:
            ed_changes["isbn_13"] = meta["isbn_13"]
        if ed_row["isbn_10"] is None and meta["isbn_10"]:
            ed_changes["isbn_10"] = meta["isbn_10"]
        if ed_row["publisher"] is None and meta["publisher"]:
            ed_changes["publisher"] = meta["publisher"]
        if ed_row["published_year"] is None and meta["first_publish_year"]:
            ed_changes["published_year"] = meta["first_publish_year"]
        if ed_row["pages"] is None and meta["pages"]:
            ed_changes["pages"] = meta["pages"]
        if ed_row["cover_url"] is None and meta["cover_url"]:
            ed_changes["cover_url"] = meta["cover_url"]
        changes["edition"] = ed_changes
        if ed_changes and not dry_run:
            cols = ", ".join(f"{k} = ?" for k in ed_changes)
            try:
                conn.execute(
                    f"UPDATE editions SET {cols} WHERE id = ?",
                    (*ed_changes.values(), ed_row["id"]),
                )
            except Exception as e:
                # ISBN uniqueness collisions etc.
                return {"work_id": work_row["id"], "title": title,
                        "status": "edition_update_failed", "reason": str(e),
                        "changes": changes}

    status = "updated" if (changes["work"] or changes["edition"]) else "nothing_to_change"
    return {"work_id": work_row["id"], "title": title, "status": status,
            "changes": changes}


def needs_enrichment(conn, work_id: int) -> bool:
    """A work needs enrichment if year is NULL OR no edition has isbn_13."""
    row = conn.execute(
        """SELECT w.original_year,
                  (SELECT COUNT(*) FROM editions e
                   WHERE e.work_id = w.id AND e.isbn_13 IS NOT NULL) AS has_isbn
           FROM works w WHERE w.id = ?""",
        (work_id,),
    ).fetchone()
    if not row:
        return False
    return row["original_year"] is None or row["has_isbn"] == 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--limit", type=int, default=0, help="Process at most N works")
    p.add_argument("--work-id", type=int, help="Enrich a single work only")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--sleep", type=float, default=1.2,
                   help="Seconds between API calls (default: 1.2)")
    args = p.parse_args()

    conn = open_db(args.db)

    if args.work_id:
        rows = conn.execute("SELECT * FROM works WHERE id = ?", (args.work_id,)).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM works
               WHERE original_year IS NULL
                  OR id NOT IN (SELECT work_id FROM editions WHERE isbn_13 IS NOT NULL)
               ORDER BY id"""
        ).fetchall()

    if args.limit:
        rows = rows[: args.limit]

    sys.stderr.write(f"Enrichment candidates: {len(rows)}\n")

    results = []
    stats = {"total": len(rows), "updated": 0, "no_match": 0,
             "nothing_to_change": 0, "edition_update_failed": 0}

    for i, row in enumerate(rows):
        if i > 0:
            time.sleep(args.sleep)
        r = enrich_work(conn, dict(row), args.dry_run)
        results.append(r)
        s = r.get("status", "unknown")
        if s in stats:
            stats[s] += 1

    if args.dry_run:
        conn.rollback()
    else:
        conn.commit()
    conn.close()

    json.dump({"stats": stats, "results": results, "dry_run": args.dry_run},
              sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
