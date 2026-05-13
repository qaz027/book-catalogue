# Status — Book Catalogue handoff (2026-05-12)

This file exists so a fresh Claude Code session can pick up the project without re-reading the whole repo. Keep it under one screen of scrolling and update it at session boundaries.

## What the project is

A personal book catalogue. The big-picture spec is in `README.md` and the **operational playbook is in `CLAUDE.md`** (auto-loaded by Claude Code — read it first). Three layers:

1. **Catalog** — SQLite (`library.db`) committed to this repo. Tracks every book the user **owns** (physical or digital), across all vendors and shelves. Plus a wishlist.
2. **Capture inbox** — phone → Google Drive subfolders (`Book Catalogue/{shelf,wishlist,add,move,memo}/`) → desktop processing (Claude) → catalog rows.
3. **Notes (Phase 3+)** — Obsidian vault, linked by `work_id`. Not started yet.

Live read-only PWA: <https://qaz027.github.io/book-catalogue/>
Repo: <https://github.com/qaz027/book-catalogue>
Local working copy: `/media/quimbano/T7/Projects/Book_Catalogue/` (external T7 SSD, exFAT)

## Current catalog state (as of last commit `cc476a7`)

- **23 works, 23 editions, 23 copies** — all physical, all at one location named "book shelf"
- Came from one shelf photo (IMG_2261) processed via the Drive capture pipeline
- 20 of 23 have publication years and cover thumbnails (enriched via Open Library)
- Zero of 23 have ISBN-13s (limitation of OL's `search.json` — see "Known gaps")
- Wishlist: empty
- 3 works didn't match any external metadata: "The Kaggle Book", "The Power of Story", and one more

Run `python3 scripts/report.py` for a live snapshot any time.

## Scripts (each has a docstring — read that for usage)

| Script | One-liner |
|---|---|
| `scripts/init_db.py` | Build/refresh `library.db` from `schema.sql`. Seeds vendors. Idempotent. |
| `scripts/add_book.py` | Interactive single-book entry (ISBN lookup or manual). |
| `scripts/add_book_batch.py` | **JSON in / JSON out** bulk insert. Auto-enriches title+author, skips dup copies at same edition+location/vendor unless `--allow-duplicates`. |
| `scripts/add_to_wishlist.py` | Bulk wishlist insert with already-owned detection. |
| `scripts/move_copies.py` | Relocate existing copies. Returns ok / ambiguous / not_found / same_location. |
| `scripts/change_status.py` | Lifecycle: loaned-out / sold / lost / returned / borrowed (Libby) / found. |
| `scripts/enrich_metadata.py` | Backfill year/publisher/ISBN/cover via Open Library + Google Books. Idempotent. |
| `scripts/import_goodreads.py` | Goodreads CSV importer. **See caveats below.** |
| `scripts/import_amazon_kindle.py` | Amazon Kindle export importer. Untested with real data yet. |
| `scripts/report.py` | Catalog stats + flag report (overdue Libby borrows, already-owned wishlist, etc.). |

## The next concrete task: Goodreads import (with overlap awareness)

The user has dropped (or is about to drop) a Goodreads export into `data/raw/`. **Verify the file exists with `ls data/raw/`** — at handoff time the folder was empty on disk.

**Critical context the next session must understand before running the importer:**

The user uses Goodreads to track **everything they've read**. This project tracks **everything they own**. The intersection is far from total:

- Books they read from the library (Libby, physical library) — read but never owned
- Books they own but haven't read yet — owned but won't be in Goodreads as "read"
- Books they own AND read — overlap
- Goodreads "Want to read" shelf — these belong in `wishlist`, not `copies`

In the user's words: *"I have many more books than I read."*

**Overlap with the 23 already-catalogued books is likely.** Those came from a shelf photo, are physical, at "book shelf". If Goodreads has them marked "Owned Copies >= 1", the current `import_goodreads.py` will create a second physical copy at "Goodreads import (review)" — which is wrong.

### Known limitations of `import_goodreads.py` to fix or work around

1. **No copy-level dedup.** Unlike `add_book_batch.py`, the Goodreads importer raw-INSERTs copies. It checks idempotency only by counting prior `acquired_source = 'Goodreads import'` rows — it does NOT check whether the same edition already has a copy from the shelf capture. **This is the main thing to fix before a bulk run.**
2. **Goodreads "Owned Copies" is often blank** even for books the user owns — Goodreads users rarely fill this in. Many real owned books will be missed.
3. **The "to-read" shelf isn't routed to wishlist.** It should be: those entries should go to the `wishlist` table, not just be created as work-only with no copy.

### Suggested approach for the Goodreads import

Have a conversation with the user about strategy BEFORE running:

1. **Always run `--dry-run` first** and report counts: new works, matched-to-existing works, would-create copies, would-skip-as-duplicate.
2. **Ask which Goodreads shelves indicate ownership.** Likely candidates: `owned`, `physical`, `kindle`, `audiobook`, `audible`. The default importer only uses the "Owned Copies" numeric column.
3. **Route "to-read" to wishlist**, not the catalog. Probably wants a code change or a separate one-off script. Easier path: filter the CSV before running the importer, OR add a `--wishlist-shelves to-read,wishlist` flag.
4. **Patch `import_goodreads.py` to skip copy creation if a copy already exists** for the same edition + location (mirror the dedup in `add_book_batch.py`). Without this, expect duplicate physical copies for any of the 23 books that are also in Goodreads.

### Useful queries the next session will probably want

```bash
# How much overlap does the user's Goodreads file have with existing catalog?
python3 -c "
import csv, sqlite3
db = sqlite3.connect('library.db'); db.row_factory = sqlite3.Row
existing = {(r['title'].lower(), (r['author_display'] or '').lower())
            for r in db.execute('SELECT title, author_display FROM works')}
with open('data/raw/goodreads_library_export.csv', newline='') as f:
    overlap = 0; total = 0
    for row in csv.DictReader(f):
        total += 1
        key = (row['Title'].lower(), row['Author'].lower())
        if any(t.startswith(key[0][:20]) for t,_ in existing): overlap += 1
print(f'goodreads rows: {total}, likely-overlap with catalog: {overlap}')
"
```

```bash
# Dry-run the importer and inspect counts
python3 scripts/import_goodreads.py data/raw/goodreads_library_export.csv --dry-run
```

## Known gaps (not blocking, can defer)

- **ISBNs missing on all 23 catalogued books.** Open Library's `search.json` returns work-level records without ISBNs. A follow-up enrichment pass against `/works/<id>/editions.json` would fix this. Not urgent — FTS search works on title+author.
- **`import_amazon_kindle.py` is untested with a real export.** Column aliases are best-effort. User mentioned having an Amazon export but hasn't dropped it yet.
- **No Phase 3 (Obsidian) work has started.**
- **Drive folder cleanup is manual.** The Drive MCP doesn't expose move/delete, so the user has to periodically clear `Book Catalogue/<tag>/` folders by hand. The processed Drive file IDs are tracked in `inbox/.processed_drive_ids.txt` (committed).

## Important rules (mirrored from CLAUDE.md — please re-read CLAUDE.md too)

- **Confirm before every DB write.** Vision can hallucinate; bulk imports can duplicate. Show counts, ask, then act.
- **Never auto-push.** Every push rebuilds the public PWA. Always ask.
- **Use the scripts, not raw SQL.** They handle FTS triggers, dedupe, foreign keys.
- **Schema changes go through `schema.sql`.** Don't `ALTER TABLE` ad-hoc.
- **Memo captures are deferred to Phase 4.** Skip files in `Book Catalogue/memo/`.

## Phase status

| Phase | What | Status |
|-------|------|--------|
| 1 | Catalog spine + lookup PWA | Done |
| 2 | Drive capture inbox + processing playbook | Done |
| 2.5 | Move workflow | Done |
| 2.7 | Status changes, enrichment, dedup, report, UI polish | Done (overnight 2026-05-10 → 2026-05-11) |
| **2.8** | **Bulk imports (Goodreads, Kindle) with overlap-aware dedup** | **NEXT — needs fix in `import_goodreads.py` before running** |
| 3 | Obsidian linkage for per-book notes | Not started |
| 4 | Audiobook voice-memo → Whisper → Obsidian | Not started |
| 5 | Handwritten note OCR → Obsidian | Not started |
