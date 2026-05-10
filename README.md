# Book Catalogue

A personal database of every book I own — physical and digital, across all vendors and shelves — plus a "want to buy" wishlist. Designed so I can check on my phone before buying whether I already own a book.

**Live lookup:** https://qaz027.github.io/book-catalogue/

## Repo layout

```
library.db              SQLite catalog (committed)
schema.sql              Source of truth for the schema
scripts/                Python ingestion + maintenance scripts
inbox/                  Phone captures (incoming/processed) — gitignored
data/raw/               Vendor exports (gitignored, private)
index.html, manifest.json   PWA search UI (served by GitHub Pages)
CLAUDE.md               Processing playbook for Claude Code
```

## Workflow

### On a new computer

```bash
git clone https://github.com/qaz027/book-catalogue.git ~/Projects/Book_Catalogue
cd ~/Projects/Book_Catalogue
python3 scripts/init_db.py    # only needed if library.db is missing
```

No external Python deps — stdlib only.

### Looking up books (any device)

Open https://qaz027.github.io/book-catalogue/. Search by title or author. Results show whether you already own the book and in what format(s).

### Adding books from the desktop (one at a time)

```bash
python3 scripts/add_book.py                  # interactive prompt
python3 scripts/add_book.py --isbn 9780441013593
```

### Adding books from the desktop (bulk imports)

```bash
python3 scripts/import_goodreads.py     data/raw/goodreads_library_export.csv
python3 scripts/import_amazon_kindle.py data/raw/amazon_kindle.csv
```

Drop your raw exports into `data/raw/` (gitignored). The Goodreads importer creates physical copies for any row with `Owned Copies >= 1` at a placeholder location `Goodreads import (review)` for you to reshelve later. The Amazon importer creates digital copies linked to the `Amazon Kindle` vendor.

### Adding books from the phone (Phase 2 capture inbox)

Take a photo on your phone, share it to **Google Drive**, save it into the appropriate subfolder of a top-level folder called `Book Catalogue`:

| Drive subfolder            | What goes there                                            |
|----------------------------|------------------------------------------------------------|
| `Book Catalogue/shelf/`    | Photos of one or more book spines on a shelf               |
| `Book Catalogue/wishlist/` | Books you want — X screenshots, photos of covers, notes    |
| `Book Catalogue/add/`      | One specific newly-acquired book in clear focus            |
| `Book Catalogue/memo/`     | Voice memos from audiobook drives (Phase 4 — deferred)     |

If the folders don't exist yet, Claude will create them on first run.

Then, on the desktop, open Claude Code in this repo and tell it:

> Process the inbox.

Claude will list new files in those Drive folders, download them via the Drive MCP, view each image, ask you to confirm the extracted books, and route the captures into the catalog or wishlist. Drive file IDs already processed are tracked in `inbox/.processed_drive_ids.txt` (committed) so reruns are idempotent. See `CLAUDE.md` for the exact playbook.

### Saving changes

```bash
git add library.db
git commit -m "Add 12 books from living room shelf 3"
git push
```

A push triggers a GitHub Pages rebuild (~30 seconds). Reload the PWA on your phone to see the new books.

## Build phases

| Phase | What | Status |
|-------|------|--------|
| 1 | Catalog spine + lookup PWA | Done |
| 2 | Capture inbox (phone email → desktop processing) | Done |
| 3 | Obsidian linkage for notes | Not started |
| 4 | Audiobook voice memo → Whisper → Obsidian | Not started |
| 5 | Handwritten note OCR → Obsidian | Not started |

## Architecture decisions

- **SQLite committed to git** — single source of truth, multi-machine sync via GitHub, full version history of every change.
- **Phone is capture-only.** Phone never edits the DB directly. Captures land in `inbox/` and the desktop processes them under your supervision.
- **Work → Edition → Copy.** A `work` is the abstract book (Dune by Herbert); an `edition` is a specific ISBN/format; a `copy` is the actual instance you own. Lets the same work coexist as a paperback and a Kindle edition without confusion.
- **GitHub Pages over the same `library.db`** — the static search page reads the committed DB via sql.js in the browser. No backend, no hosting bill, no separate API.
