const audio = document.getElementById("audio");
const npTitle = document.getElementById("np-title");
const npArtist = document.getElementById("np-artist");
const playBtn = document.getElementById("playpause");

let queue = [];
let pos = 0;
let syncSet = new Set();   // media ids in the sync list (the Zune mirrors this)
let syncAudioCount = 0;    // of those, the songs — what the count text shows

async function getJSON(url) {
  const r = await fetch(url);
  return r.json();
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function renderList(items, label, onClick, rowExtra, after) {
  const list = document.getElementById("list");
  list.innerHTML = "";
  list.appendChild(el("h2", "list-title", label));
  if (!items.length) {
    list.appendChild(el("p", "empty", "Nothing here yet."));
    return;
  }
  items.forEach(item => {
    const row = el("div", "item");
    const name = el("span", "item-name", item.name);
    if (item.artist) {
      name.classList.add("has-sub");
      name.appendChild(el("span", "item-sub", item.artist));
    }
    row.appendChild(name);
    row.appendChild(el("span", "item-count", String(item.n)));
    row.onclick = () => onClick(item);
    if (rowExtra) rowExtra(item, row);
    list.appendChild(row);
  });
  if (after) after(list);
}

function renderTracks(tracks, opts = {}) {
  const list = document.getElementById("list");
  list.innerHTML = "";
  if (opts.back) {
    const back = el("div", "item", opts.back[0]);
    back.onclick = opts.back[1];
    list.appendChild(back);
  }
  list.appendChild(el("h2", "list-title", opts.title || "Tracks"));
  tracks.forEach((t, i) => {
    const row = el("div", "item track");
    if (opts.syncable) {
      const cb = el("span", "item-sync");
      cb.dataset.id = t.id;
      cb.textContent = syncSet.has(t.id) ? "☑" : "☐";
      cb.title = "Sync to Zune";
      cb.onclick = e => {
        e.stopPropagation();
        toggleSync(t.id, !syncSet.has(t.id));
        if (opts.syncList) showSyncList();   // unchecking drops the row
      };
      row.appendChild(cb);
    }
    row.appendChild(el("span", "item-name", t.title || "(untitled)"));
    row.appendChild(el("span", "item-count", t.artist || ""));
    if (opts.playlistId) {
      const x = el("span", "item-x", "✕");
      x.title = "Remove from playlist";
      x.onclick = e => {
        e.stopPropagation();
        removeFromPlaylist(opts.playlistId, t.id);
      };
      row.appendChild(x);
    }
    row.onclick = () => playQueue(tracks, i);
    list.appendChild(row);
  });
  setGrid(tracks);
}

function playQueue(tracks, start) {
  queue = tracks;
  pos = start;
  playCurrent();
}

function playCurrent() {
  const t = queue[pos];
  if (!t) return;
  audio.src = `/audio/${t.id}`;
  audio.play();
  npTitle.textContent = t.title || "(untitled)";
  npArtist.textContent = `${t.artist || ""} — ${t.album || ""}`;
}

function next() { if (pos < queue.length - 1) { pos++; playCurrent(); } }
function prev() { if (pos > 0) { pos--; playCurrent(); } }

audio.addEventListener("ended", next);
playBtn.onclick = () => (audio.paused ? audio.play() : audio.pause());
document.getElementById("next").onclick = next;
document.getElementById("prev").onclick = prev;

document.getElementById("push-zune").onclick = () => {
  // every face mirrors its checked set onto the device
  if (face === "video") doSync("video");
  else if (face === "photo") doSync("photo");
  else doSync();
};

// ── sync list (iTunes-style: the Zune mirrors the list) ──
async function loadSyncSet() {
  const rows = await getJSON("/api/select");
  syncSet = new Set(rows.map(r => r.id));
  syncAudioCount = rows.filter(r => r.type === "audio").length;
  updateSyncCount();
  markSyncBoxes();
}
function updateSyncCount() {
  const n = syncAudioCount;
  document.getElementById("sync-count").textContent =
    n ? `${n} song${n === 1 ? "" : "s"} in sync list` : "sync list is empty";
}
function markSyncBoxes() {
  document.querySelectorAll(".item-sync").forEach(cb => {
    cb.textContent = syncSet.has(Number(cb.dataset.id)) ? "☑" : "☐";
  });
}
async function toggleSync(id, on) {
  await fetch("/api/select", {
    method: on ? "POST" : "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids: [id] }),
  });
  if (on) syncSet.add(id); else syncSet.delete(id);
  updateSyncCount();
  markSyncBoxes();
}
async function doSync(type = "audio") {
  const btn = document.getElementById("push-zune");
  btn.disabled = true; btn.textContent = "syncing…";
  try {
    const r = await fetch("/api/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type }),
    });
    const d = await r.json();
    alert(d.error ? d.error
      : `Synced to ${d.model}: +${d.added} added, −${d.removed} removed ` +
        `(${d.selected} in your sync list, ${d.playlists ?? 0} folder playlist(s)).`);
  } catch (e) {
    alert("Sync failed: " + e);
  } finally {
    btn.disabled = false; btn.innerHTML = "&#8657; Zune";
  }
}
document.getElementById("sync-list").onclick = async () => {
  const ids = queue.map(t => t.id);
  if (!ids.length) {
    alert("Nothing selected yet — pick an artist, album, genre, folder, or playlist first.");
    return;
  }
  await fetch("/api/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids }),
  });
  ids.forEach(i => syncSet.add(i));
  updateSyncCount();
  markSyncBoxes();
  doSync();
};
audio.addEventListener("play", () => (playBtn.textContent = "⏸"));
audio.addEventListener("pause", () => (playBtn.textContent = "▶"));

// ── A1–F0 selection grid ──
const grid = document.getElementById("grid");
const cells = [];
for (let i = 0; i < 60; i++) {
  const row = "ABCDEF"[Math.floor(i / 10)];
  const num = i % 10 === 9 ? "0" : String(i % 10 + 1);
  const c = el("div", "cell", row + num);
  grid.appendChild(c);
  cells.push(c);
}

function setGrid(tracks) {
  cells.forEach((c, i) => {
    if (i < tracks.length) {
      c.classList.add("lit");
      c.title = tracks[i].title || "(untitled)";
      c.onclick = () => playQueue(tracks, i);
    } else {
      c.classList.remove("lit");
      c.title = "";
      c.onclick = null;
    }
  });
}

// ── views ──
// iTunes-style: pressing a group row starts playing it, then shows its
// tracks below so you can jump around (rows and the A1–F0 grid both play).
function playAndList(tracks, title, backLabel, backFn) {
  renderTracks(tracks, { title, back: [backLabel, backFn], syncable: true });
  if (tracks.length) playQueue(tracks, 0);
}

async function showArtists() {
  const items = await getJSON("/api/artists");
  renderList(items, "Artists", async item => {
    const tracks = await getJSON(`/api/query?artist=${encodeURIComponent(item.name)}`);
    playAndList(tracks, item.name, "← Artists", showArtists);
  });
}
async function showGenres() {
  const items = await getJSON("/api/genres");
  renderList(items, "Genres", async item => {
    const tracks = await getJSON(`/api/query?genre=${encodeURIComponent(item.name)}`);
    playAndList(tracks, item.name, "← Genres", showGenres);
  });
}
async function showAlbums() {
  const items = await getJSON("/api/albums");
  renderList(items, "Albums", async item => {
    let url = `/api/query?album=${encodeURIComponent(item.name)}`;
    if (item.artist) url += `&artist=${encodeURIComponent(item.artist)}`;
    const tracks = await getJSON(url);
    playAndList(tracks, item.artist ? `${item.name} — ${item.artist}` : item.name, "← Albums", showAlbums);
  });
}

async function showFolders() {
  const [items, extra] = await Promise.all([
    getJSON("/api/folders"), getJSON("/api/library/folders")]);
  renderList(items, "Folders", async item => {
    const folder = item.name === "(root)" ? "" : item.name;
    const tracks = await getJSON(`/api/query?folder=${encodeURIComponent(folder)}`);
    playAndList(tracks, item.name, "← Folders", showFolders);
  }, (item, row) => {
    const b = el("span", "item-add", "✓");
    b.title = "Add the whole folder to the sync list — then uncheck any songs you don't want";
    b.onclick = e => { e.stopPropagation(); addFolderToSync(item); };
    row.appendChild(b);
  }, list => {
    // "add a folder" row at the top: pick any folder on this laptop (WMP-style)
    const add = el("div", "item");
    add.appendChild(el("span", "item-name", "➕ Add a folder to the library…"));
    add.onclick = () => loadPicker(null);
    list.insertBefore(add, list.children[1]);
    // folders the app registered — removable, files stay on disk
    if (extra.length) {
      list.appendChild(el("h2", "list-title", "Library folders (✕ removes — its songs drop, files untouched)"));
      extra.forEach(f => {
        const row = el("div", "item");
        const n = el("span", "item-name has-sub", f.path);
        n.appendChild(el("span", "item-sub", "scanned at startup and on add"));
        row.appendChild(n);
        const x = el("span", "item-x", "✕");
        x.title = "Remove from the library";
        x.onclick = async e => {
          e.stopPropagation();
          if (!confirm(`Remove ${f.path} from the library? Its songs drop from the index; the files on disk are untouched.`)) return;
          await fetch(`/api/library/folders/${f.id}`, { method: "DELETE" });
          showFolders();
        };
        row.appendChild(x);
        list.appendChild(row);
      });
    }
  });
}

// folder picker: drill through this laptop's directories (the server lists
// them — a browser can't), then "add this folder" scans it into the library
async function loadPicker(path) {
  const d = await getJSON("/api/dirs" + (path ? `?path=${encodeURIComponent(path)}` : ""));
  if (d.error) { alert(d.error); showFolders(); return; }
  const list = document.getElementById("list");
  list.innerHTML = "";
  list.appendChild(el("h2", "list-title", "Pick a folder to add to the library"));
  list.appendChild(el("p", "list-title", d.path));
  if (d.parent && d.parent !== d.path) {
    const up = el("div", "item");
    up.appendChild(el("span", "item-name", "↑ up"));
    up.onclick = () => loadPicker(d.parent);
    list.appendChild(up);
  }
  const cta = el("div", "item cta");
  cta.appendChild(el("span", "item-name", "✓ Add this folder to the library"));
  cta.onclick = async () => {
    const r = await fetch("/api/library/folders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: d.path }),
    });
    const out = await r.json();
    if (out.error) { alert(out.error); return; }
    alert(`${d.path} added: +${out.added} new, ${out.updated} updated, −${out.removed} gone.`);
    await loadSyncSet();
    showFolders();
  };
  list.appendChild(cta);
  (d.dirs.length ? d.dirs : ["(no subfolders)"]).forEach(name => {
    const row = el("div", "item");
    row.appendChild(el("span", "item-name", name === "(no subfolders)" ? name : "📁 " + name));
    if (name !== "(no subfolders)") row.onclick = () => loadPicker(d.path + "/" + name);
    list.appendChild(row);
  });
}

// "✓" on a folder row: check in every song in it, then open it so the
// checkboxes are right there to take songs out (the Zune mirrors this list)
async function addFolderToSync(item) {
  const folder = item.name === "(root)" ? "" : item.name;
  const tracks = await getJSON(`/api/query?folder=${encodeURIComponent(folder)}`);
  if (!tracks.length) { alert(`No songs in ${item.name}.`); return; }
  await fetch("/api/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids: tracks.map(t => t.id) }),
  });
  tracks.forEach(t => syncSet.add(t.id));
  updateSyncCount();
  markSyncBoxes();
  queue = tracks;   // the open view becomes this folder, so the tools row targets it
  renderTracks(tracks, { title: item.name, back: ["← Folders", showFolders], syncable: true });
  alert(`Checked in ${tracks.length} song(s) from ${item.name}.\nUncheck the ones you don't want, then hit ⇧ Zune to push.`);
}

// "✓ Check this album" on the photo face: same idea for photos
async function checkAlbum(photos) {
  if (!photos.length) { alert("No photos in this album."); return; }
  await fetch("/api/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids: photos.map(p => p.id) }),
  });
  photos.forEach(p => syncSet.add(p.id));
  updateSyncCount();
  markSyncBoxes();
  alert(`Checked in ${photos.length} photo(s).\nUncheck the ones you don't want, then hit ⇧ Zune to push.`);
}

// ── playlists (iTunes-style user sources) ──
let currentPlaylist = null;   // {id, name} while a playlist's tracks are open

async function refreshPlAdd() {
  const pls = await getJSON("/api/playlists");
  for (const selId of ["pl-add", "pl-add-checked"]) {
    const sel = document.getElementById(selId);
    sel.innerHTML = "";
    if (!pls.length) {
      const o = el("option");
      o.value = ""; o.textContent = "(no playlists yet)";
      sel.appendChild(o);
      continue;
    }
    pls.forEach(p => {
      const o = el("option", null, p.name);
      o.value = String(p.id);
      sel.appendChild(o);
    });
  }
}

async function showSyncList() {
  currentPlaylist = null;
  document.getElementById("pl-del").disabled = true;
  const rows = (await getJSON("/api/select")).filter(r => r.type === "audio");
  renderTracks(rows, { title: "Sync list — the Zune mirrors exactly these songs",
                       syncable: true, syncList: true });
  if (!rows.length)
    document.getElementById("list").appendChild(
      el("p", "empty", "Nothing checked yet — open any view and tick ☑ the songs you want on the Zune."));
  refreshPlAdd();
}

async function showPlaylists() {
  currentPlaylist = null;
  document.getElementById("pl-del").disabled = true;
  const items = await getJSON("/api/playlists");
  renderList(items, "Playlists", item => loadPlaylist(item.id, item.name, true));
  refreshPlAdd();
}

async function loadPlaylist(id, name, play = true) {
  const tracks = await getJSON(`/api/playlists/${id}`);
  currentPlaylist = { id, name };
  document.getElementById("pl-del").disabled = false;
  renderTracks(tracks, { title: name, back: ["← Playlists", showPlaylists],
                         playlistId: id, syncable: true });
  if (play && tracks.length) playQueue(tracks, 0);
}

async function removeFromPlaylist(pid, mid) {
  await fetch(`/api/playlists/${pid}/tracks`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids: [mid] }),
  });
  // sync follows the playlist: the song drops out of the device mirror too
  await fetch("/api/select", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids: [mid] }),
  });
  syncSet.delete(mid);
  updateSyncCount();
  await loadPlaylist(pid, currentPlaylist.name, false);   // re-render, keep playing
}

document.getElementById("pl-add").onchange = async e => {
  const pid = e.target.value;
  e.target.value = "";
  if (!pid) return;
  const ids = queue.map(t => t.id);
  if (!ids.length) {
    alert("Nothing selected yet — pick an artist, album, genre, folder, or playlist first.");
    return;
  }
  const r = await fetch(`/api/playlists/${pid}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids }),
  });
  const d = await r.json();
  alert(`Added ${d.updated} track${d.updated === 1 ? "" : "s"} to the playlist.`);
  refreshPlAdd();
};

document.getElementById("pl-add-checked").onchange = async e => {
  const pid = e.target.value;
  e.target.value = "";
  if (!pid) return;
  const ids = queue.filter(t => syncSet.has(t.id)).map(t => t.id);
  if (!ids.length) {
    alert("Tick ☑ the songs you want in the current view first.");
    return;
  }
  const r = await fetch(`/api/playlists/${pid}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids }),
  });
  const d = await r.json();
  alert(`Added ${d.updated} track${d.updated === 1 ? "" : "s"}.` +
    " They're in your sync list too — ⇧ Zune puts them on the device.");
  refreshPlAdd();
};

document.getElementById("sync-count").onclick = () =>
  document.querySelector(".tabs button[data-view='sync']").click();

document.getElementById("pl-new").onclick = async () => {
  const name = prompt("Playlist name:");
  if (!name || !name.trim()) return;
  const r = await fetch("/api/playlists", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name.trim() }),
  });
  const d = await r.json();
  if (d.error) alert(d.error);
  refreshPlAdd();
  if (document.querySelector(".tabs button[data-view='playlists']").classList.contains("active"))
    showPlaylists();
};

document.getElementById("pl-del").onclick = async () => {
  if (!currentPlaylist) return;
  if (!confirm(`Delete playlist “${currentPlaylist.name}”?\nIts tracks will also drop out of the Zune sync list.`)) return;
  const tracks = await getJSON(`/api/playlists/${currentPlaylist.id}`);
  const ids = tracks.map(t => t.id);
  if (ids.length) {
    await fetch("/api/select", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    ids.forEach(i => syncSet.delete(i));
    updateSyncCount();
  }
  await fetch(`/api/playlists/${currentPlaylist.id}`, { method: "DELETE" });
  currentPlaylist = null;
  showPlaylists();
};

document.querySelectorAll(".tabs button[data-view]").forEach(btn => {
  btn.onclick = () => {
    document.querySelectorAll(".tabs button").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    ({ artists: showArtists, albums: showAlbums, genres: showGenres,
       folders: showFolders, playlists: showPlaylists, sync: showSyncList })[btn.dataset.view]();
  };
});

document.getElementById("surprise").onclick = async () => {
  const all = await getJSON("/api/query");
  if (!all.length) return;
  const i = Math.floor(Math.random() * all.length);
  renderTracks(all, { syncable: true });
  playQueue(all, i);
};

// ── faces: which media the cabinet is showing ──
const cabinet = document.querySelector(".cabinet");
let face = "audio";
let currentPhotos = [];   // photo face: photos in the currently opened album

function setFace(next) {
  face = next;
  document.querySelectorAll(".faces button").forEach(b =>
    b.classList.toggle("active", b.dataset.face === next));
  cabinet.classList.remove("face-audio", "face-photo", "face-video");
  cabinet.classList.add("face-" + next);
  if (next === "audio") {
    currentPlaylist = null;
    document.getElementById("pl-del").disabled = true;
    refreshPlAdd();
    showArtists();
  }
  else if (next === "photo") showPhotoAlbums();
  else showDvd();
}

document.querySelectorAll(".faces button").forEach(btn => {
  btn.onclick = () => setFace(btn.dataset.face);
});

// ── photo face ──
async function showPhotoAlbums() {
  const items = await getJSON("/api/albums?type=photo");
  npTitle.textContent = "Photo Albums";
  renderList(items, "Albums", item => showPhotoGrid(item.name),
    (item, row) => {
      const play = el("span", "item-x", "▶");
      play.title = "Play this album as a slideshow";
      play.onclick = async e => {
        e.stopPropagation();
        const photos = await getJSON(`/api/query?type=photo&album=${encodeURIComponent(item.name)}`);
        currentPhotos = photos;
        startSlideshow(photos, 0);
      };
      row.appendChild(play);
    });
}

async function showPhotoGrid(album) {
  const photos = await getJSON(`/api/query?type=photo&album=${encodeURIComponent(album)}`);
  currentPhotos = photos;
  const list = document.getElementById("list");
  list.innerHTML = "";
  const back = el("div", "item", "← All albums");
  back.onclick = () => showPhotoAlbums();
  list.appendChild(back);
  list.appendChild(el("h2", "list-title", `${album} — ☑ to sync, click a photo for the slideshow`));
  const play = el("div", "item", "▶ Play slideshow");
  play.onclick = () => startSlideshow(photos, 0);
  list.appendChild(play);
  const checkAll = el("div", "item", "✓ Check this album");
  checkAll.onclick = () => checkAlbum(photos);
  list.appendChild(checkAll);
  const gridEl = el("div", "photo-grid");
  photos.forEach((p, i) => {
    const thumb = el("div", "photo-thumb");
    const img = new Image();
    img.src = `/photo/${p.id}`;
    img.alt = p.title;
    const cb = el("span", "item-sync");
    cb.dataset.id = p.id;
    cb.textContent = syncSet.has(p.id) ? "☑" : "☐";
    cb.title = "Sync to Zune (checked = on the device)";
    cb.onclick = e => { e.stopPropagation(); toggleSync(p.id, !syncSet.has(p.id)); };
    thumb.appendChild(img);
    thumb.appendChild(cb);
    thumb.appendChild(el("div", "thumb-label", p.title));
    thumb.onclick = () => startSlideshow(photos, i);
    gridEl.appendChild(thumb);
  });
  list.appendChild(gridEl);
  npTitle.textContent = album;
  npArtist.textContent = `${photos.length} photos`;
}

// ── slideshow ──
const ss = { photos: [], pos: 0, timer: null, open: false };

function startSlideshow(photos, start) {
  ss.photos = photos; ss.pos = start; ss.open = true;
  document.getElementById("slideshow").hidden = false;
  ssShow();
  ssAuto();
}

function ssShow() {
  const p = ss.photos[ss.pos];
  document.getElementById("ss-img").src = `/photo/${p.id}`;
  document.getElementById("ss-count").textContent = `${ss.pos + 1} / ${ss.photos.length} — ${p.title}`;
}

function ssAuto() {
  clearInterval(ss.timer);
  ss.timer = setInterval(() => { ss.pos = (ss.pos + 1) % ss.photos.length; ssShow(); }, 4000);
}

function ssStep(delta) {
  if (!ss.photos.length) return;   // stage is in video mode — arrows don't apply
  ss.pos = (ss.pos + delta + ss.photos.length) % ss.photos.length;
  ssShow();
  ssAuto();
}

function stopSlideshow() {
  ss.open = false;
  clearInterval(ss.timer);
  video.pause();
  video.hidden = true;
  document.getElementById("ss-img").hidden = false;
  document.getElementById("ss-bar").hidden = false;
  document.getElementById("slideshow").hidden = true;
}

document.getElementById("ss-prev").onclick = () => ssStep(-1);
document.getElementById("ss-next").onclick = () => ssStep(1);
document.getElementById("ss-close").onclick = stopSlideshow;
document.addEventListener("keydown", e => {
  if (!ss.open) return;
  if (e.key === "ArrowRight") ssStep(1);
  else if (e.key === "ArrowLeft") ssStep(-1);
  else if (e.key === "Escape") stopSlideshow();
});

// ── DVD face ──
let currentVideos = [];   // DVD face: the disc list

function fmtDur(s) {
  s = Math.round(s);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

async function showDvd() {
  const videos = await getJSON("/api/query?type=video");
  currentVideos = videos;
  const list = document.getElementById("list");
  list.innerHTML = "";
  list.appendChild(el("h2", "list-title", "Discs — ☑ to sync, click to play"));
  if (!videos.length) {
    list.appendChild(el("p", "dvd-empty", "No videos found — scan a folder that has some"));
  } else {
    videos.forEach(v => {
      const row = el("div", "item");
      const cb = el("span", "item-sync");
      cb.dataset.id = v.id;
      cb.textContent = syncSet.has(v.id) ? "☑" : "☐";
      cb.title = "Sync to Zune (checked = on the device)";
      cb.onclick = e => { e.stopPropagation(); toggleSync(v.id, !syncSet.has(v.id)); };
      row.appendChild(cb);
      const name = el("span", "item-name", v.title);
      if (v.album) {
        name.classList.add("has-sub");
        name.appendChild(el("span", "item-sub", v.album));
      }
      row.appendChild(name);
      row.appendChild(el("span", "item-count", v.duration ? fmtDur(v.duration) : ""));
      row.onclick = () => playVideo(v);
      list.appendChild(row);
    });
  }
  npTitle.textContent = "DVD Player";
  npArtist.textContent = videos.length ? `${videos.length} disc${videos.length === 1 ? "" : "s"}` : "no discs";
}

// ── video playback (the stage) ──
const video = document.getElementById("video");

function playVideo(v) {
  stopSlideshow();   // clear slideshow state and restore stage defaults
  ss.open = true;
  document.getElementById("ss-img").hidden = true;
  document.getElementById("ss-bar").hidden = true;
  video.hidden = false;
  video.src = `/video/${v.id}`;
  video.play();
  document.getElementById("slideshow").hidden = false;
  npTitle.textContent = v.title;
  npArtist.textContent = v.album || "DVD";
}

// init
setFace("audio");
loadSyncSet();
