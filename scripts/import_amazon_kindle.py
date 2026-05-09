"""Import an Amazon Kindle library export into the catalog.

How to get the file:
  1. Go to https://www.amazon.com/gp/privacycentral/dsar/preview.html
     (Account → Data and Privacy → Request Your Information)
  2. Request "Kindle" data. You'll receive a ZIP by email after a few hours/days.
  3. Inside, find a CSV named like Kindle.KindleAcquisitions.csv or
     Kindle.Devices.PaymentSettings.PaymentInstrument.1.csv. The first one is
     the library. Drop it in data/raw/ as `amazon_kindle.csv` (or pass --csv).

What this importer does:
  - One row in the export = one digital copy you own (or a sample/loan).
  - Match or create a `work` from title + author. ASIN identifies the edition.
  - Create one `edition` per ASIN (format='ebook'), one `copy` per row with
    vendor='Amazon Kindle' and status='owned'.
  - Skips samples and free promotional items based on Origin column where present.

Column mapping is flexible: it tries common header variations. If your CSV uses
different headers, the script prints what it found and exits — share that
output and the importer can be tweaked.

Usage:
    python3 scripts/import_amazon_kindle.py data/raw/amazon_kindle.csv
    python3 scripts/import_amazon_kindle.py --dry-run data/raw/amazon_kindle.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    DEFAULT_DB, open_db, upsert_author, link_work_author,
    upsert_work, upsert_edition, vendor_id,
)

VENDOR_NAME = "Amazon Kindle"

# Try these header names in order. Lowercase comparison.
COLUMN_ALIASES = {
    "asin":         ["asin", "amazon standard identification number", "product code"],
    "title":        ["title", "product title", "name"],
    "authors":      ["authorlist", "authors", "author", "creator"],
    "origin":       ["origin", "acquisitiontype", "purchase type"],
    "acquired":     ["orderdate", "acquired date", "acquisition date", "purchase date", "date"],
    "category":     ["productgroup", "category", "content type"],
}


def find_column(fieldnames: list[str], aliases: list[str]) -> str | None:
    lower = {fn.lower().strip(): fn for fn in fieldnames}
    for alias in aliases:
        if alias in lower:
            return lower[alias]
    return None


def parse_authors(raw: str | None) -> list[str]:
    if not raw:
        return []
    s = raw.strip()
    # Common formats: "Author1, Author2", "Author1; Author2", "Author1|Author2"
    for sep in (";", "|", ","):
        if sep in s:
            return [a.strip() for a in s.split(sep) if a.strip()]
    return [s] if s else []


def import_csv(path: Path, db_path: Path, dry_run: bool, include_samples: bool) -> None:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        cols = {key: find_column(fieldnames, aliases)
                for key, aliases in COLUMN_ALIASES.items()}

        if not cols["asin"] or not cols["title"]:
            print("ERROR: could not locate required columns (need ASIN and Title).", file=sys.stderr)
            print(f"Headers found in {path.name}:", file=sys.stderr)
            for fn in fieldnames:
                print(f"  - {fn}", file=sys.stderr)
            sys.exit(2)

        conn = open_db(db_path)
        kindle_vendor = vendor_id(conn, VENDOR_NAME)
        if kindle_vendor is None:
            raise SystemExit(f"vendor '{VENDOR_NAME}' missing — run init_db.py")

        stats = {
            "rows": 0, "skipped": 0,
            "works_new": 0, "works_existing": 0,
            "editions_new": 0, "editions_existing": 0,
            "copies_created": 0, "copies_existing": 0,
        }

        for row in reader:
            stats["rows"] += 1
            asin = (row.get(cols["asin"]) or "").strip()
            title = (row.get(cols["title"]) or "").strip()
            if not asin or not title:
                stats["skipped"] += 1
                continue

            origin = (row.get(cols["origin"]) or "").strip().lower() if cols["origin"] else ""
            if not include_samples and origin in {"sample", "kindle unlimited", "free trial"}:
                stats["skipped"] += 1
                continue

            authors = parse_authors(row.get(cols["authors"])) if cols["authors"] else []
            author_display = ", ".join(authors) if authors else None
            acquired = (row.get(cols["acquired"]) or "").strip() or None if cols["acquired"] else None

            work_id, created = upsert_work(
                conn,
                title=title,
                subtitle=None,
                author_display=author_display,
                original_year=None,
                asin=asin,
            )
            stats["works_new" if created else "works_existing"] += 1

            if created:
                for i, name in enumerate(authors):
                    aid = upsert_author(conn, name)
                    link_work_author(conn, work_id, aid, role="author", sort_order=i)

            edition_id, ed_created = upsert_edition(
                conn,
                work_id=work_id,
                format="ebook",
                asin=asin,
            )
            stats["editions_new" if ed_created else "editions_existing"] += 1

            existing_copy = conn.execute(
                "SELECT id FROM copies WHERE edition_id = ? AND vendor_id = ?",
                (edition_id, kindle_vendor),
            ).fetchone()
            if existing_copy:
                stats["copies_existing"] += 1
            else:
                conn.execute(
                    """INSERT INTO copies(edition_id, medium, vendor_id, vendor_book_id,
                                          acquired_date, status, acquired_source)
                       VALUES (?, 'digital', ?, ?, ?, 'owned', 'Amazon Kindle export')""",
                    (edition_id, kindle_vendor, asin, acquired),
                )
                stats["copies_created"] += 1

    if dry_run:
        conn.rollback()
        print("DRY RUN — no changes committed")
    else:
        conn.commit()
    conn.close()

    print(f"Amazon Kindle import from {path}")
    for k, v in stats.items():
        print(f"  {k:20s} {v}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("csv_path", type=Path, help="Path to Amazon Kindle CSV")
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--include-samples", action="store_true",
                   help="Include rows tagged Sample / Kindle Unlimited / Free Trial")
    args = p.parse_args()
    if not args.csv_path.exists():
        raise SystemExit(f"File not found: {args.csv_path}")
    import_csv(args.csv_path, args.db, args.dry_run, args.include_samples)


if __name__ == "__main__":
    main()
