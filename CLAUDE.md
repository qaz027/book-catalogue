# Book Catalogue — Project Guide for Claude

This file is auto-loaded by Claude Code when opened in this repo. It documents the project structure and the playbook for processing the capture inbox.

## Project shape

- **Catalog DB:** `library.db` (SQLite, committed to repo). Schema in `schema.sql`.
- **Model:** `works` (the abstract book) → `editions` (specific ISBN/format) → `copies` (instances you own/borrow).
- **Lookup UI:** `index.html` deployed to `https://qaz027.github.io/book-catalogue/`. Read-only PWA over the committed `library.db`.
- **Capture transport:** **Google Drive** — phone shares to `Book Catalogue/<tag>/` folders; Claude pulls via `mcp__claude_ai_Google_Drive__*` tools. (Email/Gmail was tried first; the Gmail MCP cannot download attachment bytes, so Drive replaced it.)
- **Author/owner:** qaz027 / francis.quimby@gmail.com. Single user, no sharing.

## Directory map

```
library.db, schema.sql                Catalog DB and schema
scripts/init_db.py                    Build/refresh DB from schema, seeds vendors
scripts/_common.py                    Shared upsert helpers
scripts/add_book.py                   Interactive single-book entry
scripts/add_book_batch.py             Non-interactive bulk insert (JSON in, JSON out)
scripts/add_to_wishlist.py            Bulk wishlist insert (JSON in, JSON out)
scripts/move_copies.py                Relocate existing copies — UPDATE, not new copy
scripts/import_goodreads.py           Goodreads CSV importer
scripts/import_amazon_kindle.py       Amazon Kindle library importer
inbox/incoming/                       Files downloaded from Drive, awaiting processing (gitignored)
inbox/processed/<YYYY-MM-DD>/         Archived after processing (gitignored)
inbox/.processed_drive_ids.txt        Tracks Drive file IDs already processed (committed — multi-machine state)
inbox/.drive_root_id                  Cached Drive ID of the "Book Catalogue" folder (committed)
data/raw/                             Vendor exports (gitignored, private)
index.html, manifest.json             PWA search UI
```

## Drive folder convention

The user keeps a folder in Google Drive called **`Book Catalogue`** with these subfolders:

```
Book Catalogue/
  shelf/        photos of one or more book spines on a shelf (new captures)
  wishlist/     X screenshots, photos of covers in a store, anything to remember
  add/          one specific newly-acquired book in clear focus
  move/         photos of already-catalogued books in their NEW location
  memo/         voice memos from audiobook drives  (Phase 4 — defer)
```

Phone capture is one-tap: take photo → Share → Save to Drive → choose the right subfolder.

If any of these folders are missing on first invocation, **create them** using `mcp__claude_ai_Google_Drive__create_file` with the folder mime type. Tell the user where to drop captures from now on.

## When the user says "process the inbox" (or similar)

**Goal:** Find new files in the Drive subfolders, download them locally, route through the right pipeline, get user confirmation before any DB write, archive locally, mark the Drive file as processed, optionally commit + push.

### 1. Locate the Drive root folder

If `inbox/.drive_root_id` exists, read the cached folder ID. Otherwise:
- Use `mcp__claude_ai_Google_Drive__search_files` with a query like `name = 'Book Catalogue' and mimeType = 'application/vnd.google-apps.folder'`
- If found, write the ID to `inbox/.drive_root_id`
- If not found, create it (and the four subfolders) with `create_file`, then write the ID

### 2. List candidate files

For each tag folder (`shelf`, `wishlist`, `add`, `move` — skip `memo`):
- Use `search_files` with `'<folder-id>' in parents and trashed = false` to list children
- Filter out anything whose Drive file ID is already in `inbox/.processed_drive_ids.txt`

If nothing pending: tell the user "Drive inbox is empty," stop.

### 3. Download to inbox/incoming/

For each pending file:
- Use `mcp__claude_ai_Google_Drive__get_file_metadata` to get the original filename and modified time
- Use `mcp__claude_ai_Google_Drive__download_file_content` to pull the bytes
- Save to `inbox/incoming/<YYYY-MM-DD>_<tag>_<short-id>.<ext>` where the date comes from the file's modifiedTime, not today, and short-id is the first 6 chars of the Drive ID

### 4. Read each image

Use the `Read` tool to view the saved file. Claude Code can read images directly.

### 5. Route by tag

#### `shelf/` — multiple books on a shelf

a. **Extract candidate books.** Look at every visible spine: title, author (if visible), format hint (mass-market / trade paperback / hardcover from spine width and proportions), any visible ISBN.

b. **Show the user the list.** Numbered, editable:
```
Photo: 2026-05-10_shelf_a3f4c2.jpg  (from Drive: "20260510_shelf_living_room.jpg")
Books detected:
  1. Dune — Frank Herbert (mass-market)
  2. Children of Time — Adrian Tchaikovsky (trade paperback)
  3. <unclear spine> — possibly "The Three-Body Problem"
  ...

What's the location for these books? (e.g., "living room shelf 3")
Anything to remove or correct?
```

c. **Wait for confirmation.** Don't proceed until the user confirms. Ask explicitly about ambiguous spines.

d. **Build a JSON batch:**
```json
[
  {
    "title": "Dune",
    "author": "Frank Herbert",
    "format": "mass-market",
    "medium": "physical",
    "location": "living room shelf 3",
    "condition": "good",
    "source_image": "inbox/incoming/2026-05-10_shelf_a3f4c2.jpg"
  }
]
```

e. **Run** `python3 scripts/add_book_batch.py < batch.json` (heredoc the JSON or write it to a temp file). The script does ISBN lookup, upserts works/editions, inserts copies, returns a JSON summary.

f. **Move locally:** `mv inbox/incoming/<file> inbox/processed/<YYYY-MM-DD>/<file>` (mkdir as needed).

g. **Mark processed in Drive:** append the Drive file ID to `inbox/.processed_drive_ids.txt`. Do NOT try to move/delete files in Drive — the Drive MCP doesn't expose those operations. The user will manually clean the Drive folder periodically.

#### `wishlist/` — books you want

a. **Extract** title and author. If the file is a screenshot of an X post, OCR for the book title and the post URL. If a typed note, parse the text. If a cover photo, extract title/author.

b. **Note the source:** filename, "from X (handle: @user)", URL if visible.

c. **Show the user** the extracted entry. The script flags `already_owned: true` if the work is already in `copies` — surface that.

d. **Build JSON** and run `python3 scripts/add_to_wishlist.py < batch.json`.

e. **Move file + record processed ID** as in shelf step f-g.

#### `add/` — single specific book

Use this when the user just bought one specific book.

a. **Extract** title, author, ISBN if visible. Look up the ISBN explicitly via Open Library / Google Books.

b. **Ask** about medium (default physical), location/vendor, condition, acquired_date.

c. **Insert** via `add_book_batch.py`.

#### `move/` — relocate already-catalogued books

Use this when books that already exist in `copies` have physically moved (shelf → box, box → different shelf, etc.). This is an UPDATE on `copies.location_id`, NOT a new copy.

a. **Get the destination.** Look first at the Drive filename (`storage_box_a.jpg` → "Storage Box A"). If unclear, ask the user.

b. **Extract titles from the photo** the same way you do for `shelf/`. You don't need authors for resolution most of the time — title is usually enough.

c. **Build a move JSON list** and run `python3 scripts/move_copies.py < moves.json`:
```json
[
  {"title": "Dune", "author": "Frank Herbert", "destination": "Storage Box A",
   "source_image": "inbox/incoming/2026-05-10_move_a3f4c2.jpg"},
  {"title": "Children of Time", "destination": "Storage Box A"}
]
```

d. **Handle the three result types** from the script's output:

- **`status: "ok"`** — moved. Show "from → to" in the summary.
- **`status: "ambiguous"`** — multiple copies match (e.g., user owns two paperbacks of Dune on different shelves). The result includes a `candidates` array with each copy's id, current location, and format. Show this to the user, ask which one they moved, then re-submit a JSON entry with `copy_id` set to the chosen one.
- **`status: "not_found"`** — book in the photo isn't in the catalog. Ask the user:
  > "I saw 'Some Book' in the box but it's not in your catalog. Add it as a new copy at 'Storage Box A', or skip?"
  - If add: build a JSON entry for `add_book_batch.py` with `location: "<destination>"` and run that.
  - If skip: move on.
- **`status: "same_location"`** — the copy is already at that location (re-submitted by accident). No-op; report and move on.

e. **Audit trail.** `move_copies.py` appends a dated line to `copies.notes` for each move (`[2026-05-10] moved 'book shelf' → 'Storage Box A'`). Don't add additional manual notes unless the user explicitly asks.

f. **Move file + record processed ID** as in shelf step f-g.

#### `memo/` — voice memo

**Deferred to Phase 4.** Don't process. If you see files there, tell the user "memo capture isn't built yet — left in place."

### 6. Summarise and offer commit

```
Processed 3 files:
  - shelf/20260510_shelf_living_room.jpg  → 12 books to "living room shelf 3"
  - wishlist/x_post_001.png               → 1 wishlist entry (NEW)
  - wishlist/x_post_002.png               → 1 wishlist entry (already owned — flagged)

3 Drive file IDs recorded as processed.

Commit and push? (Y/n)
```

If yes: `git add library.db inbox/.processed_drive_ids.txt`, commit with a clear message, then ask before `git push`.

## Important rules

- **Always confirm before any DB write.** Vision can hallucinate titles, especially on stylized spines or partially obscured books. The user is the final reviewer.
- **Never auto-push.** Always ask. Pushing rewrites the public Pages site.
- **Never delete or move files in Drive.** The MCP doesn't expose those operations and the user controls cleanup. Just record the ID locally and skip on next run.
- **Never delete inbox/incoming files.** Move to `inbox/processed/<date>/`.
- **Never edit `library.db` directly with SQL.** Use the `add_*` scripts. They handle FTS triggers, dedupe, foreign keys.
- **Schema changes go through `schema.sql`.** Don't `ALTER TABLE` ad-hoc.
- **Don't process `memo/` yet.** Phase 4.
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

Find books wishlisted that are already owned:
```sql
SELECT w.id, w.title_raw, w.author_raw
FROM wishlist w
JOIN editions e ON e.work_id = w.work_id
JOIN copies c   ON c.edition_id = e.id
WHERE c.status = 'owned' AND w.status = 'wanted';
```

## Phase status

| Phase | What | Status |
|-------|------|--------|
| 1 | Catalog spine + lookup PWA | Done |
| 2 | Capture inbox (Drive transport) | Done — pivoted from Gmail (no attachment download) |
| 3 | Obsidian linkage for notes | Not started |
| 4 | Audiobook voice-memo → Whisper → notes | Not started |
| 5 | Handwritten note OCR → Obsidian | Not started |

If the user asks about something cross-phase, ask which phase before assuming.
