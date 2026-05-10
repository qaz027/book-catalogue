# Inbox

Phone captures arrive via **Google Drive**, get downloaded here, processed on the desktop, then moved to `processed/`.

## Capture workflow (phone)

1. Take a photo (or for `[memo]` later, record a voice memo).
2. Share → **Save to Drive** → into the appropriate subfolder of `Book Catalogue/`:

| Drive subfolder           | What goes there                                            | Routes to                  |
|---------------------------|------------------------------------------------------------|----------------------------|
| `Book Catalogue/shelf/`   | Photos of one or more book spines on a shelf               | `copies` table (physical)  |
| `Book Catalogue/wishlist/`| Books you want (X screenshots, photos of covers, notes)    | `wishlist` table           |
| `Book Catalogue/add/`     | One specific newly-acquired book in clear focus            | `copies` table             |
| `Book Catalogue/memo/`    | Voice memo from an audiobook drive (Phase 4 — deferred)    | Obsidian (deferred)        |

If those folders don't exist yet, Claude will create them on the first run.

## Why Drive (not email)

Originally we tried email-with-subject-tags, but the Gmail MCP exposes thread retrieval without attachment download — Claude couldn't pull the photo bytes. Google Drive's MCP includes `download_file_content`, so it works end-to-end.

## Processing workflow (desktop)

Open Claude Code in this repo and tell it:

> Process the inbox.

Claude will:
1. Find new files in `Book Catalogue/<tag>/` Drive folders that haven't been processed before (tracked locally in `.processed_drive_ids.txt`).
2. Download each file to `incoming/` with a timestamped filename.
3. View each image, propose an extracted entry, ask you to confirm.
4. Insert via `scripts/add_book_batch.py` or `scripts/add_to_wishlist.py`.
5. Move the local file to `processed/<YYYY-MM-DD>/`.
6. Record the Drive file ID as processed.
7. Show you a summary, ask whether to commit + push.

See `CLAUDE.md` at the repo root for the exact playbook.

## Folder layout

```
inbox/
  incoming/                  files downloaded from Drive, awaiting processing
  processed/<YYYY-MM-DD>/    archived locally after processing
  .processed_drive_ids.txt   Drive file IDs already handled (one per line, committed)
  .drive_root_id             cached Drive folder ID for "Book Catalogue" (committed)
  README.md                  this file
```

`incoming/` and `processed/` contents are gitignored — raw photos stay on your machine and don't go in the public repo. `.processed_drive_ids.txt` and `.drive_root_id` ARE committed so multi-machine clones share the dedupe state.

## Drive cleanup

Claude doesn't move or delete files in Drive (the MCP doesn't expose those operations). Periodically clear out the `Book Catalogue/<tag>/` folders manually — anything still listed there but already in `.processed_drive_ids.txt` is safe to delete.
