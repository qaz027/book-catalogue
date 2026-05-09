# Book Catalogue

A personal database of every book I own — physical and digital, across all vendors and shelves — plus a "want to buy" wishlist. Designed so I can check on my phone before buying whether I already own a book.

## Repo layout

```
library.db              SQLite catalog (committed)
schema.sql              Source of truth for the schema
scripts/                Python ingestion + maintenance scripts
data/raw/               Vendor exports (gitignored — keep private)
web/                    Static search UI (loaded by GitHub Pages)
inbox/                  Phone captures awaiting processing (Phase 2)
```

## Workflow

**On a new computer:**
```bash
git clone <repo-url> ~/Projects/Book_Catalogue   # or anywhere
cd ~/Projects/Book_Catalogue
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**Adding books (desktop):**
- `python3 scripts/add_book.py` — interactive ISBN entry for a single book
- `python3 scripts/import_goodreads.py data/raw/goodreads_export.csv`
- `python3 scripts/import_amazon.py data/raw/amazon_export.csv`

**Looking up books (any device):**
- Open the GitHub Pages URL in any browser. Search by title or author. Results show whether you already own the book and in what format(s).

**Saving changes:**
```bash
git add library.db
git commit -m "Add 12 books from Kindle export"
git push
```

## Build phases

1. **Catalog spine + lookup** ← currently building
2. Capture inbox (phone photos and voice memos → desktop processing)
3. Obsidian linkage for notes
4. Audiobook voice memo → Whisper → Obsidian pipeline
5. Handwritten note OCR → Obsidian
