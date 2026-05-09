"""Interactively add a book to the catalog.

Usage:
    python3 scripts/add_book.py
    python3 scripts/add_book.py --isbn 9780441013593
    python3 scripts/add_book.py --asin B000FBJCJE

Flow:
  1. Ask for ISBN (or ASIN, or 'manual' for hand entry).
  2. Look up metadata via Open Library, then Google Books as fallback.
  3. Show what was found; ask you to confirm or correct.
  4. Ask for medium (physical/digital/audio), location or vendor, status, notes.
  5. Insert work + edition + copy. Skip work/edition creation if already present.
"""
from __future__ import annotations

import argparse
import json
import sys
import textwrap
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    DEFAULT_DB, open_db, upsert_author, link_work_author,
    upsert_work, upsert_edition, vendor_id, location_id, parse_year, parse_int,
)

OPENLIB_URL = "https://openlibrary.org/api/books"
GOOGLE_URL = "https://www.googleapis.com/books/v1/volumes"
USER_AGENT = "Book_Catalogue/0.1 (+personal use)"


def _http_json(url: str) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except Exception as e:
        print(f"  (lookup failed: {e})")
        return None


def lookup_openlibrary(isbn: str) -> dict | None:
    q = urllib.parse.urlencode({
        "bibkeys": f"ISBN:{isbn}",
        "format": "json",
        "jscmd": "data",
    })
    data = _http_json(f"{OPENLIB_URL}?{q}")
    if not data:
        return None
    rec = data.get(f"ISBN:{isbn}")
    if not rec:
        return None
    return {
        "title": rec.get("title"),
        "subtitle": rec.get("subtitle"),
        "authors": [a.get("name") for a in rec.get("authors", []) if a.get("name")],
        "publishers": [p.get("name") for p in rec.get("publishers", []) if p.get("name")],
        "published_year": parse_year(rec.get("publish_date")),
        "pages": rec.get("number_of_pages"),
        "subjects": [s.get("name") for s in rec.get("subjects", []) if s.get("name")],
        "cover_url": (rec.get("cover") or {}).get("medium"),
        "source": "Open Library",
    }


def lookup_google_books(isbn: str) -> dict | None:
    q = urllib.parse.urlencode({"q": f"isbn:{isbn}"})
    data = _http_json(f"{GOOGLE_URL}?{q}")
    if not data or not data.get("items"):
        return None
    info = data["items"][0].get("volumeInfo", {})
    return {
        "title": info.get("title"),
        "subtitle": info.get("subtitle"),
        "authors": info.get("authors") or [],
        "publishers": [info["publisher"]] if info.get("publisher") else [],
        "published_year": parse_year(info.get("publishedDate")),
        "pages": info.get("pageCount"),
        "subjects": info.get("categories") or [],
        "cover_url": (info.get("imageLinks") or {}).get("thumbnail"),
        "source": "Google Books",
    }


def lookup_isbn(isbn: str) -> dict | None:
    print(f"Looking up ISBN {isbn}...")
    rec = lookup_openlibrary(isbn)
    if rec and rec.get("title"):
        print(f"  found via {rec['source']}")
        return rec
    rec = lookup_google_books(isbn)
    if rec and rec.get("title"):
        print(f"  found via {rec['source']}")
        return rec
    print("  no match found in Open Library or Google Books")
    return None


def normalise_isbn(s: str) -> tuple[str | None, str | None]:
    """Return (isbn_13, isbn_10). Computes the other if only one is given."""
    digits = "".join(c for c in s if c.isalnum())
    if len(digits) == 13 and digits.isdigit():
        return digits, _isbn13_to_10(digits)
    if len(digits) == 10:
        return _isbn10_to_13(digits), digits
    return None, None


def _isbn10_to_13(isbn10: str) -> str:
    core = "978" + isbn10[:9]
    total = sum((1 if i % 2 == 0 else 3) * int(d) for i, d in enumerate(core))
    check = (10 - total % 10) % 10
    return core + str(check)


def _isbn13_to_10(isbn13: str) -> str | None:
    if not isbn13.startswith("978"):
        return None
    core = isbn13[3:12]
    total = sum((10 - i) * int(d) for i, d in enumerate(core))
    check = (11 - total % 11) % 11
    return core + ("X" if check == 10 else str(check))


def prompt(question: str, default: str | None = None, choices: list[str] | None = None) -> str:
    suffix = ""
    if choices:
        suffix = f" [{'/'.join(choices)}]"
    if default is not None:
        suffix += f" (default: {default})"
    while True:
        answer = input(f"{question}{suffix}: ").strip()
        if not answer and default is not None:
            return default
        if choices and answer not in choices:
            print(f"  pick one of {choices}")
            continue
        if answer:
            return answer


def prompt_optional(question: str) -> str | None:
    answer = input(f"{question} (blank to skip): ").strip()
    return answer or None


def show_metadata(meta: dict) -> None:
    print()
    print("  Found:")
    print(f"    Title:   {meta.get('title')}")
    if meta.get("subtitle"):
        print(f"    Subtitle: {meta['subtitle']}")
    if meta.get("authors"):
        print(f"    Authors: {', '.join(meta['authors'])}")
    if meta.get("publishers"):
        print(f"    Publisher: {', '.join(meta['publishers'])}")
    if meta.get("published_year"):
        print(f"    Year:    {meta['published_year']}")
    if meta.get("pages"):
        print(f"    Pages:   {meta['pages']}")
    print()


def add_book(args: argparse.Namespace) -> None:
    conn = open_db(args.db)

    print("Add a book to the catalog.")
    print("Enter ISBN (10 or 13 digits), or type 'asin', or 'manual'.\n")

    identifier = args.isbn or args.asin or prompt(
        "Identifier", default=None,
    )

    isbn_13 = isbn_10 = asin = None
    meta: dict = {}

    if identifier.lower() in ("manual", "m"):
        meta = manual_entry()
    elif identifier.lower().startswith("asin") or args.asin:
        asin = args.asin or prompt("ASIN")
        meta = manual_entry(prefill_asin=asin)
    else:
        isbn_13, isbn_10 = normalise_isbn(identifier)
        if not (isbn_13 or isbn_10):
            print("  not a valid ISBN, switching to manual entry")
            meta = manual_entry()
        else:
            looked_up = lookup_isbn(isbn_13 or isbn_10 or "")
            if looked_up:
                show_metadata(looked_up)
                if prompt("Use this metadata?", default="y", choices=["y", "n"]) == "y":
                    meta = looked_up
                else:
                    meta = manual_entry(prefill_isbn=isbn_13 or isbn_10)
            else:
                meta = manual_entry(prefill_isbn=isbn_13 or isbn_10)

    title = meta["title"]
    authors = meta.get("authors") or []
    author_display = ", ".join(authors) if authors else None

    work_id, created = upsert_work(
        conn,
        title=title,
        subtitle=meta.get("subtitle"),
        author_display=author_display,
        original_year=meta.get("published_year"),
        description=meta.get("description"),
        subjects=", ".join(meta.get("subjects") or []) or None,
        isbn_13=isbn_13, isbn_10=isbn_10, asin=asin,
    )
    if created:
        print(f"  + created work #{work_id}")
        for i, name in enumerate(authors):
            aid = upsert_author(conn, name)
            link_work_author(conn, work_id, aid, role="author", sort_order=i)
    else:
        print(f"  = matched existing work #{work_id}: {title}")

    fmt = prompt(
        "Format",
        default=meta.get("format", "paperback"),
        choices=["hardcover", "paperback", "mass-market", "ebook", "audiobook", "pdf", "epub", "other"],
    )

    edition_id, ed_created = upsert_edition(
        conn,
        work_id=work_id,
        format=fmt,
        isbn_13=isbn_13,
        isbn_10=isbn_10,
        asin=asin,
        publisher=(meta.get("publishers") or [None])[0],
        published_year=meta.get("published_year"),
        pages=parse_int(meta.get("pages")),
        cover_url=meta.get("cover_url"),
    )
    print(f"  {'+' if ed_created else '='} edition #{edition_id} ({fmt})")

    medium = prompt("Medium", default="physical", choices=["physical", "digital", "audio"])
    loc_id = vend_id = None
    file_path = None

    if medium == "physical":
        loc = prompt("Location/shelf (e.g. 'SF-3', 'Office overflow box 2')")
        loc_id = location_id(conn, loc)
        condition = prompt("Condition", default="good",
                           choices=["new", "good", "worn", "damaged"])
    else:
        vendors_avail = [r["name"] for r in conn.execute(
            "SELECT name FROM vendors ORDER BY name").fetchall()]
        print(f"  Vendors: {', '.join(vendors_avail)}")
        vname = prompt("Vendor")
        vend_id = vendor_id(conn, vname)
        if vend_id is None:
            kind = "audio" if medium == "audio" else "digital"
            cur = conn.execute("INSERT INTO vendors(name, kind) VALUES (?, ?)",
                               (vname, kind))
            vend_id = cur.lastrowid
            print(f"  + created vendor '{vname}'")
        condition = None
        if medium == "digital" or medium == "audio":
            file_path = prompt_optional("Local file path (for DRM-free files)")

    status = prompt("Status", default="owned",
                    choices=["owned", "borrowed", "loaned-out", "sold", "returned", "lost"])
    borrowed_until = None
    if status == "borrowed":
        borrowed_until = prompt_optional("Borrowed until (YYYY-MM-DD)")

    acquired_date = prompt_optional("Acquired date (YYYY-MM-DD)")
    notes = prompt_optional("Notes")

    conn.execute(
        """INSERT INTO copies(edition_id, medium, location_id, vendor_id, vendor_book_id,
                              file_path, condition, status, borrowed_until,
                              acquired_date, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (edition_id, medium, loc_id, vend_id, asin if asin else None,
         file_path, condition, status, borrowed_until, acquired_date, notes),
    )
    conn.commit()
    print(f"  + copy added\n")

    print("Done. Summary:")
    for r in conn.execute(
        """SELECT format, medium, status, location, vendor
           FROM v_ownership WHERE work_id = ?""",
        (work_id,),
    ):
        loc_or_vendor = r["location"] or r["vendor"] or "-"
        print(f"  - {r['format']} ({r['medium']}, {r['status']}) @ {loc_or_vendor}")
    conn.close()


def manual_entry(prefill_isbn: str | None = None,
                 prefill_asin: str | None = None) -> dict:
    print("Manual entry.")
    title = prompt("Title")
    subtitle = prompt_optional("Subtitle")
    authors_raw = prompt("Author(s) (comma-separated)")
    authors = [a.strip() for a in authors_raw.split(",") if a.strip()]
    year = parse_year(prompt_optional("Published year"))
    publisher = prompt_optional("Publisher")
    pages = parse_int(prompt_optional("Pages") or "")
    return {
        "title": title,
        "subtitle": subtitle,
        "authors": authors,
        "publishers": [publisher] if publisher else [],
        "published_year": year,
        "pages": pages,
        "subjects": [],
        "source": "manual",
    }


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--isbn", help="Skip the prompt and look up this ISBN")
    p.add_argument("--asin", help="Skip the prompt and use this ASIN (manual entry)")
    args = p.parse_args()
    try:
        add_book(args)
    except (KeyboardInterrupt, EOFError):
        print("\naborted")


if __name__ == "__main__":
    main()
