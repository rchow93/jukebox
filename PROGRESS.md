# Jukebox — Build Progress

A local retro diner jukebox: scan a music folder, index it in SQLite, play by
artist / album / genre, in a 2D Wurlitzer-style cabinet. The core engine is
UI-agnostic so it can later power zune-cli (shared index = source of truth).

## Stack (lightest path)
- Python core: stdlib `sqlite3` + `mutagen` (m4a/mp3 tags + cover art)
- Flask server: serves UI + JSON API + audio streaming (Range/seek)
- Vanilla HTML/CSS/SVG/JS 2D cabinet (no framework)
- venv: any Python 3 venv with mutagen + flask (see requirements.txt)

## Layout
```
jukebox/
  jukebox/
    core.py            # engine: scan + index + query  (reusable by zune-cli)
    zune.py            # shared-index → Zune handoff (fetches the published zune-cli for the USB MTP layer)
    db.py              # SQLite schema + connect()
    server.py          # Flask app factory (create_app)
    static/
      index.html       # cabinet UI
      style.css        # cabinet styling
      app.js           # browse + playback
  run.py               # entry point
  requirements.txt     # mutagen, flask
```

## Status
- **Phase 1 — Core engine: DONE + verified.** scan (incremental by mtime),
  query by artist/album/genre, artists/genres/albums/stats. CLI:
  `python -m jukebox.core scan|artists|genres|stats|query --root … --db …`
- **Phase 2 — Server + minimal UI + playback: DONE + verified.** Flask serves
  `/` (UI), `/api/{stats,artists,albums,genres,query,scan}`, `/audio/<id>`.
  Browser: browse artists/albums/genres → click track → plays; now-playing bar;
  next/prev; "Surprise me".
- **Phase 3 — Full retro Wurlitzer visuals: DONE + verified.** cabinet body,
  glowing marquee (now playing), A1–F0 selection grid (60 slots mapped to the
  current track list — lit slots play their track), glass panel, coin slot,
  lighted trim.
- **Phase 4 — Zune integration: DONE + verified.** zune-cli vendored into the
  repo as `jukebox/zune_sync.py` (importable module; MTP/WMDRM handshake intact —
  future zune changes happen here, the repo is the SoT). `jukebox/zune.py`
  exposes get_selection / selection_paths / push_paths / push_track_ids
  (lazy-imports zune_sync, so the UI needs no pyusb). Server: `/api/export`
  (selection as JSON) and `/api/push` (POST {ids} → device). UI: "⇧ Zune" button
  pushes the current browsed selection. Verified live on a Zune 30: handshake,
  read, push, delete all work; test tracks cleaned up. Album grouping: the Zune
  rejects the per-track 0xDC9A string prop (MTP 0x200f, non-fatal) — it models
  albums as a 0xBA03 Abstract Album object, so `zune.push_paths` now creates one
  per album on push (idempotent). Also fixed `_track_tags` to use mutagen easy
  mode — the old ID3-only read silently dropped every tag on m4a. Verified live:
  push_track_ids pushed 4 tracks (mp3+m4a) + created both abstract albums.
  zune-cli is the published rchow93/zune-cli repo (Python + mutagen + pyusb +
  pycryptodome; no DB today — the jukebox SQLite index becomes the shared SoT).
- **Phase 5 — Media foundation: DONE + verified** (see PLAN.md). `tracks` is now a
  unified `media` table (`type` audio|photo|video; ids preserved by the idempotent
  tracks→media migration), WAL + busy_timeout + covering indexes. Scan is one
  multi-type walk (audio +.wav/.flac, video, photo: folder = album). API:
  type-aware `/api/query`, `/api/select` sync queue (GET/POST/DELETE),
  `/photo/<id>` + `/video/<id>` streaming alongside `/audio/<id>`. Audio face
  unchanged — same ids/URLs, old endpoints green.
- **Phase 6 — Photo Album face: DONE + verified** (see PLAN.md).
  - Backend: `/api/albums?type=photo`, `/api/query?type=photo&album=…`,
    `/photo/<id>` streaming; `zune.push_media_ids(…, "photo")` → vendored
    `sync_photos` (re-encodes every image to an exact-screen baseline JPEG,
    dedups by filename). Verified: albums Beach(2)/City(1); push against a
    real Zune 30 returned 200 JSON; device restored clean afterward.
  - UI: face switcher on the cabinet (🎵 Jukebox / 📸 Photos / 🎬 DVD);
    photo face = sepia-tinted glass, album list → photo grid
    (thumbnails via `/photo/<id>`) → click for full-screen slideshow
    (auto-advance 4s, ←/→ + buttons, Esc to close). "⇧ Zune" is face-aware:
    audio pushes the track queue, photos push the open album as
    `{type:'photo', ids}`.
- **Phase 7 — DVD Player face: DONE + verified** (see PLAN.md).
  - Backend: `zune.push_videos` (vendored `sync_video`: ffmpeg transcode to
    WMV 320x240 into the device Video folder, dedup by stem, subtitles map
    plumbed but empty for now) + `sync_video` now returns its counts like
    `sync_photos`; `push_media_ids(…, "video")` dispatches to it.
  - UI: disc list (title / folder / duration) → click plays in the shared
    stage overlay (`<video controls>` streamed from `/video/<id>`); the
    slideshow stage doubles as the video stage — Esc closes, arrows are
    no-ops in video mode. "⇧ Zune" on the DVD face pushes all discs as
    `{type:'video', ids}` (transcoded at push time).
  - Verified: `/api/query?type=video`, `/video/<id>` streams `video/mp4`
    (42 KB real 3s testsrc fixture, duration indexed), stage markup served.
    Real-device video push left for the e2e walkthrough with the user.
- **Phase 8 — iTunes/WMP-style playback: DONE + verified** (user request:
  "play by artists, albums, genres, folders, or a playlist — think iTunes").
  - **Play-on-click**: clicking a group row (artist/album/genre/folder) now
    *starts playing* it and lists its tracks below with a back row, instead of
    only listing. `playAndList(tracks, title, backLabel, backFn)`.
  - **Folders browse**: `media.folder` column (dir relative to scan root;
    backfilled + healed by the incremental scan; covering index; ALTER for
    older DBs — folder index is created *after* the migration in `connect()`,
    since `CREATE TABLE IF NOT EXISTS` leaves old tables untouched).
    `/api/folders` + `/api/query?folder=…` + Folders tab. Verified on the real
    library: 9 folders, the largest with 20 tracks).
  - **Playlists**: `playlists` + `playlist_items` tables (addition order =
    playback order; FK cascades). API: `GET/POST /api/playlists`
    (create), `GET/POST/DELETE /api/playlists/<id>` (tracks / add / delete
    playlist), `DELETE /api/playlists/<id>/tracks` (remove). UI: Playlists
    tab (open → plays from first track, ✕ per row removes) + tools row on the
    audio face: "➕ Add list to ▾" adds the current list (whatever view is
    open) to a playlist, New playlist, Delete. Playlists sync via the existing
    "⇧ Zune" button (a loaded playlist *is* the queue). Verified end-to-end
    against the real library: created a playlist, added 2 tracks
    (idempotent re-add), removed + re-added one, counts correct.

- **Phase 9 — iTunes/Music-style reconcile sync: DONE + verified live** (after the
  physical re-plug cleared the stale macOS USB state).
  (user request: playlist or single song → force sync; deletions propagate; Music-app feel).
  - Model: `sync_selection` table = the sync list; the Zune *mirrors* it. Adding a
    song to the list and syncing pushes it; unchecking a song and syncing removes
    it from the device (add = in list, missing on device; remove = on device, not
    in list; match by title — the key sync_audio uses to name "<title>.mp3").
  - Backend: `zune.sync_device(db)` (enumerate MP3 objects on the device, delete
    strays, push the missing, rebuild abstract albums) + `POST /api/sync`
    (503 no device / 502 device error / 400 bad input).
  - UI (audio face): per-song ☑/☐ checkbox on every track row; "✓ Sync this list"
    adds the current view (artist/album/genre/folder/playlist) to the sync list
    and reconciles; deck "⇧ Zune" = reconcile; sync-count status line. Removing a
    song from a playlist (✕) and deleting a playlist also drop those tracks from
    the sync list, so the next sync deletes them on the device. Photos/video faces
    keep their push-only flow. Audio-only sync for now (user constraint: never
    touch the personal videos outside the music root).
  - Verified at API level: markup served, /api/select add/list/remove, /api/sync
    error mapping.
  - **Live device verification (a real Zune, one test track):**
    - remove from list → sync → `removed: 1`, device MP3 gone (delete propagates);
      firmware auto-GC'd the now-empty "R" abstract album.
    - re-add → sync → `added: 1` push; no-op re-sync → `added: 0, removed: 0`
      (idempotent mirror).
    - Two matching bugs found + fixed while verifying (device object names are
      the *file stem, dots stripped* — "01 On The Ground.mp3" — NOT the title
      tag; verified by enumeration):
      1. `zune.sync_device` matched device objects against `m.title` — could
         never match. Now matches on the normalized stem (`_dev_key`), treats
         duplicates as stray copies (keeps one), and rebuilds missing abstract
         album objects for the *whole* sync list (not just newly pushed tracks).
         Also fixed `len(removed)` → `removed` in the return (int counter).
      2. `sync_audio` (in zune-cli) deduped against `"<title>.mp3"` so re-pushing
         the same track accumulated duplicate objects on the device (found 2
         during debug). Now dedupes on the normalized stem. Regression-tested:
         two pushes of the same file → exactly 1 object.
  - **Subset-selection UI (user request: "sync just one or two songs / add
    specific songs to a playlist"):** the ☑ checkbox is now the whole model —
    checked = "this song is on my Zune" (sync list = device mirror). Added:
    "Sync List" tab (shows exactly what the Zune will contain; uncheck to
    drop), "➕ Add checked to ▾" (adds only the ☑-checked songs of the current
    view to a playlist), and the sync-count status text is clickable → opens
    the Sync List tab. No backend changes (all endpoints pre-existed).
    - Known, non-fatal: firmware rejects 0xDC9A (Genre) / 0xDC9B (AlbumArtist)
      string props with MTP 0x200f (logged, push still succeeds). macOS
      occasionally denies a *second* USB claim in the same process (EACCES)
      after sleep — first connect per process works; the server keeps one
      long-lived connection, so this only affects ad-hoc test scripts.
- **Phase 10 — pick folders from the laptop (WMP/iTunes-style): DONE + verified**
  (user: "allow our app to pick folders that will be sync and scan to the
  jukebox").
  - Library = primary `--root` + app-registered folders: new `scan_folders`
    table; `scan()` now takes a list of roots and walks the union, dropping
    rows only for files absent from *every* root (so adding a folder never
    orphans the primary's rows). `library_roots()` is the single source of
    truth — used by run.py startup, /api/scan, add, and remove.
  - App-added folders self-label: the `folder` column gets a prefix of the
    folder's own name, so each added folder is its own group in the Folders
    view (root-level files = "Extra", subdirs = "Extra/sub/…").
  - API: `GET /api/dirs?path=…` (server-side dir listing — a browser can't
    enumerate the laptop), `GET/POST /api/library/folders`, `DELETE
    /api/library/folders/<id>` (rescans; the folder's rows drop). Adding the
    main root, or a folder inside it, is refused.
  - UI (Folders tab): "➕ Add a folder to the library…" row → drill-down
    picker (↑ up / subfolder rows / "✓ Add this folder" CTA); a ✓ on each
    media-folder row checks the whole folder into the sync list and opens it
    for unchecking, then ⇧ Zune pushes; "Library folders" list with ✕ to
    remove (files on disk untouched).
  - Verified live: added a /tmp fixture folder → its song indexed under its
    own group (stats +1, the folder query works); removed → count restored. Subdir/primary adds refused. README.md written documenting all
    controls.
- **Phase 11 — DVD face: pick movies to sync (mirror model): DONE + verified**
  (user: "sync a folder — it has 100 movies — pick the ones we want to sync
  to the Zune"; also killed the orphan-files idea: no browser transcode cache).
  - Why not "play the converted video back from the Zune": the device copy is
    WMV 320×240 — browsers can't play WMV, and live-re-transcoding a 240p
    source is worse than the original. The file won't play in-browser because
    it's MKV + HEVC (206 stream is fine; HTML5 <video> can't demux MKV, and
    Chrome can't decode HEVC on macOS).
  - Reused the audio ☑ model for video: `sync_selection` is type-agnostic
    (media ids are globally unique); `GET /api/select` already returned
    `type`, so the UI splits: audio count text + Sync List tab filter out
    `type === "video"`; the DVD face disc rows got the same `.item-sync`
    checkbox, wired to the existing toggleSync.
  - New `sync_device_video()` in zune.py: mirrors checked movies onto the
    device's Video folder — WMV objects ("<stem>.wmv", same stem-key rule as
    audio) not checked get deleted, checked-but-missing get transcoded and
    pushed (sync_video replaces same-named objects, so no duplicates).
  - `/api/sync` takes `{"type": "video"}`; DVD-face ⇧ Zune now calls the
    mirror instead of push-all-currentVideos. Photos stay push-only.
  - Verified live: selected a movie → listed with type video
    alongside the audio row; unselected cleanly. Zune 30 attached and ready
    for the user's first real pick-to-sync.
- **Phase 12 — De-vendor the device layer; publish readiness: DONE**
  (user: reference the published zune-cli instead of vendoring it, and make
  the repo safe to publish).
  - The fixes developed after vendoring (mutagen easy-mode tag read for m4a,
    stem-normalized dedup in `sync_audio`, `sync_video`/`sync_photos` count
    returns) were ported back to the published zune-cli repo, which is now
    the SoT for the MTP/WMDRM transport.
  - `jukebox/zune_sync.py` (the vendored copy) deleted; `zune._zune_sync()`
    fetches zune-cli to `~/.jukebox/zune-cli` (clone on first push, pull on
    later pushes) and loads it as module `zune_sync` — lazy, as before.
  - Added the MIT LICENSE (matching zune-cli, with upstream credits), a
    .gitignore that can never let `*.db` / `.mtpz-data` / a cloned
    `zune-cli/` dir be committed, and sanitized the docs of identifying
    paths, library contents, and counts.

## Run
```
pip install -r requirements.txt
python run.py --root ~/Music --db ~/.jukebox/index.db --port 5000
# then open http://localhost:5000
```

## Notes / open items
- (Done) Albums view now groups by (album, artist) — same-named albums by
  different artists stay distinct; the UI shows the artist under each album and
  queries `/api/query?album=…&artist=…`.
- Next — media beyond audio (user-requested): see **PLAN.md** — three faces
  (Jukebox / Photo Album / DVD Player) over one `media` table + sync queue.
  zune-cli already has sync_photos / sync_video for the device side.
- "Surprise me" fetches all tracks client-side; add a `/api/random` endpoint if
  the library grows large.
- Test fixture: /tmp/jukebox-test (4 silent files, 2 artists/2 genres, mp3+m4a).
