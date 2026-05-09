"""Build (or re-apply schema to) library.db.

Idempotent: safe to run repeatedly. Creates the file if missing, applies schema,
and seeds known vendors so importers can reference them by name.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "library.db"
SCHEMA_PATH = REPO_ROOT / "schema.sql"

SEED_VENDORS = [
    ("Amazon Kindle",       "digital"),
    ("Amazon (physical)",   "physical_seller"),
    ("Apple Books",         "digital"),
    ("Google Play Books",   "digital"),
    ("Kobo",                "digital"),
    ("Audible",             "audio"),
    ("Libby",               "library"),
    ("Hoopla",              "library"),
    ("Local library",       "library"),
    ("Project Gutenberg",   "digital"),
    ("Standard Ebooks",     "digital"),
    ("Unknown / website",   "digital"),
]


def init(db_path: Path) -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(schema_sql)
        conn.executemany(
            "INSERT OR IGNORE INTO vendors(name, kind) VALUES (?, ?)",
            SEED_VENDORS,
        )
        conn.commit()
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'version'"
        ).fetchone()
        vendor_count = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
    print(f"library.db ready at {db_path}")
    print(f"  schema version: {version[0] if version else '?'}")
    print(f"  vendors seeded: {vendor_count}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"Path to DB (default: {DEFAULT_DB})")
    args = p.parse_args()
    init(args.db)


if __name__ == "__main__":
    main()
