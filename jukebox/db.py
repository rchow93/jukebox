"""SQLite index for the jukebox media library (audio, photo, video).

Denormalized on purpose: browse columns live directly on `media`, which keeps
queries trivial and the schema tiny. This DB is the shared source of truth
that the jukebox UI and the Zune sync read from.

It is a *disposable, rebuildable cache* over the filesystem: the scan is
idempotent and mtime-gated, and media content is never stored here (it is
streamed from files by id), so a corrupt or stale index is healed by a
re-scan. WAL + busy_timeout let the server keep serving reads while a scan
writes, without "database is locked" errors.
"""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    id           INTEGER PRIMARY KEY,
    type         TEXT NOT NULL,              -- 'audio' | 'photo' | 'video'
    path         TEXT UNIQUE NOT NULL,
    title        TEXT,
    artist       TEXT,
    album        TEXT,
    genre        TEXT,
    folder       TEXT,                       -- dir relative to the scan root
    track_number INTEGER,
    disc_number  INTEGER,
    year         INTEGER,
    duration     REAL,
    width        INTEGER,                    -- photo / video
    height       INTEGER,                    -- photo / video
    has_cover    INTEGER DEFAULT 0,
    file_mtime   REAL,
    added_at     TEXT DEFAULT (datetime('now'))
);
-- Covering indexes on the browse dimensions: every browse is an index range
-- scan, flat as the library grows.
CREATE INDEX IF NOT EXISTS idx_media_type_artist ON media (type, artist);
CREATE INDEX IF NOT EXISTS idx_media_type_album  ON media (type, album);
CREATE INDEX IF NOT EXISTS idx_media_type_genre  ON media (type, genre);
CREATE INDEX IF NOT EXISTS idx_media_type_added  ON media (type, added_at);
CREATE TABLE IF NOT EXISTS sync_selection (
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    added_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (media_id)
);
CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT DEFAULT (datetime('now'))
);
-- Addition order is the playback order (media_id is assigned in PK order).
CREATE TABLE IF NOT EXISTS playlist_items (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    added_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (playlist_id, media_id)
);
-- Folders the app added to the library (Windows Media Player-style); the
-- scanner walks the primary root plus every folder registered here.
CREATE TABLE IF NOT EXISTS scan_folders (
    id     INTEGER PRIMARY KEY,
    path   TEXT NOT NULL UNIQUE,
    added_at TEXT DEFAULT (datetime('now'))
);
"""


def _migrate(conn):
    """One-time tracks -> media migration (idempotent, single transaction).

    Existing audio rows keep their ids, so saved selections and old
    /audio/<id> URLs survive the upgrade. The index is fully rebuildable, so
    the escape hatch for any problem is 'delete the DB and re-scan' — never
    a data-loss event.
    """
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    # `media` was just created by SCHEMA above, so the legacy sentinel is:
    # a `tracks` table still present while `media` is empty.
    if "tracks" in tables and conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0:
        conn.execute("""
            INSERT INTO media (type, path, title, artist, album, genre,
                               track_number, disc_number, year, duration,
                               has_cover, file_mtime, added_at)
            SELECT 'audio', path, title, artist, album, genre,
                   track_number, disc_number, year, duration,
                   has_cover, file_mtime, added_at FROM tracks
        """)
        conn.execute("DROP TABLE tracks")
    # `folder` was added after the initial release; older DBs get it here.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(media)")}
    if "folder" not in cols:
        conn.execute("ALTER TABLE media ADD COLUMN folder TEXT")


def connect(db_path):
    """Open (and init) the index at `db_path`. Creates parent dirs as needed."""
    db_path = Path(db_path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")   # safe under WAL, faster
    conn.execute("PRAGMA busy_timeout=5000")    # wait, don't error, under contention
    conn.execute("PRAGMA foreign_keys=ON")      # sync_selection cascade
    conn.executescript(SCHEMA)
    _migrate(conn)
    # After the migration: old DBs may not have had `folder` when SCHEMA ran.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_media_type_folder ON media (type, folder)")
    conn.commit()
    return conn
