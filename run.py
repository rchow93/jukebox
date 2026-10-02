#!/usr/bin/env python3
"""Entry point:  python run.py [--root DIR] [--db PATH] [--port N] [--no-scan]"""
import argparse

from jukebox.core import library_roots, scan
from jukebox.server import create_app


def main():
    ap = argparse.ArgumentParser(description="jukebox server")
    ap.add_argument("--root", default="~/Music", help="music folder to index")
    ap.add_argument("--db", default="~/.jukebox/index.db", help="index path")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--no-scan", action="store_true", help="skip the startup scan")
    args = ap.parse_args()

    if not args.no_scan:
        roots = library_roots(args.db, args.root)
        print(f"Scanning {len(roots)} folder(s) ...")
        print(scan(roots, args.db))

    app = create_app(args.root, args.db)
    print(f"Jukebox running at http://localhost:{args.port}")
    app.run(host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
