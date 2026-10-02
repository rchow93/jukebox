# Jukebox → Media Portal — Refactor Plan

One local app with **three faces** that share one source of truth:
**Jukebox** (music), **Photo Album** (slideshow), **DVD Player** (video).
All three browse + play in the browser, let the user *add* items to a sync
queue, and sync to the Zune over USB. The SQLite index (`index.db`) is the
SoT for both the catalog and the sync state.

## The three faces

1. **Jukebox** (existing, keep working): Wurlitzer cabinet, browse
   artist/album/genre, play in browser, push tracks to the Zune
   (Abstract Album objects for device-side grouping).
2. **Photo Album**: old photo-album look — albums as pages/strips, click a
   photo for full view, **slideshow mode** (auto-advance, arrow keys),
   sync the photo library to the Zune (device slideshow) via
   `zune_sync.sync_photos`.
3. **DVD Player**: cinema portal — browse videos (title/year/duration),
   in-browser playback via a Range-streaming endpoint, sync to the Zune via
   `zune_sync.sync_video` (ffmpeg transcode when needed, subtitles optional).

A face is a UI mode. Switching faces moves no data — each face just filters
the same `media` table by `type` and keeps its own sync selection.

## Architecture (the enterprise answer)

- **Unified `media` table** with `type` ∈ {'audio','photo','video'} plus
  type-specific nullable columns. One scan pass, one upsert, one query path,
  one push path, one stream path.
- **WAL + busy_timeout** (`journal_mode=WAL`, `synchronous=NORMAL`,
  `busy_timeout=5000`): the server keeps serving reads while a scan writes;
  no "database is locked".
- **Covering indexes** on `(type, artist)`, `(type, album)`, `(type, genre)`,
  `(type, added_at)`: every browse is an index range scan, O(log n + k), flat
  as the library grows. Cross-type "recent" is one query, not a UNION.
- **Corruption posture**: the DB is a *disposable, rebuildable cache*.
  Filesystem = content SoT; index = pure derivation (idempotent, mtime-gated
  incremental scan in one atomic transaction; content never stored in the DB,
  streamed by id). A corrupt DB heals via `integrity_check()` + re-scan. Even
  the migration's escape hatch is "delete index.db and re-scan".

## Target schema

```sql
CREATE TABLE media (
  id INTEGER PRIMARY KEY,
  type TEXT NOT NULL,               -- 'audio' | 'photo' | 'video'
  path TEXT UNIQUE NOT NULL,
  title TEXT, artist TEXT, album TEXT, genre TEXT,
  track_number INTEGER, disc_number INTEGER,
  year INTEGER, duration REAL,
  width INTEGER, height INTEGER,    -- photo / video
  has_cover INTEGER DEFAULT 0,
  file_mtime REAL,
  added_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_media_type_artist ON media (type, artist);
CREATE INDEX idx_media_type_album  ON media (type, album);
CREATE INDEX idx_media_type_genre  ON media (type, genre);
CREATE INDEX idx_media_type_added  ON media (type, added_at);

-- "added to the sync queue" state, persisted across sessions:
CREATE TABLE sync_selection (
  media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
  added_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (media_id)
);
```

## Migration (tracks → media)

Idempotent, runs on connect: if `tracks` exists, create `media`,
`INSERT INTO media SELECT … FROM tracks` with `type='audio'`, `DROP TABLE
tracks` — all in one transaction. Zero data loss possible: worst case is a
re-scan.

## Scan changes

Single tree walk, dispatch on extension, same mtime gate + single commit:
- audio: `.mp3/.m4a/.wav/.flac` — mutagen easy mode (as today).
- video: `.mp4/.m4v/.mkv/.mov/.avi/.webm` — mutagen where tags exist, else
  title = file stem.
- photo: `.jpg/.jpeg/.png/.bmp` — **album = parent folder name** (photo
  albums are folders), title = stem; no new deps (dimensions optional).

## Streaming + API

- `/audio/<id>` (existing); `/video/<id>` and `/photo/<id>` use the same
  Range-streaming handler (browser `<video>` seeks via Range).
- `/api/photos`, `/api/photo-albums`, `/api/videos` — type-filtered browse.
- `POST/DELETE /api/select` — add/remove from `sync_selection`.
- `/api/push` gains `type`: audio → `sync_audio` + abstract albums (existing),
  photo → `sync_photos`, video → `sync_video` (+ffmpeg).

## UI

- Mode switcher on the cabinet: **Jukebox | Photo Album | DVD Player** — each
  with its own retro treatment (cabinet / sepia album pages / dark cinema).
- Photo face: album strip → photo grid → slideshow (auto-advance, ←/→, Esc);
  per-photo "add to sync" toggle; Sync button.
- DVD face: video list with duration/year; click → fullscreen `<video>`;
  add to sync; Sync button shows progress (ffmpeg transcoding can take a
  while).
- Sync queue persisted in `sync_selection` — survives restarts.

## Build order (small pieces)

- **Phase 5 — Foundation**: `db.py` (media schema, WAL pragmas, migration),
  `core.py` (multi-type scan, type-filtered queries), `server.py` (type-aware
  `/api/query`, `/photo/<id>`, `/video/<id>`, select endpoints). Audio face
  must stay fully working. Verify: re-scan music library, old endpoints
  unchanged.
- **Phase 6 — Photos**: photo scan (folder = album), `/api/photo-albums`,
  photo grid + slideshow UI, `sync_photos` push. Verify: fixture of
  2 albums / 6 photos; device slideshow when the Zune is attached.
- **Phase 7 — DVD Player**: video scan, `/api/videos`, DVD face UI with
  in-browser playback, `sync_video` push. Verify: one small fixture video;
  device playback.
- **Phase 8 — Polish (optional)**: sync-queue management UI (view/remove
  queued items), Zune delete-from-UI, `/api/random` endpoint.

## Risks / notes

- Browser `<video>` plays mp4/m4v/webm natively; mkv/avi previews may be
  limited — device playback still works via transcode. Accept "no in-browser
  preview" for odd formats initially.
- Keep the UI at 2 deps (mutagen + flask); device push stays lazy-imported
  (`zune_sync` pulls in pyusb/pycryptodome only on real push).
- ffmpeg already located by `zune_sync.find_ffmpeg()` for the push path.
- **The real Zune library must always be left clean after any device test.**
