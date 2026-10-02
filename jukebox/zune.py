"""zune seam — the shared-index → Zune handoff.

The SQLite index (jukebox.db) is the source of truth for *what to push*.
zune-cli's `push --audio PATH...` consumes a flat list of file paths and
re-reads tags from disk, so this module's job is: given a browse filter,
return the ordered source files the device sync should copy.

Two paths exist: the pure-selection helpers above (no device needed), and
push_paths/push_photos/push_videos/push_track_ids which drive a real USB MTP
push through the published zune-cli device layer (auto-fetched from
https://github.com/rchow93/zune-cli, lazy-imported, so the UI never needs
pyusb/pycryptodome). A push
also creates an Abstract Album object per album — the mechanism the Zune reads
for album grouping (the per-track album string prop is rejected by firmware).
"""
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

from .core import query_tracks

ZUNE_CLI_REPO = "https://github.com/rchow93/zune-cli.git"
ZUNE_CLI_DIR = Path.home() / ".jukebox" / "zune-cli"


def _zune_sync():
    """The device layer: the published zune-cli repo (SoT for the MTP/WMDRM transport).

    Cloned to ~/.jukebox/zune-cli on first use (git pull thereafter, re-clone
    if the checkout is stale), then loaded as module ``zune_sync``. Only a real
    device push goes through here — the UI/export paths never need pyusb.
    """
    mod = sys.modules.get("zune_sync")
    if mod is not None:
        return mod
    script = ZUNE_CLI_DIR / "zune-cli.py"
    if not script.is_file():
        if ZUNE_CLI_DIR.exists():
            try:
                subprocess.run(["git", "pull", "--ff-only"], cwd=ZUNE_CLI_DIR, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except subprocess.CalledProcessError:
                shutil.rmtree(ZUNE_CLI_DIR)
        if not script.is_file():
            ZUNE_CLI_DIR.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "--depth", "1", ZUNE_CLI_REPO, str(ZUNE_CLI_DIR)],
                           check=True)
    spec = importlib.util.spec_from_file_location("zune_sync", script)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["zune_sync"] = mod
    spec.loader.exec_module(mod)
    return mod


def get_selection(db_path, artist=None, album=None, genre=None, limit=None):
    """Index rows a Zune push would copy, in playback order (album/track)."""
    return query_tracks(db_path, artist=artist, album=album, genre=genre, limit=limit)


def selection_paths(db_path, **filters):
    """Just the file paths — the list to hand to zune-cli `push --audio`."""
    return [r["path"] for r in get_selection(db_path, **filters)]


def push_command(db_path, zune_cli="zune-cli.py", **filters):
    """The exact argv that would push this selection to the Zune.

    In the USB step this becomes a direct call into the ported sync_audio();
    today it documents and drives the handoff via the existing zune-cli.
    """
    paths = selection_paths(db_path, **filters)
    return [sys.executable, zune_cli, "push", "--audio", *paths]


def _audio_paths(paths):
    return [p for p in paths if p.lower().endswith((".mp3", ".wav", ".m4a", ".flac"))]


def _album_groups(zs, audio_paths):
    """Group audio paths by on-disk album tag -> ordered list of (album, [index])."""
    from collections import OrderedDict
    groups = OrderedDict()
    for i, p in enumerate(audio_paths):
        album = zs._track_tags(p).get("album")
        if album:
            groups.setdefault(album, []).append(i)
    return list(groups.items())


def _upsert_abstract_album(zs, client, album, handles):
    """Create (or refresh) the Abstract Album object the Zune uses to group tracks.

    The Zune rejects the per-track 0xDC9A album string prop (returns 0x200f); it
    models albums as a 0xBA03 object at the device root, linked to the tracks via
    ObjectReferences. Remove any stale same-named object first so repeated pushes
    of the same album don't accumulate duplicates.
    """
    try:
        for h in client.enumerate_objects(zs.FMT["AbstractAlbum"], client.root_handle):
            try:
                if client.get_obj_info(h).get("filename") == album:
                    client.delete(h)
            except Exception:
                pass  # some existing objects can't be read — leave them alone
        zs.create_abstract_album(album, handles, client)
        return True
    except Exception as e:
        print(f"  (could not create abstract album {album!r}: {e})")
        return False


def _sync_folder_playlists(zs, client, ffmpeg, rows, db_path, on_device, dev_key):
    """Mirror the app-added folders onto the device's Playlists folder.

    Each folder with checked-in tracks gets (re)created as a .pla named after
    the folder — the same shape zune-cli's folder sync produces. A folder whose
    tracks were all unchecked loses its playlist. Folders inside the primary
    root don't get playlists (they're not app-added folders).
    """
    from pathlib import Path as P
    from .core import get_scan_folders
    folder_names = {P(p["path"]).name for p in get_scan_folders(db_path)}
    by_folder = {}
    for r in rows:
        f = r["folder"]
        if f not in folder_names:
            continue
        hs = on_device.get(dev_key(P(r["path"]).stem))
        if hs:
            by_folder.setdefault(f, []).append(hs[0])
    created = 0
    for f, hs in by_folder.items():
        try:
            zs.sync_playlist(f, [], client, ffmpeg, track_handles=hs)
            created += 1
        except Exception as e:  # one odd folder name shouldn't kill the rest
            print(f"  (could not create playlist {f!r}: {e})")
    # a folder's tracks all unchecked -> its playlist goes away too
    pl_dir = None
    for h in client.enumerate_objects(zs.FMT["Assoc"], client.root_handle):
        try:
            if client.get_obj_info(h)["filename"] == "Playlists":
                pl_dir = h
                break
        except Exception:
            pass
    if pl_dir:
        for h in client.enumerate_objects(zs.FMT["Playlist"], pl_dir):
            try:
                name = client.get_obj_info(h)["filename"]
            except Exception:
                continue
            stem = name[:-4] if name.lower().endswith(".pla") else name
            if stem in folder_names and stem not in by_folder:
                try:
                    client.delete(h)
                except Exception:
                    pass
    return created


def push_paths(db_path, paths):
    """Push an explicit list of file paths to the attached Zune over USB MTP.

    Also creates an Abstract Album object per album so the tracks group by album
    on the device. Lazy-imports the zune-cli module so the UI/export path never requires
    pyusb or pycryptodome — only a real device push does.
    """
    zs = _zune_sync()
    device, model = zs.find_device()
    client = zs.connect(device)
    try:
        ffmpeg = zs.find_ffmpeg()
        handles = zs.sync_audio(list(paths), client, ffmpeg)
        audio_paths = _audio_paths(list(paths))
        albums = 0
        for album, idxs in _album_groups(zs, audio_paths):
            if _upsert_abstract_album(zs, client, album, [handles[i] for i in idxs]):
                albums += 1
        return {"model": model, "requested": len(paths),
                "pushed": len(handles), "albums": albums}
    finally:
        client.close()


def push_selection(db_path, **filters):
    """Push the tracks matching a browse filter (artist/album/genre)."""
    tracks = get_selection(db_path, **filters)
    out = push_paths(db_path, [t["path"] for t in tracks])
    out["tracks"] = [t["title"] for t in tracks]
    return out


def push_track_ids(db_path, ids):
    """Push specific tracks (by index id) — the 'Push to Zune' button path."""
    return push_media_ids(db_path, ids, "audio")


def sync_device(db_path):
    """iTunes-style sync: make the device's music MIRROR the sync list
    (the sync_selection table), not just push into it.

    - in the list, missing on the device  -> push (audio convert + album objs)
    - on the device, no longer in the list -> delete
    Matching is by the normalized file stem (lowercase, dots stripped) — the
    key the firmware uses to name each MP3 object "<stem>.mp3" (verified: the
    title tag is NOT what names the object). Video gets the same model in
    sync_device_video; photos keep the push-only path.
    """
    zs = _zune_sync()
    from .db import connect
    from pathlib import Path

    def _dev_key(name):
        # The Zune strips dots from object names ("01. Song" -> "01 Song"), and
        # sync_audio creates objects from the file stem — so the stem, dots
        # stripped, is the stable match key (the title tag is NOT what the
        # firmware names the object).
        return name.lower().replace(".", "")

    conn = connect(db_path)
    rows = conn.execute(
        "SELECT m.path, m.album, m.folder FROM sync_selection s"
        " JOIN media m ON m.id = s.media_id WHERE m.type = 'audio'"
    ).fetchall()
    conn.close()
    wanted = {_dev_key(Path(r["path"]).stem) for r in rows}

    device, model = zs.find_device()
    client = zs.connect(device)
    try:
        ffmpeg = zs.find_ffmpeg()
        # device snapshot: normalized stem -> [handles]
        on_device = {}
        for h in client.enumerate_objects(zs.FMT["MP3"], client.root_handle):
            try:
                info = client.get_obj_info(h)
            except Exception:
                continue  # unreadable object — leave it alone
            fname = info["filename"]
            stem = fname[:-4] if fname.lower().endswith(".mp3") else fname
            on_device.setdefault(_dev_key(stem), []).append(h)
        removed = 0
        for key, handles in on_device.items():
            # strays (not in the list) go; stale duplicates collapse to one
            if key not in wanted:
                doomed = handles
            elif len(handles) > 1:
                doomed = handles[1:]
            else:
                doomed = []
            for h in doomed:
                client.delete(h)
                removed += 1
        missing = [r["path"] for r in rows if _dev_key(Path(r["path"]).stem) not in on_device]
        added = 0
        if missing:
            handles = zs.sync_audio(missing, client, ffmpeg)
            added = len(handles)
            # record the pushed handles so the album pass can reference them
            for path, h in zip(missing, handles):
                on_device.setdefault(_dev_key(Path(path).stem), []).append(h)
        # rebuild any abstract album objects missing for the sync list
        # (idempotent; no-op syncs that only re-check leave existing ones alone)
        existing_albums = set()
        for h in client.enumerate_objects(zs.FMT["AbstractAlbum"], client.root_handle):
            try:
                existing_albums.add(client.get_obj_info(h).get("filename"))
            except Exception:
                pass  # unreadable object — leave it alone
        by_album = {}
        for r in rows:
            hs = on_device.get(_dev_key(Path(r["path"]).stem))
            # "Unknown Album" is the tag-less placeholder (YouTube downloads) —
            # those tracks group as folder playlists, not one giant album
            if not hs or not r["album"] or r["album"] == "Unknown Album":
                continue
            by_album.setdefault(r["album"], []).append(hs[0])
        for album, hs in by_album.items():
            if album not in existing_albums:
                _upsert_abstract_album(zs, client, album, hs)
        playlists = _sync_folder_playlists(zs, client, ffmpeg, rows, db_path,
                                           on_device, _dev_key)
        return {"model": model, "selected": len(rows),
                "added": added, "removed": removed, "playlists": playlists}
    finally:
        client.close()


def sync_device_video(db_path):
    """Mirror the sync list's movies onto the device's Video folder.

    Same model as sync_device, for video: WMV objects ("<stem>.wmv") on the
    device that are no longer checked get deleted; checked-but-missing ones
    are transcoded and pushed. sync_video replaces any same-named object it
    finds, so re-syncing never duplicates.
    """
    zs = _zune_sync()
    from .db import connect
    from pathlib import Path

    def _dev_key(name):
        return name.lower().replace(".", "")

    conn = connect(db_path)
    rows = conn.execute(
        "SELECT m.path FROM sync_selection s"
        " JOIN media m ON m.id = s.media_id WHERE m.type = 'video'"
    ).fetchall()
    conn.close()
    wanted = {_dev_key(Path(r["path"]).stem) for r in rows}

    device, model = zs.find_device()
    client = zs.connect(device)
    try:
        ffmpeg = zs.find_ffmpeg()
        on_device = {}
        for h in client.enumerate_objects(zs.FMT["WMV"], client.root_handle):
            try:
                fname = client.get_obj_info(h)["filename"]
            except Exception:
                continue  # unreadable object — leave it alone
            stem = fname[:-4] if fname.lower().endswith(".wmv") else fname
            on_device.setdefault(_dev_key(stem), []).append(h)
        removed = 0
        for key, handles in on_device.items():
            if key not in wanted:
                for h in handles:
                    client.delete(h)
                    removed += 1
        missing = [r["path"] for r in rows if _dev_key(Path(r["path"]).stem) not in on_device]
        added = 0
        if missing:
            result = zs.sync_video(missing, {}, client, ffmpeg) or {}
            added = result.get("pushed", 0)
        return {"model": model, "selected": len(rows),
                "added": added, "removed": removed}
    finally:
        client.close()


def sync_device_photo(db_path):
    """Mirror the sync list's photos onto the device's Pictures folder.

    Same model, for photos: JPEG objects ("<stem>.jpg") on the device that
    are no longer checked get deleted; checked-but-missing ones are
    re-encoded and pushed. sync_photos skips same-named objects it finds, so
    re-syncing never duplicates.
    """
    zs = _zune_sync()
    from .db import connect
    from pathlib import Path

    def _dev_key(name):
        return name.lower().replace(".", "")

    conn = connect(db_path)
    rows = conn.execute(
        "SELECT m.path FROM sync_selection s"
        " JOIN media m ON m.id = s.media_id WHERE m.type = 'photo'"
    ).fetchall()
    conn.close()
    wanted = {_dev_key(Path(r["path"]).stem) for r in rows}

    device, model = zs.find_device()
    client = zs.connect(device)
    try:
        pics_dir = zs._root_folder(client, "Pictures")
        on_device = {}
        handles = client.enumerate_objects(zs.FMT["JPEG"], pics_dir)
        handles += client.enumerate_objects(zs.FMT["Assoc"], pics_dir)
        for h in handles:
            try:
                fname = client.get_obj_info(h)["filename"]
            except Exception:
                continue  # unreadable object — leave it alone
            stem = fname[:-4] if fname.lower().endswith(".jpg") else fname
            on_device.setdefault(_dev_key(stem), []).append(h)
        removed = 0
        for key, hs in on_device.items():
            if key not in wanted:
                for h in hs:
                    client.delete(h)
                    removed += 1
        missing = [r["path"] for r in rows if _dev_key(Path(r["path"]).stem) not in on_device]
        added = 0
        if missing:
            result = zs.sync_photos(missing, client) or {}
            added = result.get("pushed", 0)
        return {"model": model, "selected": len(rows),
                "added": added, "removed": removed}
    finally:
        client.close()


def push_photos(db_path, paths):
    """Push photos to the attached Zune (device slideshow, Pictures folder).

    Lazy-imports the zune-cli module like push_paths — only a real device push needs
    pyusb/pycryptodome.
    """
    zs = _zune_sync()
    device, model = zs.find_device()
    client = zs.connect(device)
    try:
        result = zs.sync_photos(list(paths), client) or {"pushed": 0, "total": len(paths)}
        return {"model": model, "requested": len(paths),
                "pushed": result.get("pushed", 0)}
    finally:
        client.close()


def push_videos(db_path, paths):
    """Push videos to the attached Zune (Video folder, transcoded to WMV 320x240).

    The device only plays its own WMV baseline, so ffmpeg converts each file at
    push time; subtitles aren't in the index yet, so none are paired.
    Lazy-imports the zune-cli module like push_paths — only a real device push needs
    pyusb/pycryptodome.
    """
    zs = _zune_sync()
    device, model = zs.find_device()
    client = zs.connect(device)
    try:
        ffmpeg = zs.find_ffmpeg()
        result = zs.sync_video(list(paths), {}, client, ffmpeg) or {"pushed": 0, "total": len(paths)}
        return {"model": model, "requested": len(paths),
                "pushed": result.get("pushed", 0)}
    finally:
        client.close()


def push_media_ids(db_path, ids, media_type="audio"):
    """Push rows (by index id) of the given type — each face's 'Sync to Zune'."""
    from .db import connect
    conn = connect(db_path)
    ph = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT path FROM media WHERE type = ? AND id IN ({ph})", [media_type, *ids]
    ).fetchall()
    conn.close()
    paths = [r["path"] for r in rows]
    if media_type == "audio":
        return push_paths(db_path, paths)
    if media_type == "photo":
        return push_photos(db_path, paths)
    if media_type == "video":
        return push_videos(db_path, paths)
    raise ValueError(f"unsupported media type for push: {media_type!r}")
