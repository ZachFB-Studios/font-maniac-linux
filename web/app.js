"use strict";
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const CATS = [["sans", "Sans"], ["serif", "Serif"], ["slab", "Slab"], ["script", "Script"], ["mono", "Mono"],
  ["display", "Display"], ["blackletter", "Blackletter"], ["symbol", "Symbol"], ["world", "Non-Latin"], ["other", "Other"]];
const SCRIPTS = [["latinext", "Latin Ext"], ["viet", "Vietnamese"], ["cyrillic", "Cyrillic"], ["greek", "Greek"],
  ["arabic", "Arabic"], ["hebrew", "Hebrew"], ["devanagari", "Devanagari"], ["thai", "Thai"], ["cjk", "CJK"],
  ["kana", "Japanese"], ["hangul", "Korean"]];
const BUCKETS = {
  width: [["compressed", "Compressed"], ["condensed", "Condensed"], ["normal", "Normal"], ["wide", "Wide"]],
  contrast: [["low", "Low"], ["mid", "Medium"], ["high", "High"]],
  xh: [["small", "Small"], ["mid", "Medium"], ["large", "Large"]],
};
const FLAGS = [["enabled", "Enabled"], ["mono", "Monospaced"], ["variable", "Variable"], ["italic", "Italic"],
  ["upright", "Upright"], ["dupes", "Duplicate files"], ["samename", "Same name twice"]];
const GCATS = [["sans_serif", "Sans"], ["serif", "Serif"], ["display", "Display"], ["handwriting", "Handwriting"],
  ["monospace", "Mono"]];
const LICENSES = [["OFL", "OFL"], ["APACHE2", "Apache 2"], ["UFL", "Ubuntu"]];
const LONG_SAMPLE = "The quick brown fox jumps over the lazy dog 0123456789";

const S = {
  faces: [], families: new Map(), dirs: [], library: "", enabled: new Set(), gen: 0,
  list: [], view: "families", sort: "name", similarTo: null, qmode: "names", tagRank: null, tagQ: "", sortBefore: "name", tags: null, text: "", th: 56, sel: null,
  f: { q: "", cats: new Set(), scripts: new Set(), width: new Set(), contrast: new Set(), xh: new Set(),
       flags: new Set(), gcat: new Set(), lic: new Set(), wmin: 100, wmax: 900, folder: "" },
};

// ------------------------------------------------------------------ data

function bucket(kind, x) {
  if (kind === "width") {
    if (x.wr == null) return x.width <= 2 ? "compressed" : x.width <= 4 ? "condensed" : x.width <= 6 ? "normal" : "wide";
    return x.wr < 0.55 ? "compressed" : x.wr < 0.66 ? "condensed" : x.wr < 0.86 ? "normal" : "wide";
  }
  if (x[kind] == null) return null;
  if (kind === "contrast") return x.contrast < 1.25 ? "low" : x.contrast < 1.9 ? "mid" : "high";
  return x.xh < 0.69 ? "small" : x.xh < 0.775 ? "mid" : "large";
}

async function load() {
  const d = await (await fetch("/api/fonts")).json();
  S.dirs = d.dirs; S.gf = d.gf || []; S.library = d.library; S.gen = Math.round(d.status.time || 0);
  S.enabled = new Set(d.enabled);
  const shaCount = new Map(), nameCount = new Map();
  S.faces = d.faces.map(r => {
    const [id, dir, file, idx, family, style, weight, width, italic, mono, variable, cat, serif, contrast, xh, wr, stem, slant, scripts, tw, sha, added] = r;
    const rel = d.dirs[dir];
    const x = { id, dir, file, idx, family, style, weight, width, italic, mono, variable, cat, serif, contrast, xh, wr, stem, slant,
      scripts: new Set(scripts ? scripts.split(",") : []), tw, sha, added,
      path: (rel === "." ? d.library : d.library + "/" + rel) + "/" + file, top: rel === "." ? "(top level)" : rel.split("/")[0] };
    const g = S.gf[dir];
    x.gcat = new Set(g ? g[0] : []); x.lic = g ? g[1] : null; x.designer = g ? g[2] : "";
    x.key = family.toLowerCase();
    x.hay = (family + " " + style + " " + file).toLowerCase();
    x.dirl = rel.toLowerCase();
    x.b = { width: bucket("width", x), contrast: bucket("contrast", x), xh: bucket("xh", x) };
    shaCount.set(sha, (shaCount.get(sha) || 0) + 1);
    const nk = x.key + "|" + style.toLowerCase();
    nameCount.set(nk, (nameCount.get(nk) || 0) + 1);
    x.nk = nk;
    return x;
  });
  S.families = new Map();
  for (const x of S.faces) {
    x.dupe = shaCount.get(x.sha) > 1;
    x.samename = nameCount.get(x.nk) > 1;
    let fam = S.families.get(x.key);
    if (!fam) S.families.set(x.key, fam = { key: x.key, name: x.family, faces: [] });
    fam.faces.push(x);
  }
  for (const fam of S.families.values()) fam.faces.sort(faceOrder);
  buildFilters();
  apply();
  status(d.status);
}

const faceOrder = (a, b) => a.italic - b.italic || a.weight - b.weight || a.width - b.width || a.style.localeCompare(b.style);
const isOn = x => S.enabled.has(x.path);

// ------------------------------------------------------------------ filtering

function matches(x, skip) {
  const f = S.f;
  if (S.qmode === "tags") { if (S.tagRank && !S.tagRank.has(x.id)) return false; }
  else if (f.q) for (const w of f.qw) {
    if (w.startsWith("in:") ? !x.dirl.includes(w.slice(3)) : !x.hay.includes(w)) return false;
  }
  if (skip !== "cats" && f.cats.size && !f.cats.has(x.cat)) return false;
  if (x.weight < f.wmin || x.weight > f.wmax) return false;
  for (const k of ["width", "contrast", "xh"]) if (skip !== k && f[k].size && !f[k].has(x.b[k])) return false;
  if (skip !== "scripts") for (const s of f.scripts) if (!x.scripts.has(s)) return false;
  if (f.folder && x.top !== f.folder) return false;
  if (skip !== "gcat" && f.gcat.size && ![...x.gcat].some(c => f.gcat.has(c))) return false;
  if (skip !== "lic" && f.lic.size && !f.lic.has(x.lic)) return false;
  if (skip !== "flags") for (const fl of f.flags) {
    if (fl === "enabled" && !isOn(x)) return false;
    if (fl === "mono" && !x.mono) return false;
    if (fl === "variable" && !x.variable) return false;
    if (fl === "italic" && !x.italic) return false;
    if (fl === "upright" && x.italic) return false;
    if (fl === "dupes" && !x.dupe) return false;
    if (fl === "samename" && !x.samename) return false;
  }
  return true;
}

function rep(faces) {  // the face a family tile shows: upright, closest to Regular
  let best = faces[0], score = Infinity;
  for (const x of faces) {
    const s = Math.abs(x.weight - 400) + x.italic * 1000 + Math.abs(x.width - 5) * 50;
    if (s < score) { score = s; best = x; }
  }
  return best;
}

function vec(x) {
  if (x.serif == null) return null;
  return [Math.min(x.serif - 1, 1.5) * 1.6, Math.log(x.contrast || 1) / Math.log(5) * 2, (x.xh - 0.72) / 0.06,
    (x.wr - 0.72) / 0.12, (x.stem - 0.15) / 0.06, x.slant / 10];
}
function dist(a, b) {
  const va = vec(a), vb = vec(b);
  if (!va || !vb) return 1e9;
  let d = a.cat === b.cat ? 0 : 4;
  for (let i = 0; i < va.length; i++) d += (va[i] - vb[i]) ** 2;
  return d;
}

const SORTS = {
  name: (a, b) => a.name.localeCompare(b.name),
  weight: (a, b) => (a.x.stem ?? 9) - (b.x.stem ?? 9),
  contrast: (a, b) => (b.x.contrast ?? -1) - (a.x.contrast ?? -1),
  xh: (a, b) => (b.x.xh ?? -1) - (a.x.xh ?? -1),
  wr: (a, b) => (a.x.wr ?? 9) - (b.x.wr ?? 9),
  newest: (a, b) => b.x.added - a.x.added,
  folder: (a, b) => S.dirs[a.x.dir].localeCompare(S.dirs[b.x.dir]) || a.name.localeCompare(b.name),
  similar: (a, b) => a.d - b.d,
  match: (a, b) => b.score - a.score || a.name.localeCompare(b.name),
};

// tags mode: rank by the best-matching face; a family tile shows that face
function scoreItem(it) {
  let best = -9;
  for (const x of it.faces) { const s = S.tagRank.get(x.id) ?? -9; if (s > best) { best = s; if (S.view === "families") it.x = x; } }
  it.score = best;
}

function apply() {
  const f = S.f;
  f.qw = f.q.toLowerCase().split(/\s+/).filter(Boolean);
  const counts = { cats: new Map(), width: new Map(), contrast: new Map(), xh: new Map(), scripts: new Map(), flags: new Map(), gcat: new Map(), lic: new Map() };
  const bump = (m, k) => m.set(k, (m.get(k) || 0) + 1);
  let list = [];
  if (S.view === "families") {
    for (const fam of S.families.values()) {
      const hit = fam.faces.filter(x => matches(x));
      if (hit.length) list.push({ fam, faces: hit, x: rep(hit), name: fam.name });
      countFam(fam, counts, bump);
    }
  } else {
    for (const x of S.faces) {
      if (matches(x)) list.push({ fam: S.families.get(x.key), faces: [x], x, name: x.family + " " + x.style });
      countFace(x, counts, bump);
    }
  }
  if (S.sort === "similar" && S.similarTo) for (const it of list) it.d = dist(S.similarTo, it.x);
  if (S.tagRank) for (const it of list) scoreItem(it); else for (const it of list) it.score = 0;
  list.sort(SORTS[S.sort] || SORTS.name);
  S.list = list;
  $("#count").textContent = list.length.toLocaleString() + (S.view === "families" ? " families" : " styles");
  updateCounts(counts);
  layout(true);
}

// counts for a chip = matches if that chip's own group were ignored (counted once per family in family view)
function countFam(fam, counts, bump) {
  for (const [group, key] of [["cats", x => [x.cat]], ["width", x => [x.b.width]], ["contrast", x => [x.b.contrast]],
    ["xh", x => [x.b.xh]], ["scripts", x => [...x.scripts]], ["flags", flagKeys], ["gcat", x => [...x.gcat]],
    ["lic", x => x.lic ? [x.lic] : []]]) {
    const seen = new Set();
    for (const x of fam.faces) if (matches(x, group)) for (const k of key(x)) seen.add(k);
    for (const k of seen) bump(counts[group], k);
  }
}
function countFace(x, counts, bump) {
  if (matches(x, "cats")) bump(counts.cats, x.cat);
  for (const k of ["width", "contrast", "xh"]) if (matches(x, k)) bump(counts[k], x.b[k]);
  if (matches(x, "scripts")) for (const s of x.scripts) bump(counts.scripts, s);
  if (matches(x, "flags")) for (const k of flagKeys(x)) bump(counts.flags, k);
  if (matches(x, "gcat")) for (const c of x.gcat) bump(counts.gcat, c);
  if (matches(x, "lic") && x.lic) bump(counts.lic, x.lic);
}
function flagKeys(x) {
  const out = [];
  if (isOn(x)) out.push("enabled");
  if (x.mono) out.push("mono");
  if (x.variable) out.push("variable");
  out.push(x.italic ? "italic" : "upright");
  if (x.dupe) out.push("dupes");
  if (x.samename) out.push("samename");
  return out;
}

// ------------------------------------------------------------------ filter panel

function chipGroup(title, group, items) {
  return `<h3>${title}</h3><div class="chips" data-group="${group}">` +
    items.map(([k, label]) => `<button class="chip" data-k="${k}">${esc(label)} <small></small></button>`).join("") + "</div>";
}

function buildFilters() {
  const folders = [...new Set(S.faces.map(x => x.top))].sort((a, b) => a.localeCompare(b));
  if (S.f.folder && !folders.includes(S.f.folder)) S.f.folder = "";
  $("#folder").innerHTML = `<option value="">All folders</option>` +
    folders.map(f => `<option${f === S.f.folder ? " selected" : ""}>${esc(f)}</option>`).join("");
  $("#filters").innerHTML =
    chipGroup("Category", "cats", CATS) +
    `<h3>Weight</h3><div class="range2"><span class="lab" id="wlab"></span><div class="pair">
      <input type="range" id="wmin" min="100" max="900" step="100" value="${S.f.wmin}">
      <input type="range" id="wmax" min="100" max="900" step="100" value="${S.f.wmax}"></div></div>` +
    chipGroup("Width", "width", BUCKETS.width) +
    chipGroup("Contrast", "contrast", BUCKETS.contrast) +
    chipGroup("x-height", "xh", BUCKETS.xh) +
    chipGroup("Show only", "flags", FLAGS) +
    chipGroup("Also covers", "scripts", SCRIPTS) +
    (S.gf.some(Boolean) ? chipGroup("Google Fonts category", "gcat", GCATS) + chipGroup("License", "lic", LICENSES) : "") +
    `<button class="tonal clearall" id="clear">Clear filters</button>`;
  for (const el of document.querySelectorAll("#filters .chips")) {
    const set = S.f[el.dataset.group];
    for (const b of el.children) b.classList.toggle("on", set.has(b.dataset.k));
  }
  wlabel();
}

function wlabel() { $("#wlab").textContent = S.f.wmin === 100 && S.f.wmax === 900 ? "any" : `${S.f.wmin}–${S.f.wmax}`; }

function updateCounts(counts) {
  for (const el of document.querySelectorAll("#filters .chips")) {
    const m = counts[el.dataset.group];
    for (const b of el.children) {
      const n = m.get(b.dataset.k) || 0;
      b.querySelector("small").textContent = n.toLocaleString();
      b.classList.toggle("zero", !n && !b.classList.contains("on"));
    }
  }
}

$("#filters").addEventListener("click", e => {
  const chip = e.target.closest(".chip");
  if (chip) {
    const set = S.f[chip.parentElement.dataset.group], k = chip.dataset.k;
    set.has(k) ? set.delete(k) : set.add(k);
    if (k === "italic" && set.has(k)) set.delete("upright");
    if (k === "upright" && set.has(k)) set.delete("italic");
    for (const b of chip.parentElement.children) b.classList.toggle("on", set.has(b.dataset.k));
    return apply();
  }
  if (e.target.id === "clear") {
    Object.assign(S.f, { q: "", wmin: 100, wmax: 900, folder: "" });
    for (const k of ["cats", "scripts", "width", "contrast", "xh", "flags", "gcat", "lic"]) S.f[k].clear();
    $("#q").value = ""; $("#folder").value = "";
    if (S.sort === "similar") setSort("name");
    buildFilters(); apply();
  }
});
$("#filters").addEventListener("input", e => {
  if (e.target.id === "wmin" || e.target.id === "wmax") {
    let a = +$("#wmin").value, b = +$("#wmax").value;
    if (a > b) e.target.id === "wmin" ? b = a : a = b;
    $("#wmin").value = S.f.wmin = a; $("#wmax").value = S.f.wmax = b;
    wlabel(); apply();
  }
});

// ------------------------------------------------------------------ grid (virtualised)

const grid = $("#grid"), sizer = $("#sizer");
let G = { cols: 1, tw: 300, rh: 100, pool: new Map() };

function inkUrl(x, h) {
  const th = h || S.th;
  if (!S.text && th <= 80) return `/thumb/${x.id}?g=${S.gen}`;
  const px = Math.max(80, Math.round(th * devicePixelRatio));
  return `/render?id=${x.id}&s=${Math.round(px * 0.7)}&h=${px}` + (S.text ? "&t=" + encodeURIComponent(S.text) : "");
}

function layout(reset) {
  const W = grid.clientWidth - 16;
  G.cols = Math.max(1, Math.floor(W / Math.max(200, S.th * 4.8)));
  G.tw = Math.floor(W / G.cols);
  G.rh = S.th + 54;
  document.documentElement.style.setProperty("--th", S.th + "px");
  sizer.style.height = Math.ceil(S.list.length / G.cols) * G.rh + 16 + "px";
  $("#empty").hidden = S.list.length > 0;
  if (reset) { for (const el of G.pool.values()) el.remove(); G.pool.clear(); }
  draw();
}

function draw() {
  const top = grid.scrollTop, h = grid.clientHeight;
  const r0 = Math.max(0, Math.floor(top / G.rh) - 2), r1 = Math.ceil((top + h) / G.rh) + 2;
  const i0 = r0 * G.cols, i1 = Math.min(S.list.length, r1 * G.cols);
  for (const [i, el] of G.pool) if (i < i0 || i >= i1) { el.remove(); G.pool.delete(i); }
  const frag = document.createDocumentFragment();
  for (let i = i0; i < i1; i++) {
    if (G.pool.has(i)) continue;
    const it = S.list[i], el = document.createElement("div");
    el.className = "tile";
    el.dataset.i = i;
    el.style.cssText = `transform:translate(${8 + (i % G.cols) * G.tw}px,${8 + Math.floor(i / G.cols) * G.rh}px);width:${G.tw - 6}px;height:${G.rh - 6}px`;
    paintTile(el, it);
    G.pool.set(i, el);
    frag.appendChild(el);
  }
  sizer.appendChild(frag);
}

function paintTile(el, it) {
  const n = it.fam.faces.length, on = it.faces.some(isOn);
  const sub = S.view === "families" ? (n > 1 ? `${n} styles` : it.x.style) : it.x.cat;
  el.classList.toggle("sel", S.sel === it.fam.key);
  el.innerHTML = `<div class="ink" style="--m:url('${inkUrl(it.x)}')"></div>
    <div class="meta"><span class="name">${esc(S.view === "families" ? it.name : it.x.family + " " + it.x.style)}</span>
    <span class="sub">· ${esc(sub)}</span>${on ? '<span class="dot" title="Enabled"></span>' : ""}</div>`;
}

function repaint() { for (const [i, el] of G.pool) paintTile(el, S.list[i]); }

grid.addEventListener("scroll", () => requestAnimationFrame(draw), { passive: true });
new ResizeObserver(() => layout(true)).observe(grid);
grid.addEventListener("click", e => {
  const t = e.target.closest(".tile");
  if (t) openFamily(S.list[+t.dataset.i].fam.key, S.list[+t.dataset.i].x.id);
});

// ------------------------------------------------------------------ drawer

const drawer = $("#drawer");
let D = { size: 40, open: null, face: null };

function openFamily(key, faceId) {
  S.sel = key;
  D.face = faceId;
  repaint();
  renderDrawer();
}

function renderDrawer() {
  const fam = S.families.get(S.sel);
  if (!fam) { drawer.hidden = true; return; }
  drawer.hidden = false;
  const onCount = fam.faces.filter(isOn).length, folders = [...new Set(fam.faces.map(x => S.dirs[x.dir]))];
  const r = rep(fam.faces);
  const gfLine = r.lic ? `<br>Google Fonts · ${esc([...r.gcat].join(", ").replaceAll("_", " "))} · ${esc(r.lic)}` +
    (r.designer ? ` · by ${esc(r.designer)}` : "") : "";
  drawer.innerHTML = `
    <div class="head"><h2>${esc(fam.name)}</h2><button class="close" data-a="close" title="Close (Esc)">
      <svg viewBox="0 0 24 24"><path d="M19 6.4 17.6 5 12 10.6 6.4 5 5 6.4 10.6 12 5 17.6 6.4 19 12 13.4 17.6 19 19 17.6 13.4 12z"/></svg></button></div>
    <div class="info">${fam.faces.length} style${fam.faces.length > 1 ? "s" : ""} · ${esc(r.cat)} · ${onCount} enabled${gfLine}<br>
      ${folders.slice(0, 3).map(esc).join("<br>")}${folders.length > 3 ? `<br>+${folders.length - 3} more folders` : ""}</div>
    <div class="actions">
      <button class="filled" data-a="enable-all">${onCount === fam.faces.length ? "All enabled" : "Enable family"}</button>
      <button class="tonal" data-a="disable-all" ${onCount ? "" : "disabled"}>Disable family</button>
      <button class="tonal" data-a="similar" ${r.serif == null ? "disabled" : ""}>Similar</button>
      <button class="tonal" data-a="reveal" data-id="${r.id}">Show in folder</button>
    </div>
    <label class="dsize">Size <input type="range" min="20" max="140" value="${D.size}" data-a="dsize"></label>
    ${fam.faces.map(x => `
      <div class="face${x.id === D.face ? " sel" : ""}" data-id="${x.id}">
        <div class="row"><b>${esc(x.style)}</b><span>${x.weight}${x.italic ? " · italic" : ""}${x.variable ? " · variable" : ""}${x.dupe ? " · duplicate" : ""}</span><span class="sp"></span>
          <button class="icon" data-a="copy" title="Copy path"><svg viewBox="0 0 24 24"><path d="M16 1H4a2 2 0 0 0-2 2v14h2V3h12zm3 4H8a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2zm0 16H8V7h11z"/></svg></button>
          <button class="icon" data-a="reveal" title="Show in folder"><svg viewBox="0 0 24 24"><path d="M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8z"/></svg></button>
          <button class="switch${isOn(x) ? " on" : ""}" data-a="toggle" title="${isOn(x) ? "Enabled: click to disable" : "Disabled: click to enable"}"></button></div>
        <div class="ink" data-a="info" style="height:${D.size}px;--m:url('${faceUrl(x)}')" title="Click for details"></div>
        <div class="more"></div>
      </div>`).join("")}`;
  if (D.face) showInfo(D.face, false);
}

function faceUrl(x) {
  const px = Math.round(D.size * 1.25 * devicePixelRatio);
  const t = S.text || (x.scripts.has("latin") ? LONG_SAMPLE : "");
  return `/render?id=${x.id}&s=${Math.round(px * 0.72)}&h=${px}` + (t ? "&t=" + encodeURIComponent(t) : "");
}

async function showInfo(id, toggle = true) {
  const el = drawer.querySelector(`.face[data-id="${id}"] .more`);
  if (!el) return;
  if (toggle && el.innerHTML) { el.innerHTML = ""; return; }
  const d = await (await fetch("/api/face/" + id)).json();
  const kb = d.size > 1048576 ? (d.size / 1048576).toFixed(1) + " MB" : Math.round(d.size / 1024) + " KB";
  const rows = [["File", d.path], ["Full name", d.full], ["PostScript", d.ps], ["Version", d.version],
    ["Designer", d.designer], ["Vendor", d.vendor], ["Glyphs", d.glyphs + " · " + kb], ["Covers", d.scripts.replaceAll(",", ", ")],
    ["Axes", d.axes], ["Category", `${d.category} (from ${d.cat_src})`],
    ["Measured", d.serif == null ? "" : `serif ${d.serif} · contrast ${d.contrast} · x-height ${d.xh} · width ${d.wr} · stem ${d.stem} · slant ${d.slant}°`],
    ["Identical copies", d.twins.join("\n")]];
  el.innerHTML = `<div class="gsheet" data-id="${id}"></div>` +
    `<dl class="details">${rows.filter(r => r[1]).map(([k, v]) => `<dt>${k}</dt><dd>${esc(v).replaceAll("\n", "<br>")}</dd>`).join("")}</dl>`;
  glyphSheet(el.firstChild, id);
}

// ------------------------------------------------------------------ glyph sheet

const GS = { faces: new Set(), lists: new Map() };
const BASIC = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.,:;!?&@#$%*()[]{}'\"-–—/\\+=<>_€£¥©®™°±×÷…•";
const GFILTERS = [["all", "All"], ["L", "Letters"], ["N", "Numbers"], ["P", "Punctuation"], ["Sm", "Math"], ["arrows", "Arrows"],
  ["Sc", "Currency"], ["S", "Symbols"], ["Co", "Private use"]];
const isArrow = cp => (cp >= 0x2190 && cp <= 0x21FF) || (cp >= 0x27F0 && cp <= 0x27FF) || (cp >= 0x2900 && cp <= 0x297F) || (cp >= 0x2B00 && cp <= 0x2BFF) || (cp >= 0x1F800 && cp <= 0x1F8FF);
const gfilter = (f, g) => f === "all" || (f === "arrows" ? isArrow(g[0]) : f === "S" ? g[2][0] === "S" && g[2] !== "Sm" && g[2] !== "Sc" && !isArrow(g[0]) :
  f.length === 1 ? g[2][0] === f : g[2] === f);
const hex = cp => cp.toString(16).toUpperCase().padStart(4, "0");

async function fontFamilyFor(id) {      // the real font file, so the cells are text you can copy
  const fam = "fmface" + id;
  if (!GS.faces.has(id)) {
    GS.faces.add(id);
    try { document.fonts.add(await new FontFace(fam, `url(/font/${id})`).load()); }
    catch { GS.faces.delete(id); }
  }
  return fam;
}

async function glyphSheet(box, id) {
  box.innerHTML = `<div class="ghead"><b>Glyphs</b><span class="gcount">loading…</span></div>`;
  const [fam, d] = await Promise.all([fontFamilyFor(id),
    GS.lists.get(id) || fetch("/api/glyphs/" + id).then(r => r.json())]);
  if (d.error) { box.querySelector(".gcount").textContent = d.error; return; }
  GS.lists.set(id, d);
  const st = { id, fam, d, expanded: false, filter: "all", q: "", sel: null, shown: 0, list: [] };
  box._g = st;
  gRender(box);
}

function gBasic(d) {
  const have = new Map(d.chars.map(g => [g[0], g]));
  const out = [...BASIC].map(c => have.get(c.codePointAt(0))).filter(Boolean);
  return out.length >= 20 ? out : d.chars.slice(0, 96);    // symbol / non-Latin fonts: their first glyphs
}

function gRender(box) {
  const st = box._g, d = st.d, n = d.chars.length, um = d.unmapped.length;
  const counts = new Map(GFILTERS.map(([k]) => [k, 0]));
  if (st.expanded) for (const g of d.chars) for (const [k] of GFILTERS) if (gfilter(k, g)) counts.set(k, counts.get(k) + 1);
  box.innerHTML = `
    <div class="ghead"><b>Glyphs</b><span class="gcount">${n.toLocaleString()} characters${um ? ` · ${um.toLocaleString()} more without a character code` : ""}</span>
      <span class="sp"></span><button class="text" data-g="expand">${st.expanded ? "Basic sheet" : `All ${(n + um).toLocaleString()} glyphs`}</button></div>
    ${st.expanded ? `<div class="gtools"><div class="chips">${GFILTERS.filter(([k]) => k === "all" || counts.get(k))
      .map(([k, l]) => `<button class="chip${st.filter === k ? " on" : ""}" data-g="filter" data-k="${k}">${l} <small>${counts.get(k).toLocaleString()}</small></button>`).join("")}</div>
      <div class="field gsearch"><svg viewBox="0 0 24 24"><path d="M15.5 14h-.8l-.3-.3A6.5 6.5 0 1 0 14 15.5l.3.3v.8l5 5 1.5-1.5-5-5zm-6 0a4.5 4.5 0 1 1 0-9 4.5 4.5 0 0 1 0 9z"/></svg>
        <input data-g="q" placeholder="Name or U+ code · integral, arrow, 2211" value="${esc(st.q)}" autocomplete="off" spellcheck="false"></div></div>` : ""}
    <div class="ggrid${st.expanded ? " big" : ""}" style="font-family:'${st.fam}', 'Adobe NotDef'"></div>
    <div class="gmore"></div>
    <div class="ginfo">${gInfo(st)}</div>`;
  st.list = st.expanded ? d.chars.filter(g => gfilter(st.filter, g) && gMatch(st.q, g)) : gBasic(d);
  st.shown = 0;
  gMore(box);
  if (st.expanded && um && st.filter === "all" && !st.q) {
    const u = document.createElement("div");
    u.className = "gunmapped";
    u.innerHTML = `<div class="ghead"><b>Without a character code</b><span class="gcount">alternates, ligatures and other forms an app picks for you; not typeable, shown for reference</span></div><div class="ggrid big svg"></div>`;
    box.querySelector(".gmore").after(u);
    st.uShown = 0;
  }
  gObserve(box);
}

function gMatch(q, g) {
  if (!q) return true;
  const m = q.match(/^(?:u\+|0x)?([0-9a-f]{2,6})$/i);
  if (m && g[0] === parseInt(m[1], 16)) return true;
  if (q.length === 1 || [...q].length === 1) return String.fromCodePoint(g[0]) === q;
  return g[1].toLowerCase().includes(q.toLowerCase());
}

function gCell(g) {
  const ch = String.fromCodePoint(g[0]);
  return `<button class="gl" data-g="pick" data-cp="${g[0]}" title="${esc(g[1] || "U+" + hex(g[0]))}  ·  click to copy">${esc(ch)}</button>`;
}

function gMore(box) {             // add cells a chunk at a time, so 40,000-glyph CJK fonts stay smooth
  const st = box._g, grid = box.querySelector(".ggrid");
  const next = st.list.slice(st.shown, st.shown + 600);
  grid.insertAdjacentHTML("beforeend", next.map(gCell).join(""));
  st.shown += next.length;
  if (!st.list.length) grid.innerHTML = `<div class="gnone">No glyphs match.</div>`;
}

async function gMoreUnmapped(box) {
  const st = box._g, grid = box.querySelector(".gunmapped .ggrid");
  if (!grid || st.uBusy || st.uShown >= st.d.unmapped.length) throw 0;   // nothing more: stop the fill loop
  st.uBusy = true;
  try {
    const r = await (await fetch(`/api/glyphs/${st.id}?start=${st.uShown}&n=300`)).json();
    const h = r.asc - r.desc;
    grid.insertAdjacentHTML("beforeend", r.glyphs.map(([name, path, w, b]) => {
      const x0 = Math.min(0, b ? b[0] : 0), x1 = Math.max(w, b ? b[2] : w, 1);
      return `<div class="gl" title="${esc(name)}"><svg viewBox="${x0} ${-r.asc} ${x1 - x0} ${h}"><path transform="scale(1,-1)" d="${path}"/></svg></div>`;
    }).join(""));
    st.uShown += r.glyphs.length;
  } finally { st.uBusy = false; }
}

function gObserve(box) {             // fill the next chunk once the end of the grid comes near the bottom of the drawer
  const st = box._g, end = () => drawer.getBoundingClientRect().bottom + 500;
  while (st.shown < st.list.length && box.querySelector(".gmore").getBoundingClientRect().top < end()) gMore(box);
  const u = box.querySelector(".gunmapped");
  if (st.shown >= st.list.length && u && u.getBoundingClientRect().bottom < end())
    gMoreUnmapped(box).then(() => gObserve(box), () => {});
}
drawer.addEventListener("scroll", () => { for (const b of drawer.querySelectorAll(".gsheet")) if (b._g?.expanded) gObserve(b); }, { passive: true });

function gInfo(st) {
  const g = st.sel;
  if (!g) return `<span class="ghint">Click a glyph to copy it. Its codes show here.</span>`;
  const ch = String.fromCodePoint(g[0]), u = hex(g[0]);
  const codes = [["Character", ch], ["Unicode", "U+" + u], ["HTML", `&#x${u};`]];
  codes.push(["Windows", g[3] ? "Alt+" + g[3] : `${u} Alt+X`]);   // no Alt code: Word/WordPad turn hex + Alt+X into the character
  codes.push(["Linux", "Ctrl+Shift+U " + u.toLowerCase()]);
  return `<span class="gbig" style="font-family:'${st.fam}'">${esc(ch)}</span>
    <div class="gcodes"><div class="gname">${esc(g[1] || "No Unicode name")}</div>
      ${codes.map(([k, v]) => `<button class="gcode" data-g="copy" data-v="${esc(v)}" title="Copy ${k}"><small>${k}</small> ${esc(v)}</button>`).join("")}</div>`;
}

async function gCopy(text, label) {
  try { await navigator.clipboard.writeText(text); }
  catch {                                 // older browsers / no clipboard permission: the selection way
    const ta = Object.assign(document.createElement("textarea"), { value: text });
    ta.style.cssText = "position:fixed;opacity:0";
    document.body.append(ta); ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    if (!ok) return toast("Couldn't reach the clipboard");
  }
  toast(`Copied ${label}`);
}

drawer.addEventListener("click", e => {
  const t = e.target.closest("[data-g]"), box = e.target.closest(".gsheet");
  if (!t || !box?._g) return;
  e.stopPropagation();
  const st = box._g;
  switch (t.dataset.g) {
    case "expand": st.expanded = !st.expanded; st.filter = "all"; st.q = ""; gRender(box); break;
    case "filter": st.filter = t.dataset.k; gRender(box); break;
    case "pick": {
      const cp = +t.dataset.cp;
      st.sel = st.d.chars.find(g => g[0] === cp);
      for (const c of box.querySelectorAll(".gl.on")) c.classList.remove("on");
      t.classList.add("on");
      box.querySelector(".ginfo").innerHTML = gInfo(st);
      const ch = String.fromCodePoint(cp);
      gCopy(ch, `${ch}  ·  U+${hex(cp)}${st.sel[3] ? "  ·  Alt+" + st.sel[3] : ""}`);
      break;
    }
    case "copy": gCopy(t.dataset.v, t.dataset.v); break;
  }
}, true);
let gqTimer;
drawer.addEventListener("input", e => {
  if (e.target.dataset.g !== "q") return;
  const box = e.target.closest(".gsheet"), st = box._g;
  clearTimeout(gqTimer);
  gqTimer = setTimeout(() => {
    st.q = e.target.value.trim();
    const pos = e.target.selectionStart;
    gRender(box);
    const inp = box.querySelector('[data-g="q"]');
    inp.focus(); inp.setSelectionRange(pos, pos);
  }, 200);
});

drawer.addEventListener("click", async e => {
  const b = e.target.closest("[data-a]");
  if (!b) return;
  const fam = S.families.get(S.sel), faceEl = e.target.closest(".face");
  const x = faceEl && fam.faces.find(f => f.id === +faceEl.dataset.id);
  switch (b.dataset.a) {
    case "close": closeDrawer(); break;
    case "toggle": await setEnabled([x.path], !isOn(x)); break;
    case "enable-all": await setEnabled(fam.faces.map(f => f.path), true); break;
    case "disable-all": await setEnabled(fam.faces.map(f => f.path), false); break;
    case "reveal": post("/api/reveal", { path: (x || S.faces.find(f => f.id === +b.dataset.id)).path }); break;
    case "copy": await navigator.clipboard.writeText(x.path); toast("Path copied"); break;
    case "info": D.face = x.id; for (const f of drawer.querySelectorAll(".face")) f.classList.toggle("sel", f === faceEl); showInfo(x.id); break;
    case "similar": S.similarTo = rep(fam.faces); setSort("similar"); grid.scrollTop = 0; apply(); break;
  }
});
drawer.addEventListener("change", e => {
  if (e.target.dataset.a === "dsize") { D.size = +e.target.value; renderDrawer(); }
});

function closeDrawer() { S.sel = null; drawer.hidden = true; repaint(); }

async function setEnabled(paths, on) {
  const r = await post("/api/enable", { paths, on });
  if (r.error) return toast(r.error);
  S.enabled = new Set(r.enabled);
  toast(`${on ? "Enabled" : "Disabled"} ${paths.length} font file${paths.length > 1 ? "s" : ""}`);
  renderDrawer(); repaint();
  if (S.f.flags.has("enabled")) apply();
}

// ------------------------------------------------------------------ top bar

function setSort(v) {
  S.sort = v;
  $('#sort option[value="match"]').hidden = S.qmode !== "tags";
  const opt = $('#sort option[value="similar"]');
  opt.hidden = v !== "similar";
  if (v === "similar") opt.textContent = "Similar to " + S.similarTo.family;
  $("#sort").value = v;
}

let qTimer, tTimer;
$("#q").addEventListener("input", e => {
  clearTimeout(qTimer);
  if (S.qmode === "tags") qTimer = setTimeout(() => tagSearch(e.target.value), 350);
  else qTimer = setTimeout(() => { S.f.q = e.target.value; apply(); }, 120);
});

// ------------------------------------------------------------------ style tags search

const Q_PLACEHOLDER = { names: "Search names   ·   in:folder   ( / )", tags: "Describe a look   ·   groovy 70s, horror, wedding" };

async function tagSearch(q) {
  q = q.trim();
  S.tagQ = q;
  if (!q) { S.tagRank = null; return apply(); }
  $("#q").parentElement.classList.add("busy");
  try {
    const r = await (await fetch("/api/tags?q=" + encodeURIComponent(q))).json();
    if (S.tagQ !== q || S.qmode !== "tags") return;      // a newer query or a mode switch won
    if (r.error) { toast(r.error.replace(/^\w+: /, "")); return; }
    S.tagRank = new Map(r.hits);
    grid.scrollTop = 0;
    apply();
  } finally { $("#q").parentElement.classList.remove("busy"); }
}

function setQmode(m) {
  if (m === S.qmode) return;
  S.qmode = m;
  $("#qmode").value = m;
  const q = $("#q");
  q.placeholder = Q_PLACEHOLDER[m];
  if (m === "tags") {
    S.sortBefore = S.sort;
    post("/api/tags", { action: "warm" }).then(tagsShow);
    S.f.q = "";
    setSort("match");
    tagSearch(q.value);
  } else {
    S.tagRank = null; S.tagQ = "";
    setSort(S.sortBefore === "match" ? "name" : S.sortBefore);
    S.f.q = q.value;
    apply();
  }
  q.focus();
}
$("#qmode").addEventListener("change", e => setQmode(e.target.value));

// tag index state: Tags option label, placeholder progress, settings section; polls while the tagger runs
let tagsPolling = false;
function tagsShow(t) {
  if (!t || t.error && !t.available) return;
  S.tags = t;
  const opt = $('#qmode option[value="tags"]');
  opt.disabled = !t.searchable;
  opt.textContent = !t.available ? "Tags (not installed)" : !t.searchable ? "Tags (indexing…)" : "Tags";
  const part = t.running && t.total ? `   ·   ${t.done.toLocaleString()} / ${t.total.toLocaleString()} indexed` : "";
  Q_PLACEHOLDER.tags = "Describe a look   ·   groovy 70s, horror, wedding" + part;
  if (S.qmode === "tags") $("#q").placeholder = Q_PLACEHOLDER.tags;
  if (dlg.open) tagsSettings(t);
  if (t.running && !tagsPolling) tagsPoll();
}
async function tagsPoll() {
  tagsPolling = true;
  try {
    while (true) {
      await new Promise(r => setTimeout(r, 2000));
      const t = await (await fetch("/api/tags")).json();
      tagsShow(t);
      if (!t.running) { if (S.qmode === "tags" && S.tagQ) tagSearch(S.tagQ); break; }
    }
  } finally { tagsPolling = false; }
}
function tagsSettings(t) {
  for (const b of $("#set-tags").children) b.classList.toggle("on", b.dataset.v === (SET_TAGS ?? t.mode));
  const fast = $("#tags-fast"), bar = $("#tags-bar"), lines = [];
  fast.disabled = !t.available || (t.running && t.fast) || (!t.running && t.total && t.done >= t.total);
  fast.textContent = t.running && t.fast ? "Running at full speed…" : "Full speed now";
  bar.hidden = !t.running;
  if (t.running) bar.firstChild.style.width = (t.total ? Math.max(2, 100 * t.done / t.total) : 2) + "%";
  if (!t.available) lines.push("Not installed: run install.sh again and turn on Style search (about 3 GB download).");
  else {
    if (t.total) lines.push(`${t.done.toLocaleString()} of ${t.total.toLocaleString()} styles indexed` +
      (t.running ? (t.fast ? " · full speed" : " · in the background") : ""));
    else lines.push(t.running ? "Starting…" : "Nothing indexed yet");
    if (t.error) lines.push(`<span class="err">${esc(t.error)}</span>`);
  }
  $("#tags-state").innerHTML = lines.map(l => l.startsWith("<") ? l : esc(l)).join("<br>");
}
let SET_TAGS = null;   // unsaved Background/Off choice in the dialog
$("#set-tags").addEventListener("click", e => {
  const b = e.target.closest("button");
  if (!b) return;
  SET_TAGS = b.dataset.v;
  if (S.tags) tagsSettings(S.tags);
});
$("#tags-fast").addEventListener("click", async () => {
  const r = await post("/api/tags", { action: "fast" });
  if (r.error) return toast(r.error.replace(/^\w+: /, ""));
  tagsShow(r);
});
$("#text").addEventListener("input", e => {
  clearTimeout(tTimer);
  tTimer = setTimeout(() => { S.text = e.target.value.trim(); layout(true); if (S.sel) renderDrawer(); }, 300);
});
$("#size").addEventListener("input", e => { S.th = +e.target.value; layout(true); });
$("#sort").addEventListener("change", e => { setSort(e.target.value); apply(); });
$("#view").addEventListener("click", e => {
  const b = e.target.closest("button");
  if (!b) return;
  S.view = b.dataset.v;
  for (const x of $("#view").children) x.classList.toggle("on", x === b);
  grid.scrollTop = 0;
  apply();
});
$("#folder").addEventListener("change", e => { S.f.folder = e.target.value; grid.scrollTop = 0; apply(); });
$("#rescan").addEventListener("click", async () => status(await post("/api/rescan", {})));

document.addEventListener("keydown", e => {
  if (e.key === "/" && !/INPUT|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); $("#q").focus(); }
  if ($("#settings").open) return;
  if (e.key === "Escape") { if (/INPUT/.test(document.activeElement.tagName)) document.activeElement.blur(); else closeDrawer(); }
});

// ------------------------------------------------------------------ status, theme, helpers

let polling = false;
async function status(st) {
  if (st.tags) tagsShow(st.tags);
  const bar = $("#progress"), btn = $("#rescan");
  bar.classList.toggle("on", !!st.running);
  btn.disabled = !!st.running;
  if (st.running) {
    btn.textContent = st.state === "scanning" ? "Scanning…" : `Indexing ${st.done}/${st.total}`;
    bar.firstChild.style.width = st.total ? (100 * st.done / st.total) + "%" : "5%";
    if (!polling) {
      polling = true;
      while (true) {
        await new Promise(r => setTimeout(r, 1000));
        const s = await (await fetch("/api/status")).json();
        if (!s.running) { polling = false; btn.textContent = "Rescan"; bar.classList.remove("on"); btn.disabled = false;
          if (s.total || s.gone) { toast(`Indexed ${s.total} new or changed files` + (s.gone ? `, ${s.gone} gone` : "")); await load(); } return; }
        btn.textContent = s.state === "scanning" ? "Scanning…" : `Indexing ${s.done}/${s.total}`;
        bar.firstChild.style.width = s.total ? (100 * s.done / s.total) + "%" : "5%";
      }
    }
  } else btn.textContent = "Rescan";
}

// ------------------------------------------------------------------ settings

const dlg = $("#settings");
let SET = null, gfPolling = false, rootTimer;

async function openSettings() {
  SET = await (await fetch("/api/settings")).json();
  $("#set-lib").value = SET.library;
  $("#set-groot").value = SET.google_root;
  gfShow(SET);
  SET_TAGS = null;
  tagsShow(SET.tags);
  tagsSettings(SET.tags);
  dlg.returnValue = "";
  dlg.showModal();
  if (SET.job.running) gfPoll();
}

function gfShow(st) {
  const g = st.google, j = st.job, btn = $("#gf-btn"), bar = $("#gf-bar");
  const lines = [];
  if (j.running) {
    btn.disabled = true;
    btn.textContent = j.action === "update" ? "Updating…" : "Downloading…";
    bar.hidden = false;
    bar.firstChild.style.width = Math.max(2, j.percent) + "%";
    lines.push(`${j.phase || "Starting"} · ${Math.round(j.percent)}%`);
  } else {
    bar.hidden = true;
    btn.disabled = g.exists && !g.git;
    btn.textContent = g.git ? "Update" : "Download Google Fonts";
    if (g.git) lines.push(`Installed in ${g.path}` + (g.updated ? ` · newest change ${new Date(g.updated * 1000).toLocaleDateString()}` : ""));
    else if (g.exists) lines.push(`${g.path} exists but is not a git clone`);
    else lines.push(`Will be cloned to ${g.path}`);
    if (j.ok === false) lines.push(`<span class="err">Failed: ${esc(j.msg)}</span>`);
    else if (j.ok && j.msg) lines.push(j.msg + (g.in_library && j.msg === "Done" ? " · rescanning the library" : ""));
    if (!g.in_library) lines.push(`<span class="warn">Not inside the fonts location, so these won't be indexed</span>`);
  }
  $("#gf-state").innerHTML = lines.map(l => l.startsWith("<") ? l : esc(l)).join("<br>");
}

async function gfRefresh() {
  const root = $("#set-groot").value.trim();
  const lib = $("#set-lib").value.trim();
  SET = await (await fetch(`/api/settings?root=${encodeURIComponent(root)}&lib=${encodeURIComponent(lib)}`)).json();
  gfShow(SET);
}

async function gfPoll() {
  if (gfPolling) return;
  gfPolling = true;
  try {
    while (true) {
      await new Promise(r => setTimeout(r, 700));
      const j = await (await fetch("/api/google")).json();
      SET.job = j;
      gfShow(SET);
      if (!j.running) break;
    }
    await gfRefresh();
    if (SET.job.ok) {
      toast(SET.job.action === "update" ? "Google Fonts updated" : "Google Fonts downloaded");
      status(await (await fetch("/api/status")).json());
    }
  } finally { gfPolling = false; }
}

dlg.addEventListener("click", async e => {
  const pick = e.target.closest("[data-pick]");
  if (pick) {
    const input = $("#" + pick.dataset.pick);
    pick.disabled = true;
    try {
      const r = await post("/api/pick", { start: input.value, title: pick.dataset.pick === "set-lib" ? "Fonts location" : "Google Fonts root" });
      if (r.error) toast(r.error);
      else if (r.path) { input.value = r.path; if (input.id === "set-groot") gfRefresh(); }
    } finally { pick.disabled = false; }
    return;
  }
  if (e.target.closest("#set-same")) { $("#set-groot").value = $("#set-lib").value; gfRefresh(); return; }
  if (e.target.closest("#gf-btn")) {
    const r = await post("/api/google", { root: $("#set-groot").value.trim() });
    if (r.error) return toast(r.error.replace(/^\w+: /, ""));
    SET = r; gfShow(SET); gfPoll();
  }
});
$("#set-groot").addEventListener("input", () => { clearTimeout(rootTimer); rootTimer = setTimeout(gfRefresh, 400); });
$("#set-lib").addEventListener("input", () => { clearTimeout(rootTimer); rootTimer = setTimeout(gfRefresh, 400); });

dlg.addEventListener("close", async () => {
  if (dlg.returnValue !== "save") return;
  const body = { library: $("#set-lib").value.trim(), google_root: $("#set-groot").value.trim() };
  if (SET_TAGS) body.tags = SET_TAGS;
  const r = await post("/api/settings", body);
  if (r.tags) tagsShow(r.tags);
  if (r.error) { toast(r.error.replace(/^\w+: /, "")); return openSettings(); }
  if (r.rescan) {
    toast("Fonts location changed: rescanning");
    S.f.folder = ""; S.sel = null; drawer.hidden = true;
    status(await (await fetch("/api/status")).json());
  } else toast("Settings saved");
});
$("#gear").addEventListener("click", openSettings);

async function post(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return r.json();
}

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.hidden = true, 2200);
}

async function theme() {
  try {
    const { mode, colors: c } = await (await fetch("/api/theme")).json();
    if (!c) return;
    const root = document.documentElement;
    root.dataset.theme = mode;
    const map = { bg: "surface", s1: "surface_container_low", s2: "surface_container", s3: "surface_container_high",
      s4: "surface_container_highest", on: "on_surface", "on-var": "on_surface_variant", outline: "outline",
      "outline-var": "outline_variant", primary: "primary", "on-primary": "on_primary", pc: "primary_container",
      "on-pc": "on_primary_container", sc: "secondary_container", "on-sc": "on_secondary_container", err: "error" };
    for (const [k, v] of Object.entries(map)) if (c[v]) root.style.setProperty("--" + k, c[v]);
    $("#favicon").href = "/icon";
    const ink = getComputedStyle(root).getPropertyValue("--on").trim();
    const svg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 12'><path d='M2.5 4.5 6 8l3.5-3.5' fill='none' stroke='${ink}' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'/></svg>`;
    root.style.setProperty("--chev-img", `url("data:image/svg+xml,${encodeURIComponent(svg)}")`);
  } catch { /* keep defaults */ }
}

window.addEventListener("focus", theme);
theme();
load();
