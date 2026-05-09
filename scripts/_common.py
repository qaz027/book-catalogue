"""Shared helpers for importer scripts."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "library.db"


def open_db(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(
            f"library.db not found at {path}. Run: python3 scripts/init_db.py"
        )
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_GOODREADS_ISBN_RE = re.compile(r'^="?(\d{0,13})"?$')


def clean_isbn(value: str | None) -> str | None:
    """Goodreads wraps ISBNs as `="9781234567890"` to stop Excel coercion. Strip it."""
    if not value:
        return None
    s = value.strip()
    if not s:
        return None
    m = _GOODREADS_ISBN_RE.match(s)
    if m:
        s = m.group(1)
    s = re.sub(r"[^0-9Xx]", "", s)
    return s or None


def normalise_title(title: str) -> str:
    """For fuzzy matching: lowercase, strip parentheticals, collapse whitespace."""
    t = title.lower()
    t = re.sub(r"\([^)]*\)", " ", t)        # drop "(Dune Chronicles, #1)"
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def upsert_author(conn: sqlite3.Connection, name: str, sort_name: str | None = None) -> int:
    name = name.strip()
    if not name:
        raise ValueError("author name is empty")
    row = conn.execute("SELECT id FROM authors WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO authors(name, sort_name) VALUES (?, ?)",
        (name, sort_name),
    )
    return cur.lastrowid


def link_work_author(conn: sqlite3.Connection, work_id: int, author_id: int,
                     role: str = "author", sort_order: int = 0) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO work_authors(work_id, author_id, role, sort_order) "
        "VALUES (?, ?, ?, ?)",
        (work_id, author_id, role, sort_order),
    )


def find_work_by_isbn(conn: sqlite3.Connection, isbn_13: str | None,
                      isbn_10: str | None, asin: str | None) -> int | None:
    for col, val in (("isbn_13", isbn_13), ("isbn_10", isbn_10), ("asin", asin)):
        if not val:
            continue
        row = conn.execute(
            f"SELECT work_id FROM editions WHERE {col} = ?", (val,)
        ).fetchone()
        if row:
            return row["work_id"]
    return None


def find_work_by_title_author(conn: sqlite3.Connection, title: str,
                              author_display: str | None) -> int | None:
    """Fuzzy match on normalised title + first author."""
    if not title:
        return None
    norm = normalise_title(title)
    candidates = conn.execute(
        "SELECT id, title, author_display FROM works"
    ).fetchall()
    for c in candidates:
        if normalise_title(c["title"]) != norm:
            continue
        if not author_display or not c["author_display"]:
            return c["id"]
        if author_display.split(",")[0].strip().lower() in c["author_display"].lower():
            return c["id"]
    return None


def upsert_work(conn: sqlite3.Connection, *, title: str, subtitle: str | None,
                author_display: str | None, original_year: int | None,
                description: str | None = None, subjects: str | None = None,
                series_name: str | None = None, series_position: float | None = None,
                isbn_13: str | None = None, isbn_10: str | None = None,
                asin: str | None = None) -> tuple[int, bool]:
    """Find or create a work. Returns (work_id, created)."""
    work_id = find_work_by_isbn(conn, isbn_13, isbn_10, asin)
    if work_id is None:
        work_id = find_work_by_title_author(conn, title, author_display)
    if work_id is not None:
        return work_id, False
    cur = conn.execute(
        """INSERT INTO works(title, subtitle, author_display, original_year,
                             description, subjects, series_name, series_position)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (title, subtitle, author_display, original_year,
         description, subjects, series_name, series_position),
    )
    return cur.lastrowid, True


def upsert_edition(conn: sqlite3.Connection, *, work_id: int, format: str,
                   isbn_13: str | None = None, isbn_10: str | None = None,
                   asin: str | None = None, publisher: str | None = None,
                   published_year: int | None = None, pages: int | None = None,
                   duration_minutes: int | None = None, language: str | None = None,
                   cover_url: str | None = None) -> tuple[int, bool]:
    """Find or create an edition. Match on isbn_13 / isbn_10 / asin if provided,
    otherwise on (work_id, format, publisher, published_year)."""
    for col, val in (("isbn_13", isbn_13), ("isbn_10", isbn_10), ("asin", asin)):
        if not val:
            continue
        row = conn.execute(f"SELECT id FROM editions WHERE {col} = ?", (val,)).fetchone()
        if row:
            return row["id"], False
    if not (isbn_13 or isbn_10 or asin):
        row = conn.execute(
            """SELECT id FROM editions
               WHERE work_id = ? AND format = ?
                 AND COALESCE(publisher, '') = COALESCE(?, '')
                 AND COALESCE(published_year, 0) = COALESCE(?, 0)""",
            (work_id, format, publisher, published_year),
        ).fetchone()
        if row:
            return row["id"], False
    cur = conn.execute(
        """INSERT INTO editions(work_id, isbn_13, isbn_10, asin, format, publisher,
                                published_year, pages, duration_minutes, language, cover_url)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (work_id, isbn_13, isbn_10, asin, format, publisher,
         published_year, pages, duration_minutes, language, cover_url),
    )
    return cur.lastrowid, True


def vendor_id(conn: sqlite3.Connection, name: str) -> int | None:
    row = conn.execute("SELECT id FROM vendors WHERE name = ?", (name,)).fetchone()
    return row["id"] if row else None


def location_id(conn: sqlite3.Connection, name: str, description: str | None = None) -> int:
    row = conn.execute("SELECT id FROM locations WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO locations(name, description) VALUES (?, ?)",
        (name, description),
    )
    return cur.lastrowid


def parse_year(value: str | None) -> int | None:
    if not value:
        return None
    s = str(value).strip()
    m = re.search(r"-?\d{1,4}", s)
    if not m:
        return None
    try:
        y = int(m.group(0))
        if -3000 <= y <= 9999:
            return y
    except ValueError:
        pass
    return None


def parse_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None
