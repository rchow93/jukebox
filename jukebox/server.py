"""Flask server: serves the jukebox UI, a small JSON API, and media streaming.

The UI is static files; the API is a thin wrapper over jukebox.core. Media is
streamed from the local files the index points at — served by id (not by path)
to avoid path traversal, with conditional/Range support so the browser can
seek. Audio, photo, and video all stream through the same handler.
"""
import os
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory

from .core import (
    add_folder, get_albums, get_artists, get_folders, get_genres, get_scan_folders,
    library_roots, query_tracks, remove_folder, scan, stats,
)
from .db import connect
from .zune import (get_selection, push_media_ids, sync_device,
                   sync_device_photo, sync_device_video)


def create_app(root, db):
    app = Flask(__name__)  # static_folder defaults to <package>/static
    app.config["ROOT"] = os.path.expanduser(root)
    app.config["DB"] = os.path.expanduser(db)

    @app.route("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.route("/api/stats")
    def api_stats():
        return jsonify(stats(app.config["DB"]))

    @app.route("/api/artists")
    def api_artists():
        return jsonify(get_artists(app.config["DB"]))

    @app.route("/api/albums")
    def api_albums():
        return jsonify(get_albums(
            app.config["DB"],
            artist=request.args.get("artist"),
            media_type=request.args.get("type", "audio"),
        ))

    @app.route("/api/genres")
    def api_genres():
        return jsonify(get_genres(app.config["DB"]))

    @app.route("/api/folders")
    def api_folders():
        return jsonify(get_folders(
            app.config["DB"], media_type=request.args.get("type", "audio")))

    @app.route("/api/query")
    def api_query():
        return jsonify(query_tracks(
            app.config["DB"],
            artist=request.args.get("artist"),
            album=request.args.get("album"),
            genre=request.args.get("genre"),
            folder=request.args.get("folder"),
            media_type=request.args.get("type", "audio"),
        ))

    @app.route("/api/export")
    def api_export():
        rows = get_selection(
            app.config["DB"],
            artist=request.args.get("artist"),
            album=request.args.get("album"),
            genre=request.args.get("genre"),
        )
        return jsonify({
            "count": len(rows),
            "paths": [r["path"] for r in rows],
            "tracks": [
                {"id": r["id"], "title": r["title"], "artist": r["artist"], "album": r["album"]}
                for r in rows
            ],
        })

    @app.route("/api/push", methods=["POST"])
    def api_push():
        body = request.get_json(silent=True) or {}
        media_type = body.get("type", "audio")
        ids = body.get("ids", [])
        if not ids:
            return jsonify({"error": "no ids provided"}), 400
        try:
            return jsonify(push_media_ids(app.config["DB"], ids, media_type))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except SystemExit as e:  # zune_sync's "No Zune device found"
            msg = e.args[0] if e.args else "no Zune device found"
            return jsonify({"error": str(msg)}), 503
        except Exception as e:  # device I/O (libusb claim, MTP errors, …)
            return jsonify({"error": f"device error: {e}"}), 502

    @app.route("/api/sync", methods=["POST"])
    def api_sync():
        """iTunes-style sync: reconcile the device to the sync list.

        `type` picks the face: "audio" (default) mirrors the music library,
        "video" mirrors the checked movies onto the device's Video folder,
        "photo" mirrors the checked photos onto the device's Pictures folder.
        """
        body = request.get_json(silent=True) or {}
        media_type = body.get("type", "audio")
        try:
            if media_type == "video":
                return jsonify(sync_device_video(app.config["DB"]))
            if media_type == "photo":
                return jsonify(sync_device_photo(app.config["DB"]))
            return jsonify(sync_device(app.config["DB"]))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except SystemExit as e:  # zune_sync's "No Zune device found"
            msg = e.args[0] if e.args else "no Zune device found"
            return jsonify({"error": str(msg)}), 503
        except Exception as e:  # device I/O (libusb claim, MTP errors, …)
            return jsonify({"error": f"device error: {e}"}), 502

    @app.route("/api/select", methods=["GET", "POST", "DELETE"])
    def api_select():
        """The sync queue (sync_selection): GET lists, POST adds, DELETE removes."""
        db = app.config["DB"]
        if request.method == "GET":
            conn = connect(db)
            rows = conn.execute(
                "SELECT m.id, m.type, m.path, m.title, m.artist, m.album"
                " FROM sync_selection s JOIN media m ON m.id = s.media_id"
                " ORDER BY m.type, m.album, m.title"
            ).fetchall()
            conn.close()
            return jsonify([dict(r) for r in rows])
        ids = (request.get_json(silent=True) or {}).get("ids", [])
        if not ids:
            return jsonify({"error": "no ids provided"}), 400
        conn = connect(db)
        if request.method == "POST":
            conn.executemany(
                "INSERT OR IGNORE INTO sync_selection (media_id) VALUES (?)",
                [(i,) for i in ids],
            )
        else:
            ph = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM sync_selection WHERE media_id IN ({ph})", ids)
        conn.commit()
        conn.close()
        return jsonify({"updated": len(ids)})

    @app.route("/api/scan", methods=["POST"])
    def api_scan():
        # rescan the whole library: primary root + every app-added folder
        return jsonify(scan(library_roots(app.config["DB"], app.config["ROOT"]),
                            app.config["DB"]))

    # ── library folders (app picks folders on this laptop, WMP-style) ───────
    @app.route("/api/dirs")
    def api_dirs():
        # folder picker: list a path's subdirectories on this machine
        p = Path(request.args.get("path", "")).expanduser() if request.args.get("path") \
            else Path.home()
        if not p.is_dir():
            return jsonify({"error": f"Not a folder: {p}"}), 400
        dirs = sorted((e.name for e in os.scandir(p) if e.is_dir()
                       and not e.name.startswith(".")), key=str.lower)
        return jsonify({"path": str(p), "parent": str(p.parent),
                        "dirs": dirs})

    @app.route("/api/library/folders", methods=["GET", "POST"])
    def api_library_folders():
        if request.method == "POST":
            d = request.get_json(silent=True) or {}
            if not d.get("path"):
                return jsonify({"error": "path required"}), 400
            return jsonify(add_folder(app.config["DB"], app.config["ROOT"], d["path"]))
        return jsonify(get_scan_folders(app.config["DB"]))

    @app.route("/api/library/folders/<int:fid>", methods=["DELETE"])
    def api_library_folder_del(fid):
        return jsonify(remove_folder(app.config["DB"], app.config["ROOT"], fid))

    # ── playlists (iTunes/WMP-style user sources) ───────────────────────────
    @app.route("/api/playlists", methods=["GET", "POST"])
    def api_playlists():
        db = app.config["DB"]
        if request.method == "POST":
            name = (request.get_json(silent=True) or {}).get("name", "").strip()
            if not name:
                return jsonify({"error": "no name provided"}), 400
            conn = connect(db)
            try:
                cur = conn.execute(
                    "INSERT INTO playlists (name) VALUES (?)", (name,))
                conn.commit()
                return jsonify({"id": cur.lastrowid, "name": name})
            except Exception:
                return jsonify({"error": f"playlist '{name}' already exists"}), 400
            finally:
                conn.close()
        conn = connect(db)
        rows = conn.execute(
            "SELECT p.id, p.name, COUNT(i.media_id) AS n FROM playlists p"
            " LEFT JOIN playlist_items i ON i.playlist_id = p.id"
            " GROUP BY p.id ORDER BY p.name"
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    @app.route("/api/playlists/<int:pid>", methods=["GET", "POST", "DELETE"])
    def api_playlist(pid):
        db = app.config["DB"]
        conn = connect(db)
        if request.method == "GET":
            rows = conn.execute(
                "SELECT m.* FROM playlist_items i JOIN media m ON m.id = i.media_id"
                " WHERE i.playlist_id = ? ORDER BY i.media_id", (pid,)).fetchall()
            conn.close()
            return jsonify([dict(r) for r in rows])
        if request.method == "DELETE":
            conn.execute("DELETE FROM playlists WHERE id = ?", (pid,))
            conn.commit()
            conn.close()
            return jsonify({"deleted": pid})
        ids = (request.get_json(silent=True) or {}).get("ids", [])
        conn.executemany(
            "INSERT OR IGNORE INTO playlist_items (playlist_id, media_id) VALUES (?, ?)",
            [(pid, i) for i in ids],
        )
        conn.commit()
        conn.close()
        return jsonify({"updated": len(ids)})

    @app.route("/api/playlists/<int:pid>/tracks", methods=["DELETE"])
    def api_playlist_remove(pid):
        """Remove tracks (by media id) from a playlist."""
        db = app.config["DB"]
        ids = (request.get_json(silent=True) or {}).get("ids", [])
        conn = connect(db)
        conn.executemany(
            "DELETE FROM playlist_items WHERE playlist_id = ? AND media_id = ?",
            [(pid, i) for i in ids],
        )
        conn.commit()
        conn.close()
        return jsonify({"removed": len(ids)})

    def _stream(media_type, media_id):
        """Serve the file a media row points at (by id, never by path)."""
        conn = connect(app.config["DB"])
        row = conn.execute(
            "SELECT path FROM media WHERE type = ? AND id = ?",
            (media_type, media_id),
        ).fetchone()
        conn.close()
        if not row or not os.path.exists(row["path"]):
            return "not found", 404
        return send_file(row["path"], conditional=True)

    @app.route("/audio/<int:media_id>")
    def audio(media_id):
        return _stream("audio", media_id)

    @app.route("/photo/<int:media_id>")
    def photo(media_id):
        return _stream("photo", media_id)

    @app.route("/video/<int:media_id>")
    def video(media_id):
        return _stream("video", media_id)

    return app
