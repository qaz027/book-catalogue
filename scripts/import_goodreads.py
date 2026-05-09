"""Import a Goodreads export CSV into the catalog.

Goodreads exports are available at: https://www.goodreads.com/review/import

What this importer does:
  - Creates a `work` for every row (deduped by ISBN13/ISBN10/title+author).
  - Creates an `edition` per row with the binding/format Goodreads recorded.
  - If `Owned Copies >= 1`, creates that many physical `copies` with location
    'Goodreads import (review)' so you can re-shelve them properly later.
  - Does NOT auto-create copies for read-but-not-owned rows. Goodreads doesn't
    reliably know what you own; we only act on the explicit Owned Copies column.

Usage:
    python3 scripts/import_goodreads.py path/to/goodreads_library_export.csv
    python3 scripts/import_goodreads.py --dry-run path/to/goodreads_library_export.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    DEFAULT_DB, open_db, clean_isbn, upsert_author, link_work_author,
    upsert_work, upsert_edition, location_id, parse_year, parse_int,
)

REVIEW_LOCATION = "Goodreads import (review)"

BINDING_TO_FORMAT = {
    "hardcover": "hardcover",
    "paperback": "paperback",
    "mass market paperback": "mass-market",
    "kindle edition": "ebook",
    "ebook": "ebook",
    "audio cd": "audiobook",
    "audible audio": "audiobook",
    "audiobook": "audiobook",
    "unknown binding": "other",
}


def normalise_format(binding: str | None) -> str:
    if not binding:
        return "other"
    return BINDING_TO_FORMAT.get(binding.strip().lower(), "other")


def import_csv(path: Path, db_path: Path, dry_run: bool) -> None:
    conn = open_db(db_path)
    stats = {
        "rows": 0, "skipped": 0,
        "works_new": 0, "works_existing": 0,
        "editions_new": 0, "editions_existing": 0,
        "copies_created": 0,
    }

    review_loc_id = None if dry_run else location_id(
        conn, REVIEW_LOCATION,
        "Imported from Goodreads — assign a real shelf location"
    )

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stats["rows"] += 1
            title = (row.get("Title") or "").strip()
            if not title:
                stats["skipped"] += 1
                continue

            author = (row.get("Author") or "").strip()
            author_lf = (row.get("Author l-f") or "").strip() or None
            additional = (row.get("Additional Authors") or "").strip()
            isbn_13 = clean_isbn(row.get("ISBN13"))
            isbn_10 = clean_isbn(row.get("ISBN"))

            work_id, created = upsert_work(
                conn,
                title=title,
                subtitle=None,
                author_display=author or None,
                original_year=parse_year(row.get("Original Publication Year")),
                isbn_13=isbn_13,
                isbn_10=isbn_10,
            )
            stats["works_new" if created else "works_existing"] += 1

            if created and author:
                primary_id = upsert_author(conn, author, sort_name=author_lf)
                link_work_author(conn, work_id, primary_id, role="author", sort_order=0)
                if additional:
                    for i, name in enumerate(
                        [n.strip() for n in additional.split(",") if n.strip()], start=1
                    ):
                        aid = upsert_author(conn, name)
                        link_work_author(conn, work_id, aid, role="author", sort_order=i)

            edition_id, ed_created = upsert_edition(
                conn,
                work_id=work_id,
                format=normalise_format(row.get("Binding")),
                isbn_13=isbn_13,
                isbn_10=isbn_10,
                publisher=(row.get("Publisher") or "").strip() or None,
                published_year=parse_year(row.get("Year Published")),
                pages=parse_int(row.get("Number of Pages")),
            )
            stats["editions_new" if ed_created else "editions_existing"] += 1

            owned_n = parse_int(row.get("Owned Copies")) or 0
            if owned_n > 0 and not dry_run:
                already = conn.execute(
                    """SELECT COUNT(*) FROM copies
                       WHERE edition_id = ? AND acquired_source = 'Goodreads import'""",
                    (edition_id,),
                ).fetchone()[0]
                medium = "audio" if normalise_format(row.get("Binding")) == "audiobook" else "physical"
                for _ in range(max(0, owned_n - already)):
                    conn.execute(
                        """INSERT INTO copies(edition_id, medium, location_id, status,
                                              acquired_source, notes)
                           VALUES (?, ?, ?, 'owned', 'Goodreads import',
                                   'Imported from Goodreads; review location')""",
                        (edition_id, medium, review_loc_id),
                    )
                    stats["copies_created"] += 1

    if dry_run:
        conn.rollback()
        print("DRY RUN — no changes committed")
    else:
        conn.commit()
    conn.close()

    print(f"Goodreads import from {path}")
    for k, v in stats.items():
        print(f"  {k:20s} {v}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("csv_path", type=Path, help="Path to Goodreads export CSV")
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--dry-run", action="store_true",
                   help="Report what would happen without writing")
    args = p.parse_args()
    if not args.csv_path.exists():
        raise SystemExit(f"File not found: {args.csv_path}")
    import_csv(args.csv_path, args.db, args.dry_run)


if __name__ == "__main__":
    main()
