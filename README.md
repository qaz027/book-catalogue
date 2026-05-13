# Book Catalogue

A personal database of every book I own — physical and digital, across all vendors and shelves — plus a wishlist of books I want. Designed to answer one question from my phone: **"am I about to buy a book I already own?"**

**Live lookup (any device, read-only):** <https://qaz027.github.io/book-catalogue/>

---

## How the system works (in one picture)

```
   ┌─────────────────────────────────────────────────────────────────┐
   │                                                                 │
   │   ┌──────────┐                                                  │
   │   │  Phone   │  1. take photo → share to Google Drive           │
   │   └────┬─────┘                                                  │
   │        │                                                        │
   │        ▼                                                        │
   │   ┌────────────────────────────────────────┐                    │
   │   │  Google Drive: Book Catalogue/         │  staging only      │
   │   │    shelf/ wishlist/ add/ move/ memo/   │                    │
   │   └────┬───────────────────────────────────┘                    │
   │        │                                                        │
   │        │  2. desktop Claude pulls via MCP                       │
   │        ▼                                                        │
   │   ┌─────────────────────────────────────────┐                   │
   │   │  T7 SSD working copy:                   │  source of truth  │
   │   │  /media/quimbano/T7/Projects/           │                   │
   │   │  Book_Catalogue/                        │                   │
   │   │    ├── library.db   (SQLite catalog)    │                   │
   │   │    ├── scripts/     (Python tooling)    │                   │
   │   │    ├── inbox/       (downloaded files)  │                   │
   │   │    └── ...                              │                   │
   │   └────┬────────────────────────────────────┘                   │
   │        │                                                        │
   │        │  3. git push                                           │
   │        ▼                                                        │
   │   ┌─────────────────────────────────────────┐                   │
   │   │  GitHub: qaz027/book-catalogue          │  sync + hosting   │
   │   │    triggers Pages rebuild               │                   │
   │   └────┬────────────────────────────────────┘                   │
   │        │                                                        │
   │        │  4. phone refresh                                      │
   │        ▼                                                        │
   │   ┌──────────┐                                                  │
   │   │  PWA     │  qaz027.github.io/book-catalogue                 │
   │   └──────────┘                                                  │
   │                                                                 │
   └─────────────────────────────────────────────────────────────────┘
```

**Three storage layers, each with one job:**

| Layer | Path / URL | Role |
|---|---|---|
| **T7 SSD** | `/media/quimbano/T7/Projects/Book_Catalogue/` | Local working copy on this Linux machine. The only place edits happen. **Not in Dropbox.** |
| **GitHub** | <https://github.com/qaz027/book-catalogue> | Sync between computers. Public repo. Also hosts the PWA via GitHub Pages. |
| **Google Drive** | `Book Catalogue/` folder in your Drive | Phone capture staging only. Photos land here from the phone; Claude downloads them to the T7 working copy for processing. The catalog itself doesn't live here. |

Dropbox is not involved.

---

## Daily usage

### Look something up (phone, on the move)

Open <https://qaz027.github.io/book-catalogue/> in a browser, or install it as a home-screen PWA. Search by title or author. **Library tab** shows what you own; **Wishlist tab** shows what you've flagged to buy. Already-owned wishlist entries surface a red flag so you don't double-buy.

### Add a single book from the desktop

```bash
python3 scripts/add_book.py --isbn 9780441013593
# or just: python3 scripts/add_book.py  (interactive prompt)
```

### Add a shelf of books from the phone

1. Take a photo of the shelf (spines visible)
2. Share → Save to Drive → `Book Catalogue/shelf/`
3. On the Linux machine, open Claude Code in `/media/quimbano/T7/Projects/Book_Catalogue/`
4. Tell Claude: **"Process the inbox."**

Claude pulls the photo from Drive, extracts the book list with vision, asks you to confirm/correct, inserts into `library.db`, and offers to commit + push. The full playbook is in `CLAUDE.md`.

### Other phone-capture flows

| Drive subfolder | What goes there |
|---|---|
| `Book Catalogue/shelf/` | Multiple books on a shelf |
| `Book Catalogue/wishlist/` | X screenshots, photos of covers in a store, anything to remember |
| `Book Catalogue/add/` | One specific newly-acquired book |
| `Book Catalogue/move/` | Already-catalogued books photographed in their **new** location (filename = destination, e.g. `storage_box_a.jpg`) |
| `Book Catalogue/memo/` | Voice memos from audiobook drives — **deferred to Phase 4, not yet processed** |

### Tell Claude about a status change in conversation

You don't need to upload anything — just say:

> "I lent Pattern Recognition to Sarah."
> "Sold my paperback of Dune."
> "Borrowed The Hobbit from Libby until 2026-06-01."

Claude routes these through `scripts/change_status.py`.

### Bulk imports (vendor exports)

Drop the file into `data/raw/` (gitignored — your private exports never hit the public repo):

```bash
python3 scripts/import_goodreads.py      data/raw/goodreads_library_export.csv
python3 scripts/import_amazon_kindle.py  data/raw/amazon_kindle.csv
```

Always run with `--dry-run` first to see counts before writing.

### Get a snapshot of the catalog

```bash
python3 scripts/report.py
```

Totals, by location, recent additions, plus useful flags: overdue Libby borrows, wishlist entries you already own, books currently loaned out, works missing metadata.

---

## Repo layout

```
README.md                    ← you are here
STATUS.md                    ← handoff doc for session boundaries
CLAUDE.md                    ← playbook auto-loaded by Claude Code
schema.sql                   ← source of truth for the DB schema
library.db                   ← SQLite catalog (committed)
index.html, manifest.json    ← PWA search UI (served by GitHub Pages)
scripts/
  init_db.py                 ← build/refresh library.db from schema
  add_book.py                ← interactive single-book entry
  add_book_batch.py          ← JSON-driven bulk insert (used by inbox processing)
  add_to_wishlist.py         ← bulk wishlist insert
  move_copies.py             ← relocate existing copies (UPDATE, not new copy)
  change_status.py           ← lifecycle: loaned-out / sold / lost / returned / borrowed
  enrich_metadata.py         ← backfill year/publisher/ISBN/cover from Open Library + Google Books
  import_goodreads.py        ← Goodreads CSV importer
  import_amazon_kindle.py    ← Amazon Kindle export importer
  report.py                  ← snapshot + flag report
  _common.py                 ← shared helpers (DB connect, upserts, normalisation)
inbox/
  incoming/                  ← files downloaded from Drive (gitignored)
  processed/<YYYY-MM-DD>/    ← post-processing archive (gitignored)
  .processed_drive_ids.txt   ← dedup state (committed; multi-machine clones share it)
  .drive_root_id             ← cached Drive folder ID (committed)
data/raw/                    ← vendor exports (gitignored)
```

---

## Cross-machine workflow

The T7 SSD is *this* Linux machine. On any other computer:

```bash
git clone https://github.com/qaz027/book-catalogue.git ~/Projects/Book_Catalogue
cd ~/Projects/Book_Catalogue
# stdlib-only Python, no pip install needed
python3 scripts/report.py
```

You can do desktop work from any clone. Phone captures via Drive still work the same way — the playbook in `CLAUDE.md` handles per-machine state via `inbox/.drive_root_id` and `inbox/.processed_drive_ids.txt` (both committed).

To save changes back:

```bash
git add library.db inbox/.processed_drive_ids.txt
git commit -m "..."
git push       # triggers a Pages rebuild in ~30s
```

---

## Data model in one paragraph

`works` is the abstract book (Dune by Frank Herbert). `editions` is a specific publication of a work (1990 Ace paperback ISBN ..., or the Kindle edition ASIN ...). `copies` is what you actually own or borrow — one row per physical book on a shelf, one row per digital license at a vendor, one row per Libby loan. A copy points at an edition, an edition points at a work. `wishlist` is separate; entries can be linked to a `work_id` (when matched) or kept as raw text (when not yet identified). Full schema in `schema.sql`.

---

## Build phases

| Phase | What | Status |
|---|---|---|
| 1 | Catalog spine + lookup PWA | Done |
| 2 | Drive capture inbox + processing playbook | Done |
| 2.5 | Move workflow (relocate existing copies) | Done |
| 2.7 | Status changes, enrichment, dedup, report, UI polish | Done |
| **2.8** | **Bulk imports (Goodreads, Kindle) with overlap-aware dedup** | **Next — see STATUS.md** |
| 3 | Obsidian linkage for per-book notes | Not started |
| 4 | Audiobook voice memo → Whisper → Obsidian | Not started |
| 5 | Handwritten note OCR → Obsidian | Not started |

For the latest "where did we leave off?" see **`STATUS.md`**.
For the Claude Code processing playbook, see **`CLAUDE.md`**.
