# Book Catalogue — Project Guide for Claude

This file is auto-loaded by Claude Code when opened in this repo. It documents the project structure and the playbook for processing the capture inbox.

## Project shape

- **Catalog DB:** `library.db` (SQLite, committed to repo). Schema in `schema.sql`.
- **Model:** `works` (the abstract book) → `editions` (specific ISBN/format) → `copies` (instances you own/borrow).
- **Lookup UI:** `index.html` deployed to `https://qaz027.github.io/book-catalogue/`. Read-only PWA over the committed `library.db`.
- **Capture inbox:** Phone → email → `inbox/incoming/` → desktop processing (you, Claude) → routed to catalog/wishlist → archived to `inbox/processed/<date>/`.
- **Author/owner:** qaz027 / francis.quimby@gmail.com. Single user, no sharing.

## Directory map

```
library.db, schema.sql                Catalog DB and schema
scripts/init_db.py                    Build/refresh DB from schema, seeds vendors
scripts/_common.py                    Shared upsert helpers
scripts/add_book.py                   Interactive single-book entry
scripts/add_book_batch.py             Non-interactive bulk insert (JSON in, JSON out)
scripts/add_to_wishlist.py            Bulk wishlist insert (JSON in, JSON out)
scripts/import_goodreads.py           Goodreads CSV importer
scripts/import_amazon_kindle.py       Amazon Kindle library importer
inbox/incoming/                       Raw captures awaiting processing (gitignored)
inbox/processed/<YYYY-MM-DD>/         Archived after processing (gitignored)
data/raw/                             Vendor exports (gitignored, private)
index.html, manifest.json             PWA search UI
```

## When the user says "process the inbox" (or similar)

**Goal:** Pull tagged emails from Gmail, route captures to the right destination, get user confirmation before any DB write, archive processed items, optionally commit + push.

### 1. Discover what's pending

Use the Gmail MCP to find unprocessed captures. Try this search first:

```
subject:"[shelf]" -label:book-catalogue-processed in:anywhere
```

Run the same search with `[wishlist]` and `[add]` (defer `[memo]`). The `book-catalogue-processed` label may not exist yet — if so, just use the subject filter.

For each matched thread, call `get_thread` to retrieve the email body and attachments.

### 2. Save attachments to inbox/incoming/

For each attachment, save to a path like:

```
inbox/incoming/<YYYY-MM-DD>_<tag>_<n>.<ext>
```

E.g., `inbox/incoming/2026-05-09_shelf_01.jpg`. The date comes from the email date, not today's date — keeps temporal grouping intact. Use the email's subject suffix (after the tag) as a hint for context, e.g. subject `[shelf] living room shelf 3` → save the location hint for step 4.

### 3. Read each image

Use the `Read` tool to view each saved image. Claude Code can read images directly.

### 4. Route by capture type

#### `[shelf]` — multiple books on a shelf

For each photo:

a. **Extract candidate books.** Look at every visible spine. For each, capture: title, author (if visible), format hint (mass-market / trade paperback / hardcover based on size and proportions), and any visible ISBN.

b. **Show the user the list.** Present as a numbered, editable list:
```
Photo: 2026-05-09_shelf_01.jpg
Subject hint: "living room shelf 3"

Books detected:
  1. Dune — Frank Herbert (mass-market)
  2. Children of Time — Adrian Tchaikovsky (trade paperback)
  3. <unclear spine> — possibly "The Three-Body Problem"
  ...

Location for these books: living room shelf 3 (from subject hint)
Anything to remove or correct? (Otherwise I'll add all 12.)
```

c. **Wait for confirmation.** Don't proceed until the user confirms or edits. Ask explicitly about ambiguous spines.

d. **Build a JSON batch** with the confirmed books:
```json
[
  {
    "title": "Dune",
    "author": "Frank Herbert",
    "format": "mass-market",
    "medium": "physical",
    "location": "living room shelf 3",
    "condition": "good",
    "source_image": "inbox/incoming/2026-05-09_shelf_01.jpg"
  }
]
```

e. **Run** `python3 scripts/add_book_batch.py < batch.json` (write the JSON to a temp file or pipe via heredoc). The script does ISBN lookup, upserts works/editions, inserts copies, returns a JSON summary.

f. **Move the photo:** `mv inbox/incoming/<file> inbox/processed/<YYYY-MM-DD>/<file>` (mkdir as needed).

g. **Label the Gmail thread.** Apply label `book-catalogue-processed` using `label_thread` (create the label first via `create_label` if it doesn't exist).

#### `[wishlist]` — books you want

For each capture (could be a screenshot of an X post, a typed title in the email body, or a photo of a cover):

a. **Extract** title and author. If the email body has typed text, prefer that over OCR.

b. **Note the source.** Pull from email body or subject:
   - URL? → `source_url`
   - "saw on X / from @user / recommended by..." → `source` text
   - Just a photo? → `source_image_path` only

c. **Show the user** the extracted entry, and crucially, **whether the work already matches something owned**: `add_to_wishlist.py` reports `already_owned: true` if found. Surface that as a warning.

d. **Build JSON** and run `python3 scripts/add_to_wishlist.py < batch.json`.

e. **Move file + label thread** as in shelf step f-g.

#### `[add]` — single specific book

Use this when the user wants to add one specific book they've just bought (not a shelf, not a wish — a real new acquisition with cover/spine in clear focus).

a. **Extract** title, author, ISBN if visible. Encourage Claude to look up the ISBN explicitly via Open Library / Google Books.

b. **Ask** about medium (default physical), location/vendor, condition, acquired_date.

c. **Insert** via `add_book_batch.py`.

#### `[memo]` — voice memo

**Deferred to Phase 4.** For now: leave the email in place, do NOT label it processed. Tell the user "memo capture isn't built yet — left in inbox." Skip.

### 5. Summarise and offer commit

Show the user:

```
Processed 3 photos:
  - 2026-05-09_shelf_01.jpg → 12 books added to "living room shelf 3"
  - 2026-05-09_wishlist_02.png → 1 wishlist entry (NEW)
  - 2026-05-09_wishlist_03.png → 1 wishlist entry (already owned — flagged)

3 emails labelled book-catalogue-processed.

Commit and push? (Y/n)
```

If yes: `git add library.db inbox/processed/`, commit with a clear message, then ask before `git push`. Pushing rebuilds the public Pages deploy in ~30s.

## Important rules

- **Always confirm before any DB write.** Vision can hallucinate titles, especially on stylized spines or partially obscured books. The user is the final reviewer.
- **Never auto-push.** Always ask. Pushing rewrites the public Pages site.
- **Never delete inbox files.** Move to `inbox/processed/<date>/`. The user can clean up later.
- **Never edit `library.db` directly with SQL.** Use the `add_*` scripts. They handle FTS triggers, dedupe, foreign keys.
- **Schema changes go through `schema.sql`.** Don't `ALTER TABLE` ad-hoc.
- **Don't process `[memo]` yet.** Phase 4 deals with voice memos. Skip them silently.
- **Treat raw vendor exports as private.** They live in `data/raw/` (gitignored).

## Useful one-liners

Quick stats:
```bash
python3 -c "
import sqlite3
c = sqlite3.connect('library.db')
for tbl in ('works', 'editions', 'copies', 'wishlist'):
    n = c.execute(f'SELECT COUNT(*) FROM {tbl}').fetchone()[0]
    print(f'{tbl:12s} {n}')"
```

Find books that are wishlisted AND already owned:
```sql
SELECT w.id, w.title_raw, w.author_raw, w.notes
FROM wishlist w
JOIN editions e ON e.work_id = w.work_id
JOIN copies c ON c.edition_id = e.id
WHERE c.status = 'owned' AND w.status = 'wanted';
```

Find books with no copy (works imported but not yet owned):
```sql
SELECT w.id, w.title, w.author_display
FROM works w
LEFT JOIN editions e ON e.work_id = w.id
LEFT JOIN copies c ON c.edition_id = e.id
GROUP BY w.id HAVING COUNT(c.id) = 0;
```

## Phase status (for orientation)

| Phase | What | Status |
|-------|------|--------|
| 1 | Catalog spine + lookup PWA | Done |
| 2 | Capture inbox (this playbook) | Active |
| 3 | Obsidian linkage for notes | Not started |
| 4 | Audiobook voice-memo → Whisper → notes | Not started |
| 5 | Handwritten note OCR → Obsidian | Not started |

If the user asks about something cross-phase, ask which phase before assuming.
