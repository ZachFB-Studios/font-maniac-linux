"""Shared paths, database, font metadata, rendering and shape measurement."""
import hashlib
import io
import json
import os
import re
import sqlite3
from pathlib import Path

from fontTools.ttLib import TTCollection, TTFont
from PIL import Image, ImageDraw, ImageFont

APP = Path(__file__).resolve().parent
CACHE = APP / "cache"
DB_PATH = CACHE / "index.sqlite"
STATUS = CACHE / "status.json"
CONFIG = APP / "config.json"

DEFAULTS = {
    "library": str(Path.home() / "Fonts"),
    "enable_dir": str(Path.home() / ".local/share/fonts/FontManiac"),
    "port": 8747,
    "browser": "",           # empty = first Chromium-family browser found (see run.sh)
}

FONT_EXT = {".ttf", ".otf", ".ttc", ".otc", ".woff", ".woff2"}
SAMPLE = "Handgloves"
THUMB_SIZE = 56          # font size of index thumbnails (px)
THUMB_H = 80             # thumbnail height; baseline sits at 0.75 * height

# Representative character per script: (key, label, char)
SCRIPTS = [
    ("latin", "Latin", "a"), ("latinext", "Latin Ext", "ł"), ("viet", "Vietnamese", "ự"),
    ("cyrillic", "Cyrillic", "ж"), ("greek", "Greek", "λ"), ("arabic", "Arabic", "ب"),
    ("hebrew", "Hebrew", "א"), ("devanagari", "Devanagari", "क"), ("thai", "Thai", "ก"),
    ("cjk", "CJK", "永"), ("kana", "Japanese", "あ"), ("hangul", "Korean", "한"),
]
SCRIPT_SAMPLES = {"cjk": "永東国字", "kana": "あア日本", "hangul": "한국어", "arabic": "أبجد",
                  "hebrew": "אבגד", "cyrillic": "Жжйф", "greek": "Λλωσ", "devanagari": "कखग", "thai": "กขค"}

NAME_RULES = [  # checked in order against the family + style name
    ("mono", r"\b(mono|monospaced?|code|typewriter)\b"),
    ("symbol", r"dingbat|symbols?\b|\bicons?\b|emoji|ornament|wingding|webding|\bawesome\b"),
    ("blackletter", r"blackletter|fraktur|textura|schwabacher|old ?english"),
    ("script", r"script|\bhand(writ\w*)?\b|\bbrush|callig|signature|\bmarker\b|cursive|\bpen\b|chalk|crayon"),
    ("slab", r"\bslab\b|slab"),
    ("sans", r"\bsans\b|grotesk|grotesque|gothic sans"),
    ("serif", r"\bserif\b|antiqua"),
]


def config():
    cfg = dict(DEFAULTS)
    if CONFIG.exists():
        cfg.update(json.loads(CONFIG.read_text()))
    return cfg


def connect():
    CACHE.mkdir(exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS files(
        path TEXT PRIMARY KEY, size INT, mtime REAL, sha1 TEXT, err TEXT, added REAL);
    CREATE TABLE IF NOT EXISTS faces(
        id INTEGER PRIMARY KEY, path TEXT, idx INT,
        family TEXT, style TEXT, full TEXT, ps TEXT,
        weight INT, width INT, italic INT, mono INT, variable INT, axes TEXT,
        panose TEXT, category TEXT, cat_src TEXT,
        serif REAL, contrast REAL, xh REAL, wr REAL, stem REAL, slant REAL,
        scripts TEXT, glyphs INT, version TEXT, vendor TEXT, designer TEXT,
        tw INT, thumb BLOB);
    CREATE INDEX IF NOT EXISTS faces_path ON faces(path);
    """)
    return db


# ---------------------------------------------------------------- metadata

def _name(tt, *ids):
    if "name" not in tt:
        return ""
    names = tt["name"]
    for nid in ids:
        rec = names.getName(nid, 3, 1, 0x409) or names.getName(nid, 1, 0, 0) or names.getDebugName(nid)
        if rec:
            s = str(rec).strip().strip("\x00")
            if s:
                return s
    return ""


def metadata(tt):
    os2 = tt["OS/2"] if "OS/2" in tt else None
    cmap = tt.getBestCmap() or {}
    family = _name(tt, 16, 1) or "Unknown"
    style = _name(tt, 17, 2) or "Regular"
    pan = os2.panose if os2 is not None else None
    panose = [pan.bFamilyType, pan.bSerifStyle, pan.bWeight, pan.bProportion, pan.bContrast] if pan else [0] * 5
    weight = os2.usWeightClass if os2 is not None else 400
    if weight and weight < 10:            # some old fonts store 1-9
        weight *= 100
    italic = bool(os2 and os2.fsSelection & 1) or ("post" in tt and tt["post"].italicAngle != 0) \
        or bool(re.search(r"italic|oblique", style, re.I))
    mono = bool("post" in tt and tt["post"].isFixedPitch) or panose[3] == 9
    axes = ""
    if "fvar" in tt:
        axes = ",".join(f"{a.axisTag}:{a.minValue:g}-{a.maxValue:g}" for a in tt["fvar"].axes)
    return {
        "family": family, "style": style, "full": _name(tt, 4), "ps": _name(tt, 6),
        "weight": weight or 400, "width": (os2.usWidthClass if os2 is not None else 5) or 5,
        "italic": int(italic), "mono": int(mono), "variable": int("fvar" in tt), "axes": axes,
        "panose": ",".join(map(str, panose)),
        "scripts": ",".join(k for k, _, ch in SCRIPTS if ord(ch) in cmap),
        "glyphs": len(tt.getGlyphOrder()),
        "version": _name(tt, 5).replace("Version ", ""),
        "vendor": (os2.achVendID.strip("\x00 ") if os2 is not None else ""),
        "designer": _name(tt, 9) or _name(tt, 8),
        "_cmap": cmap,
    }


def sample_text(cmap, scripts):
    if all(ord(c) in cmap for c in SAMPLE):
        return SAMPLE
    for key in ("cyrillic", "greek", "cjk", "kana", "hangul", "arabic", "hebrew", "devanagari", "thai"):
        if key in scripts:
            text = "".join(c for c in SCRIPT_SAMPLES[key] if ord(c) in cmap)
            if text:
                return text
    chars = [chr(c) for c in sorted(cmap) if c > 0x20 and chr(c).isprintable() and not 0xE000 <= c <= 0xF8FF]
    if not chars:
        chars = [chr(c) for c in sorted(cmap) if c > 0x20]
    return "".join(chars[:10])


# ---------------------------------------------------------------- rendering

def load_pil(data, idx=0, size=THUMB_SIZE):
    return ImageFont.truetype(io.BytesIO(data), size=size, index=idx)


def render(font, text, height=THUMB_H, max_w=900):
    """Render text as an alpha mask (white + alpha) WebP. Returns (bytes, width)."""
    size = font.size
    base = round(height * 0.75)
    try:
        length = font.getlength(text)
    except Exception:
        length = size * len(text)
    w = int(min(max(length + size * 0.4, 8), max_w * 2))
    img = Image.new("L", (w, height), 0)
    ImageDraw.Draw(img).text((size * 0.1, base), text, font=font, fill=255, anchor="ls")
    bbox = img.getbbox()
    right = bbox[2] + 2 if bbox else 8
    img = img.crop((0, 0, max(right, 8), height))
    if img.width > max_w:
        img = img.crop((0, 0, max_w, height))
    rgba = Image.merge("RGBA", (Image.new("L", img.size, 255),) * 3 + (img,))
    buf = io.BytesIO()
    rgba.save(buf, "WEBP", lossless=True, method=1)
    return buf.getvalue(), img.width


def font_for_size(data, idx, size):
    return load_pil(data, idx, size)


# ---------------------------------------------------------------- shape

def _glyph(font, ch):
    """Bitmap (list of rows of bools as bytes) of one glyph, cropped to ink."""
    img = Image.new("L", (font.size * 2, font.size * 2), 0)
    ImageDraw.Draw(img).text((font.size * 0.5, font.size * 1.5), ch, font=font, fill=255, anchor="ls")
    box = img.getbbox()
    if not box:
        return None
    img = img.crop(box).point(lambda v: 255 if v > 110 else 0)
    w, h = img.size
    px = img.tobytes()
    return [px[r * w:(r + 1) * w] for r in range(h)], w, h


def _row_extent(row):
    first = row.find(b"\xff")
    if first < 0:
        return 0
    return row.rfind(b"\xff") - first + 1


def _runs(seq):
    runs, n = [], 0
    for v in seq:
        if v:
            n += 1
        elif n:
            runs.append(n)
            n = 0
    if n:
        runs.append(n)
    return runs


def _serif_ratio(g, left_part=1.0):
    rows, w, h = g
    cols = max(1, int(w * left_part))
    widths = [_row_extent(r[:cols]) for r in rows]
    mid = sorted(widths[int(h * .3):int(h * .7)] or widths)
    stem = mid[len(mid) // 2] or 1
    edge = max(1, h // 10)
    return max(max(widths[:edge]), max(widths[-edge:])) / stem, stem


def shape(data, idx, cmap):
    """Measured shape metrics; all None when the font has no basic Latin."""
    out = dict(serif=None, contrast=None, xh=None, wr=None, stem=None, slant=None)
    if not all(ord(c) in cmap for c in "lHoxn"):
        return out
    font = load_pil(data, idx, 160)
    gl, gH, go, gx = (_glyph(font, c) for c in "lHox")
    if not (gl and gH and go and gx):
        return out
    r1, stem = _serif_ratio(gl)
    r2, _ = _serif_ratio(gH, 0.4)
    out["serif"] = round(min(r1, r2), 3)
    cap = gH[2]
    out["stem"] = round(stem / cap, 4)
    out["xh"] = round(gx[2] / cap, 3)
    # slant: centre of the 'l' stem at the top vs the bottom quarter
    rows, w, h = gl
    def centre(r):
        a, b = r.find(b"\xff"), r.rfind(b"\xff")
        return (a + b) / 2 if a >= 0 else None
    top, bot = centre(rows[h // 4]), centre(rows[h * 3 // 4])
    if top is not None and bot is not None:
        import math
        out["slant"] = round(math.degrees(math.atan2(top - bot, h / 2)), 1)
    # contrast: side stroke of 'o' at mid height vs top stroke at mid width
    rows, w, h = go
    side = _runs(c == 255 for c in rows[h // 2])
    vert = _runs(rows[r][w // 2] == 255 for r in range(h))
    if side and vert:
        thick, thin = max(side[0], 1), max(min(vert[0], vert[-1]), 1)
        out["contrast"] = round(thick / thin, 2)
    try:
        out["wr"] = round(font.getlength("abcdefghijklmnopqrstuvwxyz") / 26 / cap, 3)
    except Exception:
        pass
    return out


def classify(meta, sh, cmap):
    text = f"{meta['family']} {meta['style']}".lower()
    for cat, rx in NAME_RULES:
        if re.search(rx, text):
            return cat, "name"
    if "latin" not in meta["scripts"]:
        if any(s in meta["scripts"] for s in ("cjk", "kana", "hangul", "arabic", "hebrew", "devanagari", "thai")):
            return "world", "cmap"
        return "symbol", "cmap"
    if meta["mono"]:
        return "mono", "meta"
    pan = [int(x) for x in meta["panose"].split(",")]
    if pan[0] == 3:
        return "script", "panose"
    if pan[0] == 4:
        return "display", "panose"
    if pan[0] == 5:
        return "symbol", "panose"
    if pan[0] == 2 and pan[1] > 1:
        if pan[1] in (11, 12, 13, 14, 15):
            return "sans", "panose"
        if pan[1] == 6:
            return "slab", "panose"
        return "serif", "panose"
    if sh["serif"] is None:
        return "other", "none"
    if sh["slant"] is not None and abs(sh["slant"]) > 18 and (sh["contrast"] or 1) > 2.5:
        return "script", "shape"
    if sh["serif"] > 1.45:
        return ("slab" if (sh["contrast"] or 9) < 1.6 else "serif"), "shape"
    return "sans", "shape"


# ---------------------------------------------------------------- one file

def open_faces(data, ext):
    if ext in (".ttc", ".otc"):
        return list(enumerate(TTCollection(io.BytesIO(data), lazy=True).fonts))
    return [(0, TTFont(io.BytesIO(data), lazy=True))]


def process_file(path):
    """Index one font file. Returns (path, size, mtime, sha1, err, faces)."""
    st = os.stat(path)
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        return path, st.st_size, st.st_mtime, None, f"read: {e}", []
    sha1 = hashlib.sha1(data).hexdigest()
    ext = Path(path).suffix.lower()
    faces = []
    try:
        for idx, tt in open_faces(data, ext):
            meta = metadata(tt)
            cmap = meta.pop("_cmap")
            if ext in (".woff", ".woff2"):        # PIL can't read woff: decompress to sfnt
                buf = io.BytesIO()
                tt.flavor = None
                tt.save(buf)
                pdata, pidx = buf.getvalue(), 0
            else:
                pdata, pidx = data, idx
            try:
                sh = shape(pdata, pidx, cmap)
            except Exception:
                sh = dict(serif=None, contrast=None, xh=None, wr=None, stem=None, slant=None)
            cat, src = classify(meta, sh, cmap)
            thumb, tw = render(load_pil(pdata, pidx), sample_text(cmap, meta["scripts"]))
            faces.append({**meta, **sh, "idx": idx, "category": cat, "cat_src": src, "thumb": thumb, "tw": tw})
    except Exception as e:
        return path, st.st_size, st.st_mtime, sha1, f"{type(e).__name__}: {e}"[:300], []
    return path, st.st_size, st.st_mtime, sha1, None, faces


def pil_data(path, idx):
    """Bytes + face index PIL can load (woff decompressed)."""
    data = Path(path).read_bytes()
    ext = Path(path).suffix.lower()
    if ext in (".woff", ".woff2"):
        tt = TTFont(io.BytesIO(data))
        tt.flavor = None
        buf = io.BytesIO()
        tt.save(buf)
        return buf.getvalue(), 0
    return data, idx
