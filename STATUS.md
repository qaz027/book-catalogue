# Status — handoff for the next Claude Code session

**Date written:** 2026-05-12
**Read these first:** `README.md` (system overview + storage topology) and `CLAUDE.md` (the inbox processing playbook — auto-loaded by Claude Code).

This file exists so you don't have to re-read every commit message to know where we left off. Keep it short. Update it at session boundaries.

---

## Where we left off

The catalog is in good shape. The PWA at <https://qaz027.github.io/book-catalogue/> shows the current state. As of last commit:

- **23 works / 23 editions / 23 copies** — all physical, all at one location named "book shelf"
- They came from one shelf photo (IMG_2261) processed via the Drive capture pipeline
- 20 of 23 have publication years and cover URLs (enriched via Open Library)
- 0 of 23 have ISBN-13 — Open Library `search.json` is work-level only (known gap)
- Wishlist is empty

Run `python3 scripts/report.py` for the live snapshot.

---

## The immediate next task: Goodreads import

The user has dropped (or is dropping) a Goodreads export into `data/raw/`. **Start by verifying it's there:** `ls -la data/raw/`. At the time this doc was written, the folder was empty on disk — could be sync lag or it landed somewhere else.

### The single most important thing to know

The user uses Goodreads to track **everything they've read**. This project tracks **everything they own**. *"I have many more books than I read."* — them.

The two sets overlap but don't match:

- Library / Libby reads → in Goodreads, but never owned
- Owned but unread books → won't be on the "read" shelf
- Owned + read → overlap
- Goodreads "to-read" shelf → these belong in `wishlist`, not `copies`

So most Goodreads rows should **not** auto-create copies. Importing naively will create a bunch of fake "owned" copies.

### Overlap with the 23 already-catalogued books

If any of the 23 books on "book shelf" are also in the Goodreads export with `Owned Copies >= 1`, the current `import_goodreads.py` will create a **second** physical copy at `Goodreads import (review)`. That's wrong — we'd duplicate the shelf.

### Three known gaps in `import_goodreads.py` to fix or work around

1. **No copy-level dedup.** The script uses raw INSERT for copies. It only checks `acquired_source = 'Goodreads import'` for idempotency on re-runs — it does NOT check whether the same edition already has a copy from another source (e.g., the shelf capture). **Fix:** mirror the dedup pattern in `add_book_batch.py` (skip if same edition + location + medium already exists).
2. **Goodreads `Owned Copies` is often blank** even for books the user owns — most Goodreads users don't fill that field in. Many real owned books will be missed by the current "if owned_n > 0" logic. Consider also checking for `Bookshelves` membership in ownership-signalling shelves (e.g., `owned`, `kindle`, `audiobook`, `audible`, `physical`). Ask the user which of their shelves indicate ownership before assuming.
3. **The `to-read` shelf isn't routed to wishlist.** Currently it just becomes a work-only row with no copy. It should go into `wishlist` instead.

### Suggested workflow

1. Verify the file: `ls data/raw/`
2. **Dry-run first** to see counts:
   ```bash
   python3 scripts/import_goodreads.py data/raw/goodreads_library_export.csv --dry-run
   ```
3. Look at the user's Goodreads `Bookshelves` column to discover their naming conventions — show them a sample if useful.
4. **Before running for real, have a strategy conversation with the user** covering:
   - Which Goodreads shelves mean "I own this"? (`owned`, `kindle`, etc.)
   - Should we patch `import_goodreads.py` first, or use a one-shot helper script?
   - For physical copies: where should newly-imported owned-physical books go? `Goodreads import (review)` is the current placeholder.
5. If patching: add dedup against existing copies (especially the 23 at "book shelf"), and route `to-read` shelf entries to `wishlist`.
6. Run for real, run `python3 scripts/report.py`, confirm sensible counts, commit, push.

### Other overlap to expect later

Amazon Kindle export, when it arrives, will overlap with:
- Goodreads "kindle" shelf entries (same books)
- Any future Audible export (some titles in both)
- Possibly physical copies of the same title (Kindle + paperback)

The `Work → Edition → Copy` model handles this — same work, multiple editions, multiple copies — but each importer needs its own dedup logic at the copy level. Plan to apply the same fix pattern to `import_amazon_kindle.py` when it sees real data.

---

## Known smaller gaps (defer; not blocking)

- ISBNs not populated on the 23 catalogued books. A follow-up pass against Open Library's `/works/<id>/editions.json` would fix it.
- 3 books didn't match in OL or Google Books: "The Kaggle Book", "The Power of Story", and one more. Visible as "Works missing original_year: 3" in `report.py`. User can manually edit later.
- `import_amazon_kindle.py` is untested with a real export — column aliases are best-effort.
- No Phase 3 (Obsidian) work has started.
- Drive folder cleanup is manual (no MCP support for delete/move). Periodically clear `Book Catalogue/<tag>/` folders on the Drive side.

---

## Rules to keep in mind (mirrored from `CLAUDE.md`)

- **Confirm before every DB write.** Especially for bulk operations.
- **Never auto-push.** Each push rebuilds the public PWA. Always ask the user.
- **Use the scripts, not raw SQL.** They handle FTS triggers, dedup, foreign keys.
- **Schema changes go through `schema.sql`** — no ad-hoc `ALTER TABLE`.
- **`memo/` is deferred to Phase 4** — skip those files.
