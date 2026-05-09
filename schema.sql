-- Book Catalogue schema
-- Model: Work -> Edition -> Copy
--   Work    = the abstract book ("Dune" by Frank Herbert)
--   Edition = a specific publication (1990 Ace paperback ISBN 978..., or the Kindle edition ASIN B...)
--   Copy    = an instance you own or borrow (the physical book on shelf SF-3, your Kindle license, a current Libby loan)

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS works (
    id              INTEGER PRIMARY KEY,
    title           TEXT    NOT NULL,
    subtitle        TEXT,
    author_display  TEXT,                       -- denormalised "Frank Herbert" or "Herbert & Anderson" for fast display
    original_year   INTEGER,
    original_language TEXT,
    description     TEXT,
    subjects        TEXT,                       -- comma-joined subjects/genres from external lookup
    series_name     TEXT,
    series_position REAL,                       -- 1, 1.5, 2, etc.
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS authors (
    id      INTEGER PRIMARY KEY,
    name    TEXT    NOT NULL,
    sort_name TEXT,                             -- "Herbert, Frank"
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS work_authors (
    work_id     INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    author_id   INTEGER NOT NULL REFERENCES authors(id),
    role        TEXT    NOT NULL DEFAULT 'author',  -- author, translator, illustrator, editor, narrator
    sort_order  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (work_id, author_id, role)
);

CREATE TABLE IF NOT EXISTS editions (
    id              INTEGER PRIMARY KEY,
    work_id         INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    isbn_13         TEXT,
    isbn_10         TEXT,
    asin            TEXT,                       -- Amazon's ID; also used for Kindle editions
    format          TEXT NOT NULL,              -- hardcover, paperback, mass-market, ebook, audiobook, pdf, epub, other
    publisher       TEXT,
    published_year  INTEGER,
    pages           INTEGER,
    duration_minutes INTEGER,                   -- audiobooks
    language        TEXT,
    cover_url       TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_editions_isbn13 ON editions(isbn_13) WHERE isbn_13 IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_editions_asin   ON editions(asin)    WHERE asin    IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_editions_work          ON editions(work_id);

CREATE TABLE IF NOT EXISTS locations (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,           -- "SF-3", "Office overflow box 2"
    description TEXT,                           -- "Living room, top shelf, left side"
    parent_id   INTEGER REFERENCES locations(id) -- optional hierarchy (Room -> Bookcase -> Shelf)
);

CREATE TABLE IF NOT EXISTS vendors (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL UNIQUE,
    kind    TEXT NOT NULL DEFAULT 'digital'     -- digital, audio, library, physical_seller
);

CREATE TABLE IF NOT EXISTS copies (
    id              INTEGER PRIMARY KEY,
    edition_id      INTEGER NOT NULL REFERENCES editions(id) ON DELETE CASCADE,
    medium          TEXT    NOT NULL CHECK (medium IN ('physical', 'digital', 'audio')),

    -- physical-only
    location_id     INTEGER REFERENCES locations(id),
    condition       TEXT,                       -- new, good, worn, damaged

    -- digital/audio
    vendor_id       INTEGER REFERENCES vendors(id),
    vendor_book_id  TEXT,                       -- vendor's internal ID (ASIN, Apple Books ID, etc.)
    file_path       TEXT,                       -- for DRM-free files in Calibre / on disk

    -- borrow tracking (Libby etc.)
    borrowed_until  TEXT,                       -- ISO date if currently on loan

    -- common
    acquired_date   TEXT,
    acquired_price  REAL,
    acquired_source TEXT,                       -- "Powell's Books", "Amazon order #...", "gift from Mom"
    status          TEXT NOT NULL DEFAULT 'owned'
                       CHECK (status IN ('owned', 'borrowed', 'loaned-out', 'sold', 'returned', 'lost')),
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_copies_edition  ON copies(edition_id);
CREATE INDEX IF NOT EXISTS idx_copies_location ON copies(location_id);
CREATE INDEX IF NOT EXISTS idx_copies_vendor   ON copies(vendor_id);
CREATE INDEX IF NOT EXISTS idx_copies_status   ON copies(status);

-- Wishlist: things I want to buy, or might want to buy.
-- work_id is nullable so we can capture a raw title from an X screenshot
-- without first matching it to a real work.
CREATE TABLE IF NOT EXISTS wishlist (
    id                  INTEGER PRIMARY KEY,
    work_id             INTEGER REFERENCES works(id),
    title_raw           TEXT,
    author_raw          TEXT,
    source              TEXT,                   -- "X post by @user", "recommendation from <person>", "bookstore browse"
    source_url          TEXT,
    source_image_path   TEXT,                   -- path inside inbox/ for capture screenshots
    priority            INTEGER DEFAULT 0,      -- 0 = whenever, higher = more wanted
    notes               TEXT,
    captured_at         TEXT NOT NULL DEFAULT (datetime('now')),
    status              TEXT NOT NULL DEFAULT 'wanted'
                            CHECK (status IN ('wanted', 'bought', 'rejected', 'duplicate'))
);

CREATE INDEX IF NOT EXISTS idx_wishlist_status ON wishlist(status);

-- Reading sessions: minimal stub for Phase 4 (audiobook drives etc.)
-- Lets you mark "currently listening to <work>" so voice memos auto-route.
CREATE TABLE IF NOT EXISTS reading_sessions (
    id          INTEGER PRIMARY KEY,
    work_id     INTEGER NOT NULL REFERENCES works(id),
    copy_id     INTEGER REFERENCES copies(id),  -- which copy you're using
    started_at  TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    is_current  INTEGER NOT NULL DEFAULT 1,     -- 1 if this is "currently reading/listening"
    notes       TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_current ON reading_sessions(is_current) WHERE is_current = 1;

-- Full-text search over works
CREATE VIRTUAL TABLE IF NOT EXISTS works_fts USING fts5(
    title,
    subtitle,
    author_display,
    description,
    subjects,
    series_name,
    content='works',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS works_ai AFTER INSERT ON works BEGIN
    INSERT INTO works_fts(rowid, title, subtitle, author_display, description, subjects, series_name)
    VALUES (new.id, new.title, new.subtitle, new.author_display, new.description, new.subjects, new.series_name);
END;

CREATE TRIGGER IF NOT EXISTS works_ad AFTER DELETE ON works BEGIN
    INSERT INTO works_fts(works_fts, rowid, title, subtitle, author_display, description, subjects, series_name)
    VALUES ('delete', old.id, old.title, old.subtitle, old.author_display, old.description, old.subjects, old.series_name);
END;

CREATE TRIGGER IF NOT EXISTS works_au AFTER UPDATE ON works BEGIN
    INSERT INTO works_fts(works_fts, rowid, title, subtitle, author_display, description, subjects, series_name)
    VALUES ('delete', old.id, old.title, old.subtitle, old.author_display, old.description, old.subjects, old.series_name);
    INSERT INTO works_fts(rowid, title, subtitle, author_display, description, subjects, series_name)
    VALUES (new.id, new.title, new.subtitle, new.author_display, new.description, new.subjects, new.series_name);
END;

-- Convenience view: "do I own this work, and how?"
CREATE VIEW IF NOT EXISTS v_ownership AS
SELECT
    w.id            AS work_id,
    w.title,
    w.author_display,
    w.series_name,
    w.series_position,
    e.id            AS edition_id,
    e.format,
    e.isbn_13,
    e.asin,
    c.id            AS copy_id,
    c.medium,
    c.status,
    c.borrowed_until,
    l.name          AS location,
    v.name          AS vendor,
    c.acquired_date
FROM works w
LEFT JOIN editions  e ON e.work_id = w.id
LEFT JOIN copies    c ON c.edition_id = e.id
LEFT JOIN locations l ON l.id = c.location_id
LEFT JOIN vendors   v ON v.id = c.vendor_id;

-- Schema metadata
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version', '1');
