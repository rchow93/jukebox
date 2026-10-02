# Jukebox

A local retro diner jukebox: scan the music folders on your laptop, index
them in SQLite, browse and stream them from a 2D Wurlitzer cabinet in the
browser, and sync a Zune over USB so it holds *exactly* the songs you chose.

## Run

```sh
pip install -r requirements.txt
python run.py --root ~/Music --db ~/.jukebox/index.db --port 5000
# open http://localhost:5000
```

`--root` is the main music folder. Extra folders added from the UI (below)
are remembered in the index and scanned at every startup. The scanner treats
the library as the union of those folders and drops index rows for files it
can no longer find — so always start with the same `--root` you built the
index with.

## The cabinet — three faces

Switch at the top: **🎵 Jukebox · 📸 Photos · 🎬 DVD**.

- **Photos**: album list → photo grid → full-screen slideshow (auto-advance,
  ←/→, Esc). ⇧ Zune pushes the open album (images re-encoded to exact-screen
  JPEGs).
- **DVD**: disc list → click to play in the overlay. Each disc has the same
  ☑ checkbox as the audio face: ⇧ Zune mirrors the checked movies onto the
  device (transcoded to WMV 320×240), and unchecking a movie takes it off
  the device on the next sync.

## Audio face (iTunes / Music style)

Browse with the **Artists · Albums · Genres · Folders · Playlists · Sync
List** tabs, or *Surprise me*. Clicking a group row starts playing it and
lists its tracks.

**The ☑ checkbox is the whole model.** Every track row has one, and checked =
"this song is on my Zune". The sync list is exactly the set of checked
songs, and the Zune *mirrors* it: **⇧ Zune** adds what's missing and deletes
what was unchecked. "Sync just two songs" = check exactly those two, then
hit ⇧ Zune.

Tools row at the top of the glass panel:

| Control | What it does |
|---|---|
| ➕ Add list to ▾ | every song in the current view → a playlist |
| ➕ Add checked to ▾ | only the ☑-checked songs in the current view → a playlist |
| New playlist / Delete | manage playlists; opening one plays it, ✕ removes a song (and drops it from the sync list) |
| ✓ Sync this list | check in the whole current view, then sync immediately |
| sync-count text | clickable — opens the Sync List tab |

**Sync List tab** shows precisely what the Zune contains; uncheck a row to
take a song off the device on the next sync.

### Folders tab — pick folders from the laptop

- **➕ Add a folder to the library…** — drill through the laptop's
  directories (the server lists them; a browser can't enumerate folders) and
  add any folder. It's scanned into the index immediately and registered for
  every startup. In "Library folders" below the list, ✕ removes one — its
  songs drop from the index, the files on disk are untouched.
- **✓ on a folder row** — check in every song in that folder at once and
  open it, so you can uncheck the ones you don't want; then ⇧ Zune pushes
  the rest.

## How sync works

`sync_selection` (the sync list) is the source of truth; the Zune is a
mirror of it. Device objects are matched by filename stem (the firmware
names MP3 objects from the file stem with dots stripped), so re-syncing never
duplicates, and album objects are rebuilt automatically. Audio and video
sync are mirrors of their respective checked sets (movie objects are matched
by the same stem rule, as `<stem>.wmv` in the device's Video folder); the
photo face keeps a push-only flow.

The device layer is the published
[zune-cli](https://github.com/rchow93/zune-cli) project: on the first push it
is fetched to `~/.jukebox/zune-cli` (git clone, then pulled on later pushes),
so fixes land upstream and the jukebox picks them up automatically. A push
needs `git`, `ffmpeg`, and the optional deps `pyusb` + `pycryptodome` — the
UI itself runs on just `mutagen` + `flask`.

## License

MIT — see [LICENSE](LICENSE). Not affiliated with or endorsed by Microsoft;
"Zune" is a Microsoft trademark.

The MTPZ handshake follows [kbhomes/libmtp-zune](https://github.com/kbhomes/libmtp-zune);
MTP/Zune protocol details were cross-checked against [Zune Explorer](https://github.com/NiceBeard/zune-explorer)
(MIT) — both via the [zune-cli](https://github.com/rchow93/zune-cli) project.
