"""The jukebox engine: scan a folder, index it into SQLite, query it.

This module knows nothing about the browser or the UI. It's the reusable core
that the Flask server talks to — and (later) zune-cli can import to share the
same index as its source of truth.
"""
import argparse
import os
from pathlib import Path

from mutagen import File as MutagenFile

from .db import connect

AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".flac"}
VIDEO_EXTS = {".mp4", ".m4v", ".mkv", ".mov", ".avi", ".webm"}
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".bmp"}


def _type_for(path):
    """'audio' | 'photo' | 'video' for an indexed extension, else None."""
    ext = path.suffix.lower()
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in PHOTO_EXTS:
        return "photo"
    return None


# ── tag reading ──────────────────────────────────────────────────────────────

def _first(val):
    """mutagen returns lists for multi-value tags; take the first, stripped."""
    if val is None:
        return None
    if isinstance(val, (list, tuple)):
        val = val[0] if val else None
        if isinstance(val, (list, tuple)):  # MP4 trkn/disk: [(n, total)]
            val = val[0] if val else None
    if val is None:
        return None
    s = str(val).strip()
    return s or None


def _to_int(val):
    if val is None:
        return None
    s = str(val).strip().split("/")[0].strip()  # "3/12" -> "3"
    try:
        return int(s)
    except ValueError:
        return None


def _read_tags(path):
    """Read metadata + cover-art presence from an mp3/m4a via mutagen."""
    audio = MutagenFile(str(path))
    if audio is None:
        return None
    tags = audio.tags
    title = artist = album = genre = None
    track_number = disc_number = year = None
    has_cover = 0

    if tags is not None:
        if path.suffix.lower() == ".m4a":  # MP4
            title = _first(tags.get("\xa9nam"))
            artist = _first(tags.get("\xa9ART"))
            album = _first(tags.get("\xa9alb"))
            genre = _first(tags.get("\xa9gen"))
            track_number = _first(tags.get("trkn"))
            disc_number = _first(tags.get("disk"))
            year = _first(tags.get("\xa9day")) or _first(tags.get("trkd"))
            has_cover = 1 if tags.get("covr") else 0
        else:  # ID3
            title = _first(tags.get("TIT2"))
            artist = _first(tags.get("TPE1"))
            album = _first(tags.get("TALB"))
            genre = _first(tags.get("TCON"))
            track_number = _first(tags.get("TRCK"))
            disc_number = _first(tags.get("TPOS"))
            year = _first(tags.get("TYER")) or _first(tags.get("TDRC"))
            has_cover = 1 if (tags.get("APIC") or tags.get("PIC")) else 0

    if not title:
        title = path.stem
    return {
        "title": title,
        "artist": artist or "Unknown Artist",
        "album": album or "Unknown Album",
        "genre": genre,
        "track_number": _to_int(track_number),
        "disc_number": _to_int(disc_number),
        "year": _to_int(year),
        "duration": audio.info.length if audio.info else None,
        "has_cover": has_cover,
    }


def _read_video_meta(path):
    """Video: title/year/duration via mutagen where the container has tags,
    else fall back to the filename. The parent folder is the 'album'."""
    meta = {"title": path.stem, "artist": None, "album": path.parent.name,
            "genre": None, "track_number": None, "disc_number": None,
            "year": None, "duration": None, "has_cover": 0}
    try:
        f = MutagenFile(str(path), easy=True)
    except Exception:
        f = None
    if f is not None:
        def g(key):
            vals = f.get(key)
            return str(vals[0]).strip() if vals else None
        if (t := g("title")):
            meta["title"] = t
        if (a := g("artist")):
            meta["artist"] = a
        if (y := g("date")):
            meta["year"] = _to_int(y[:4])
        info = getattr(f, "info", None)
        if info is not None and getattr(info, "length", None):
            meta["duration"] = info.length
    return meta


def _read_photo_meta(path):
    """Photo: the folder is the album, the filename is the title. No deps."""
    return {"title": path.stem, "artist": None, "album": path.parent.name,
            "genre": None, "track_number": None, "disc_number": None,
            "year": None, "duration": None, "has_cover": 0}


def _read_meta(path, mtype):
    """Metadata for an indexed file, dispatched by type."""
    if mtype == "audio":
        return _read_tags(path)
    if mtype == "video":
        return _read_video_meta(path)
    return _read_photo_meta(path)


# ── scan / index ─────────────────────────────────────────────────────────────

def scan(roots, db_path):
    """Scan the library (one or more roots) and (re)index into SQLite.

    `roots` is the full library: the primary root first, then any extra
    folders the app added (see add_folder). Incremental: tags are only
    re-read for files that are new or whose mtime changed, so re-scanning a
    large library on startup stays cheap. All writes land in one transaction
    (commit at the end), so the index is always a consistent snapshot; the DB
    is a rebuildable cache, so a corrupt file is healed by deleting it and
    re-scanning.

    Extra roots get a `folder` prefix of their own name, so a folder the app
    added shows up as its own group in the Folders view. Files that no longer
    exist under *any* root are dropped.
    """
    if isinstance(roots, (str, Path)):
        roots = [roots]
    roots = [Path(r).expanduser() for r in roots]
    conn = connect(db_path)
    existing = {
        r["path"]: (r["id"], r["file_mtime"], r["folder"])
        for r in conn.execute("SELECT id, path, file_mtime, folder FROM media")
    }
    seen = set()
    added = updated = 0

    for i, root in enumerate(roots):
        prefix = "" if i == 0 else root.name + "/"
        for dirpath, _dirs, filenames in os.walk(root):
            for fn in filenames:
                p = Path(dirpath) / fn
                mtype = _type_for(p)
                if mtype is None:
                    continue
                resolved = str(p.resolve())
                seen.add(resolved)
                mtime = p.stat().st_mtime
                folder = os.path.relpath(p.parent, root)
                if folder == ".":
                    folder = prefix.rstrip("/")   # "Extra", not "Extra/"
                else:
                    folder = prefix + folder

                if resolved in existing:
                    _id, old_mtime, old_folder = existing[resolved]
                    if abs((old_mtime or 0) - mtime) <= 1.0:
                        if old_folder != folder:
                            # folder column was added later / row predates it — heal
                            # without a tag read (covers renames to the same path)
                            conn.execute("UPDATE media SET folder=? WHERE id=?", (folder, _id))
                        continue  # unchanged — skip the (slow) tag read
                    meta = _read_meta(p, mtype)
                    if meta is None:
                        continue
                    conn.execute(
                        "UPDATE media SET title=?, artist=?, album=?, genre=?, folder=?,"
                        " track_number=?, disc_number=?, year=?, duration=?,"
                        " has_cover=?, file_mtime=? WHERE id=?",
                        (meta["title"], meta["artist"], meta["album"], meta["genre"], folder,
                         meta["track_number"], meta["disc_number"], meta["year"],
                         meta["duration"], meta["has_cover"], mtime, _id),
                    )
                    updated += 1
                else:
                    meta = _read_meta(p, mtype)
                    if meta is None:
                        continue
                    conn.execute(
                        "INSERT INTO media (type, path, title, artist, album, genre, folder,"
                        " track_number, disc_number, year, duration, has_cover, file_mtime)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (mtype, resolved, meta["title"], meta["artist"], meta["album"],
                         meta["genre"], folder,
                         meta["track_number"], meta["disc_number"],
                         meta["year"], meta["duration"], meta["has_cover"], mtime),
                    )
                    added += 1

    # drop files that no longer exist on disk
    removed = 0
    for path in existing:
        if path not in seen:
            conn.execute("DELETE FROM media WHERE path = ?", (path,))
            removed += 1

    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    conn.close()
    return {"roots": [str(r) for r in roots], "added": added,
            "updated": updated, "removed": removed, "total": total}


# ── library folders (Windows Media Player-style: the app adds folders) ───────

def get_scan_folders(db_path):
    conn = connect(db_path)
    rows = conn.execute("SELECT id, path FROM scan_folders ORDER BY path").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def library_roots(db_path, primary):
    """The full library: the primary root first, then app-added folders.

    The primary is always in the set, so a scan can never orphan its own rows
    even if no extra folders are registered.
    """
    primary = str(Path(primary).expanduser().resolve())
    roots = [primary]
    for r in get_scan_folders(db_path):
        p = str(Path(r["path"]).resolve())
        if p not in roots:
            roots.append(p)
    return roots


def add_folder(db_path, primary, folder):
    """Register `folder` in the library, then scan the library incrementally.

    Returns the scan stats, or {"error": ...} if the path isn't a usable
    extra folder (missing, or already inside the primary root).
    """
    p = Path(folder).expanduser().resolve()
    prim = Path(primary).expanduser().resolve()
    if not p.is_dir():
        return {"error": f"Not a folder: {p}"}
    if p == prim:
        return {"error": "That's the main root — it's already in the library."}
    try:
        p.relative_to(prim)
        return {"error": "That folder is already inside the main root."}
    except ValueError:
        pass
    conn = connect(db_path)
    conn.execute("INSERT OR IGNORE INTO scan_folders (path) VALUES (?)", (str(p),))
    conn.commit()
    conn.close()
    return scan(library_roots(db_path, primary), db_path)


def remove_folder(db_path, primary, folder_id):
    """Unregister a folder; its media rows drop on the rescan that follows."""
    conn = connect(db_path)
    conn.execute("DELETE FROM scan_folders WHERE id = ?", (folder_id,))
    conn.commit()
    conn.close()
    return scan(library_roots(db_path, primary), db_path)


# ── queries ──────────────────────────────────────────────────────────────────

def query_tracks(db_path, artist=None, album=None, genre=None, folder=None,
                 limit=None, media_type="audio"):
    """Rows matching the filter, in playback order (album/track).

    media_type selects the face: 'audio' (default, jukebox) or 'video' (DVD).
    """
    conn = connect(db_path)
    sql = "SELECT * FROM media WHERE type = ?"
    args = [media_type]
    if artist:
        sql += " AND artist = ?"; args.append(artist)
    if album:
        sql += " AND album = ?"; args.append(album)
    if genre:
        sql += " AND genre = ?"; args.append(genre)
    if folder is not None:
        sql += " AND folder = ?"; args.append(folder)
    sql += " ORDER BY album, disc_number, track_number, title"
    if limit:
        sql += " LIMIT ?"; args.append(limit)
    rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    conn.close()
    return rows


def _distinct(db_path, column, media_type="audio", extra_where=None, extra_args=None):
    conn = connect(db_path)
    sql = f"SELECT {column} AS name, COUNT(*) AS n FROM media"
    where = ["type = ?", f"{column} IS NOT NULL AND {column} != ''"]
    args = [media_type]
    if extra_where:
        where.append(extra_where)
        args.extend(extra_args or [])
    sql += " WHERE " + " AND ".join(where)
    sql += f" GROUP BY {column} ORDER BY {column}"
    rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    conn.close()
    return rows


def get_artists(db_path, media_type="audio"):
    return _distinct(db_path, "artist", media_type)


def get_folders(db_path, media_type="audio"):
    """Top-level folders holding media, iTunes-Files-style. Files sitting
    directly in the scan root are grouped as '(root)'."""
    conn = connect(db_path)
    rows = conn.execute(
        "SELECT COALESCE(NULLIF(folder, ''), '(root)') AS name, COUNT(*) AS n"
        " FROM media WHERE type = ? GROUP BY folder ORDER BY name",
        (media_type,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_albums(db_path, artist=None, media_type="audio"):
    # Group by (album, artist) so same-named albums by different artists stay distinct.
    # For photos, the parent folder *is* the album, so this lists the photo albums.
    conn = connect(db_path)
    sql = "SELECT album AS name, artist, COUNT(*) AS n FROM media"
    where = ["type = ?", "album IS NOT NULL", "album != ''"]
    args = [media_type]
    if artist:
        where.append("artist = ?"); args.append(artist)
    sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY album, artist ORDER BY artist, album"
    rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    conn.close()
    return rows


def get_genres(db_path, media_type="audio"):
    return _distinct(db_path, "genre", media_type)


def stats(db_path):
    """Audio (jukebox face) stats — the shape the UI stats bar expects."""
    conn = connect(db_path)
    row = conn.execute(
        "SELECT COUNT(*) tracks, COUNT(DISTINCT artist) artists,"
        " COUNT(DISTINCT album) albums, COUNT(DISTINCT genre) genres"
        " FROM media WHERE type = 'audio'"
    ).fetchone()
    conn.close()
    return dict(row)


def stats_all(db_path):
    """Per-type row counts — {'audio': n, 'photo': n, 'video': n}."""
    conn = connect(db_path)
    rows = conn.execute(
        "SELECT type, COUNT(*) n FROM media GROUP BY type"
    ).fetchall()
    conn.close()
    out = {"audio": 0, "photo": 0, "video": 0}
    for r in rows:
        out[r["type"]] = r["n"]
    return out


# ── CLI (headless engine interface) ──────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="jukebox core engine")
    ap.add_argument("command",
                    choices=["scan", "artists", "albums", "genres", "stats", "query"])
    ap.add_argument("--root", default="~/Music", help="folder to scan")
    ap.add_argument("--db", default="~/.jukebox/index.db", help="index path")
    ap.add_argument("--artist")
    ap.add_argument("--album")
    ap.add_argument("--genre")
    args = ap.parse_args()
    db = str(Path(args.db).expanduser())

    if args.command == "scan":
        print(scan(args.root, db))
    elif args.command == "artists":
        for r in get_artists(db):
            print(f"{r['n']:>4}  {r['name']}")
    elif args.command == "albums":
        for r in get_albums(db, artist=args.artist):
            print(f"{r['n']:>4}  {r['name']}")
    elif args.command == "genres":
        for r in get_genres(db):
            print(f"{r['n']:>4}  {r['name']}")
    elif args.command == "stats":
        print(stats(db))
    elif args.command == "query":
        for t in query_tracks(db, artist=args.artist, album=args.album,
                              genre=args.genre):
            print(f"{t['artist']} — {t['album']} — {t['title']}")


if __name__ == "__main__":
    main()
