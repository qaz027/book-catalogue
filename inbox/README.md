# Inbox

Phone captures land here, get processed on the desktop, then move to `processed/`.

## Capture workflow (phone)

1. Take a photo (or record a voice memo).
2. Share → **Mail** → send to your own Gmail.
3. Set the subject to start with one of these tags:

| Subject tag    | What it means                                | Routes to                  |
|----------------|----------------------------------------------|----------------------------|
| `[shelf]`      | Photo of one or more book spines on a shelf  | `copies` table (physical)  |
| `[wishlist]`   | Book you want to remember (X screenshot, photo of cover, typed title) | `wishlist` table |
| `[add]`        | A specific single book to add (one cover/spine in clear focus) | `copies` table |
| `[memo]`       | Voice memo from an audiobook drive (Phase 4) | Obsidian (deferred)        |

Subject can include extra text after the tag, e.g.: `[shelf] living room shelf 3`. The trailing text becomes a hint for the location/source.

## Processing workflow (desktop)

Open Claude Code in this repo and tell it:

> Process the inbox.

Claude will:
1. Use the Gmail MCP to find unread emails with the tag subjects above.
2. Download attachments into `inbox/incoming/` with timestamped filenames.
3. For each capture, run the appropriate routing:
   - `[shelf]` → use vision to extract books → confirm with you → bulk insert via `scripts/add_book_batch.py`
   - `[wishlist]` → extract title/author → confirm → `scripts/add_to_wishlist.py`
   - `[add]` → ISBN lookup if visible, else manual confirm → `scripts/add_book_batch.py`
4. Move processed files to `inbox/processed/<YYYY-MM-DD>/`.
5. Mark Gmail threads as processed (label them).
6. Show you a summary and ask if you want to commit + push.

See `CLAUDE.md` at the repo root for the exact playbook Claude follows.

## Folder layout

```
inbox/
  incoming/     raw captures pulled from email, awaiting processing
  processed/    processed captures, organized by date
  README.md     this file
```

`inbox/incoming/` and `inbox/processed/` contents are **gitignored** — raw photos and voice memos stay on your machine and don't go in the public repo. Only the structure and this README are tracked.
