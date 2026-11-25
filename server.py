#!/usr/bin/env python3
"""Local web server for the font browser. Usage: server.py [--port N] [--no-scan]"""
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import fontcore as fc
import tagger as tg

CFG = fc.config()
LIB = CFG["library"].rstrip("/") or "/"
ENABLE_DIR = Path(CFG["enable_dir"])
WEB = fc.APP / "web"
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
        ".png": "image/png"}

_local = threading.local()
_lock = threading.Lock()
_indexer = None
_tagger = None
_tagger_fast = False
_tag_seen = None          # status.json mtime the background tagger last ran after
_fc_timer = None
GOOGLE_URL = "https://github.com/google/fonts"
_git = {"running": False, "action": "", "phase": "", "percent": 0, "msg": "", "ok": None}


def db():
    if not hasattr(_local, "db"):
        _local.db = fc.connect()
    return _local.db


class LRU(OrderedDict):
    def __init__(self, cap):
        super().__init__()
        self.cap = cap

    def get_or(self, key, make):
        with _lock:
            if key in self:
                self.move_to_end(key)
                return self[key]
        val = make()
        with _lock:
            self[key] = val
            while len(self) > self.cap:
                self.popitem(last=False)
        return val


FONT_DATA = LRU(200)     # face id -> (bytes, idx)
PIL_FONTS = LRU(400)     # (id, size) -> ImageFont
RENDERS = LRU(6000)      # (id, text, size, h) -> (webp, width)
SAMPLES = LRU(20000)     # face id -> default sample text
GF_META = {}             # font dir -> [categories, license, designer] from Google Fonts METADATA.pb, or None
_GF_FIELD = re.compile(r'^(category|license|designer): "(.*)"$', re.M)


# ---------------------------------------------------------------- data

def gf_meta(d):
    """Google Fonts METADATA.pb for a font folder (same dir or its parent, e.g. ofl/x/static/)."""
    if d in GF_META:
        return GF_META[d]
    info = None
    if "/Google/" in d + "/":
        for cand in (d, os.path.dirname(d)):
            try:
                text = Path(cand, "METADATA.pb").read_text(errors="replace")
            except OSError:
                continue
            top = text.split("\nfonts {", 1)[0]     # only the family-level fields
            cats, lic, des = [], "", ""
            for k, v in _GF_FIELD.findall(top):
                if k == "category":
                    cats.append(v.lower())
                elif k == "license":
                    lic = lic or v
                else:
                    des = des or v
            info = [cats, lic, des]
            break
    GF_META[d] = info
    return info


def fonts_payload():
    rows = db().execute("""SELECT f.id, f.path, f.idx, f.family, f.style, f.weight, f.width, f.italic, f.mono,
        f.variable, f.category, f.serif, f.contrast, f.xh, f.wr, f.stem, f.slant, f.scripts, f.tw,
        substr(x.sha1, 1, 12), x.added FROM faces f JOIN files x ON x.path = f.path ORDER BY f.family, f.weight""").fetchall()
    dirs, dir_ix, faces = [], {}, []
    for r in rows:
        d, name = os.path.split(r[1])
        rel = os.path.relpath(d, LIB)
        if rel not in dir_ix:
            dir_ix[rel] = len(dirs)
            dirs.append(rel)
        faces.append([r[0], dir_ix[rel], name, *r[2:]])
    gf = [gf_meta(os.path.normpath(os.path.join(LIB, rel))) for rel in dirs]
    return {"library": LIB, "dirs": dirs, "gf": gf, "faces": faces, "enabled": enabled_paths(),
            "status": read_status()}


def read_status():
    try:
        st = json.loads(fc.STATUS.read_text())
    except (OSError, ValueError):
        st = {"state": "none"}
    st["running"] = _indexer is not None and _indexer.poll() is None
    st["tags"] = tags_info()
    return st


def face_row(fid):
    r = db().execute("SELECT * FROM faces WHERE id=?", (fid,)).fetchone()
    if not r:
        return None
    cols = [c[0] for c in db().execute("SELECT * FROM faces LIMIT 0").description]
    return dict(zip(cols, r))


def face_detail(fid):
    f = face_row(fid)
    if not f:
        return None
    f.pop("thumb", None)
    x = db().execute("SELECT size, sha1, added FROM files WHERE path=?", (f["path"],)).fetchone()
    f.update(size=x[0], sha1=x[1], added=x[2])
    f["twins"] = [p for (p,) in db().execute("SELECT path FROM files WHERE sha1=? AND path<>?", (x[1], f["path"]))]
    return f


def font_data(fid):
    def load():
        r = db().execute("SELECT path, idx FROM faces WHERE id=?", (fid,)).fetchone()
        return fc.pil_data(*r)
    return FONT_DATA.get_or(fid, load)


def sample(fid):
    def make():
        data, idx = font_data(fid)
        f = db().execute("SELECT scripts FROM faces WHERE id=?", (fid,)).fetchone()
        tt = fc.open_faces(data, ".ttc" if data[:4] == b"ttcf" else ".ttf")[idx if data[:4] == b"ttcf" else 0][1]
        return fc.sample_text(tt.getBestCmap() or {}, f[0])
    return SAMPLES.get_or(fid, make)


def render(fid, text, size, h):
    def make():
        font = PIL_FONTS.get_or((fid, size), lambda: fc.load_pil(*font_data(fid), size))
        return fc.render(font, text, h, max_w=2400)
    return RENDERS.get_or((fid, text, size, h), make)


# ---------------------------------------------------------------- enable / disable

def enabled_links():
    out = {}
    if ENABLE_DIR.is_dir():
        for e in os.scandir(ENABLE_DIR):
            if e.is_symlink():
                out[os.readlink(e.path)] = e.path
    return out


def enabled_paths():
    return sorted(enabled_links())


def set_enabled(paths, on):
    ENABLE_DIR.mkdir(parents=True, exist_ok=True)
    links = enabled_links()
    for p in paths:
        if on and (not p.startswith(LIB.rstrip("/") + "/") or not os.path.isfile(p)):
            continue
        if on and p not in links:
            name = os.path.basename(p)
            dest = ENABLE_DIR / name
            if os.path.lexists(dest):
                stem, ext = os.path.splitext(name)
                dest = ENABLE_DIR / f"{stem}~{hashlib.sha1(p.encode()).hexdigest()[:6]}{ext}"
            os.symlink(p, dest)
        elif not on and p in links and os.path.islink(links[p]):
            os.unlink(links[p])          # only our own symlink; the font file is never touched
    refresh_fontconfig()
    return enabled_paths()


def refresh_fontconfig():
    global _fc_timer
    if _fc_timer:
        _fc_timer.cancel()
    _fc_timer = threading.Timer(0.8, lambda: subprocess.Popen(["fc-cache"], stdout=subprocess.DEVNULL,
                                                              stderr=subprocess.DEVNULL))
    _fc_timer.start()


# ---------------------------------------------------------------- misc actions

def reveal(path):
    uri = "file://" + quote(path)
    r = subprocess.run(["gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1",
                        "--object-path", "/org/freedesktop/FileManager1",
                        "--method", "org.freedesktop.FileManager1.ShowItems", f"['{uri}']", ""],
                       capture_output=True, timeout=5)
    if r.returncode:                     # no FileManager1 service: open the folder instead
        subprocess.Popen(["xdg-open", os.path.dirname(path)], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def start_index(full=False):
    global _indexer
    if _indexer is not None and _indexer.poll() is None:
        return
    _indexer = subprocess.Popen([sys.executable, str(fc.APP / "index.py")] + (["--full"] if full else []),
                                cwd=fc.APP, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------- style tags (second index, see tagger.py)

TAGS = {"model": None, "tok": None, "torch": None, "loading": False, "err": None, "used": 0,
        "stamp": None, "M": None, "faces": None, "qcache": OrderedDict()}
_tags_lock = threading.Lock()
PROMPTS = ("a {} font", "{} style lettering", "a {} typeface", "text set in a {} font")
TAG_TOP = 1500            # faces returned per search, best first
TAG_IDLE = 900            # seconds without a tag search before the text model is unloaded


def tags_mode():
    return CFG.get("tags", "background")


def tagger_running():
    return _tagger is not None and _tagger.poll() is None


def tags_info():
    try:
        st = json.loads(tg.TAGS_STATUS.read_text())
    except (OSError, ValueError):
        st = {"state": "none", "done": 0, "total": 0}
    avail = tg.available()
    return {"available": avail, "mode": tags_mode(), "running": tagger_running(), "fast": _tagger_fast and tagger_running(),
            "state": st.get("state"), "done": st.get("done", 0), "total": st.get("total", 0),
            "ready": TAGS["model"] is not None, "loading": TAGS["loading"], "error": TAGS["err"],
            "searchable": avail and st.get("done", 0) > 0}


def start_tagger(fast=False):
    global _tagger, _tagger_fast
    if not tg.available():
        raise RuntimeError("Style search needs the optional packages (run install.sh and pick Style search)")
    if tagger_running():
        if _tagger_fast or not fast:
            return
        stop_tagger()                    # background run in progress: restart it at full speed
    _tagger_fast = fast
    _tagger = subprocess.Popen([sys.executable, str(fc.APP / "tagger.py")] + (["--fast"] if fast else []),
                               cwd=fc.APP, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True)   # own process group, so its render workers stop with it


def stop_tagger():
    global _tagger
    if tagger_running():
        import signal
        try:
            os.killpg(_tagger.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            _tagger.wait(10)
        except subprocess.TimeoutExpired:
            os.killpg(_tagger.pid, signal.SIGKILL)
            _tagger.wait()
        try:
            st = json.loads(tg.TAGS_STATUS.read_text())
            tg.status(**{**st, "state": "stopped"})
        except (OSError, ValueError):
            pass
    _tagger = None


def tags_watch():
    """Background mode: run the tagger once after every finished index run (it skips faces already done)."""
    global _tag_seen
    while True:
        try:
            idle = _indexer is None or _indexer.poll() is not None
            stamp = fc.STATUS.stat().st_mtime if fc.STATUS.exists() else 0
            if tags_mode() == "background" and idle and not tagger_running() and stamp != _tag_seen and tg.available():
                _tag_seen = stamp
                start_tagger(False)
            if TAGS["model"] is not None and time.time() - TAGS["used"] > TAG_IDLE:
                with _tags_lock:                 # unused for a while: give the RAM back
                    TAGS.update(model=None, tok=None, M=None, faces=None, stamp=None)
                    TAGS["qcache"].clear()
                import gc
                gc.collect()
        except Exception:
            pass
        threading.Event().wait(5)


def tags_model():
    """Text half of the model, loaded on first use (CPU, so it never holds GPU memory)."""
    with _tags_lock:
        if TAGS["model"] is not None:
            return TAGS
        TAGS["loading"] = True
    try:
        model, _, tok, torch = tg.load_model("cpu")
        model.visual = None              # only text is encoded here
        import gc
        gc.collect()
        torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
        TAGS.update(model=model, tok=tok, torch=torch, err=None)
    except Exception as e:
        TAGS["err"] = f"{type(e).__name__}: {e}"[:300]
        raise
    finally:
        TAGS["loading"] = False
    return TAGS


def tags_matrix():
    """Vectors from cache/tags.sqlite mapped to the current face ids; reloaded when either index changes."""
    import numpy as np
    stamp = tuple(f.stat().st_mtime if f.exists() else 0 for f in (tg.TAGS_STATUS, fc.STATUS))
    if TAGS["stamp"] == stamp and TAGS["M"] is not None:
        return TAGS["M"], TAGS["faces"]
    ids = {}
    for fid, sha1, idx in db().execute("SELECT f.id, x.sha1, f.idx FROM faces f JOIN files x ON x.path = f.path"):
        ids.setdefault((sha1, idx), []).append(fid)
    tdb = sqlite3.connect(tg.TAGS_DB, timeout=30)
    vecs, faces = [], []
    for sha1, idx, v in tdb.execute("SELECT sha1, idx, v FROM emb"):
        f = ids.get((sha1, idx))
        if f:
            vecs.append(np.frombuffer(v, np.float16))
            faces.append(f)
    tdb.close()
    M = np.stack(vecs).astype(np.float32) if vecs else np.zeros((0, 1), np.float32)
    TAGS.update(stamp=stamp, M=M, faces=faces)
    return M, faces


def tags_search(q):
    import numpy as np
    q = " ".join(q.lower().split())[:120]
    TAGS["used"] = time.time()
    if not q:
        return {"q": q, "hits": []}
    t = TAGS["qcache"].get(q)
    if t is None:
        T = tags_model()
        torch = T["torch"]
        with torch.no_grad():
            e = T["model"].encode_text(T["tok"]([p.format(q) for p in PROMPTS])).float()
            e = torch.nn.functional.normalize(e, dim=-1).mean(0)
            t = (e / e.norm()).numpy().astype(np.float32)
        TAGS["qcache"][q] = t
        while len(TAGS["qcache"]) > 200:
            TAGS["qcache"].popitem(last=False)
    M, faces = tags_matrix()
    if not len(M):
        return {"q": q, "hits": []}
    sc = M @ t
    top = np.argsort(-sc)[:TAG_TOP]
    hits = [[fid, round(float(sc[j]), 4)] for j in top for fid in faces[j]]
    return {"q": q, "hits": hits}


# ---------------------------------------------------------------- settings / Google Fonts

def google_dir(root):
    return Path(root) / "Google"


def google_info(root):
    g = google_dir(root)
    info = {"path": str(g), "exists": g.exists(), "git": (g / ".git").exists(), "updated": None}
    if info["git"]:
        r = subprocess.run(["git", "-C", str(g), "log", "-1", "--format=%ct"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            info["updated"] = int(r.stdout.strip())
    return info


def settings(root=None, lib=None):
    root = root or CFG.get("google_root") or LIB
    lib_res = os.path.realpath(lib or LIB)
    g = google_info(root)
    g["in_library"] = os.path.realpath(g["path"]).startswith(lib_res.rstrip("/") + "/")
    return {"library": LIB, "google_root": root, "google": g, "job": dict(_git), "tags": tags_info()}


def save_settings(body):
    global LIB, _tag_seen
    out = {}
    for key in ("library", "google_root"):
        if key in body:
            v = os.path.expanduser(str(body[key]).strip()).rstrip("/") or "/"
            if not os.path.isabs(v) or not os.path.isdir(v):
                raise ValueError(f"Not a folder: {v}")
            out[key] = v
    if "tags" in body:
        if body["tags"] not in ("background", "off"):
            raise ValueError("tags must be background or off")
        out["tags"] = body["tags"]
    try:
        file_cfg = json.loads(fc.CONFIG.read_text()) if fc.CONFIG.exists() else {}
    except ValueError:
        file_cfg = {}
    file_cfg.update(out)
    tmp = fc.CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(file_cfg, indent=2) + "\n")
    os.replace(tmp, fc.CONFIG)
    CFG.update(out)
    if out.get("tags") == "off":
        stop_tagger()
    elif out.get("tags") == "background":
        _tag_seen = None                 # let tags_watch start a run
    changed = "library" in out and out["library"] != LIB
    if changed:
        LIB = out["library"]
        if _indexer is not None and _indexer.poll() is None:
            _indexer.terminate()         # it was scanning the old library; start over on the new one
            _indexer.wait()
        start_index()
    return {**settings(), "rescan": changed}


def pick_folder(start, title):
    start = start if start and os.path.isdir(start) else str(Path.home())
    if shutil.which("kdialog"):
        cmd = ["kdialog", "--title", title, "--getexistingdirectory", start]
    elif shutil.which("zenity"):
        cmd = ["zenity", "--file-selection", "--directory", "--title", title, "--filename", start.rstrip("/") + "/"]
    else:
        raise RuntimeError("no folder picker (install kdialog or zenity)")
    r = subprocess.run(cmd, capture_output=True, text=True)
    path = r.stdout.strip()
    return path if r.returncode == 0 and path else None


_PROGRESS = re.compile(r"(Counting|Compressing|Receiving|Resolving|Updating) (objects|deltas|files):\s+(\d+)%")
_PHASES = {"Counting": (0, 5), "Compressing": (5, 10), "Receiving": (10, 80), "Resolving": (80, 90), "Updating": (90, 100)}


def google_job(root):
    """Clone (or pull) google/fonts into <root>/Google in a background thread."""
    if _git["running"]:
        raise RuntimeError("a download is already running")
    g = google_dir(root)
    if g.exists() and not (g / ".git").exists():
        raise RuntimeError(f"{g} exists but is not a git clone")
    if g.exists():
        action, cmd = "update", ["git", "-C", str(g), "pull", "--ff-only", "--progress"]
    else:
        action, cmd = "download", ["git", "clone", "--depth", "1", "--progress", GOOGLE_URL, str(g)]
    _git.update(running=True, action=action, phase="Starting", percent=0, msg="", ok=None)

    def run():
        tail = []
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
            buf = b""
            while True:
                ch = p.stdout.read(1)
                if not ch:
                    break
                if ch in b"\r\n":
                    line = buf.decode(errors="replace").strip()
                    buf = b""
                    if not line:
                        continue
                    m = _PROGRESS.search(line)
                    if m:
                        lo, hi = _PHASES[m.group(1)]
                        _git.update(phase=f"{m.group(1)} {m.group(2)}",
                                    percent=round(lo + (hi - lo) * int(m.group(3)) / 100, 1))
                    else:
                        tail = (tail + [line])[-6:]
                        _git["msg"] = line
                else:
                    buf += ch
            rc = p.wait()
            ok = rc == 0
            same = any("Already up to date" in t for t in tail)
            msg = "Already up to date" if same else \
                ("Done" if ok else "\n".join(tail[-3:]) or f"git exited with {rc}")
            _git.update(ok=ok, msg=msg, percent=100 if ok else _git["percent"], phase="Finished" if ok else "Failed")
            if ok and not same:
                GF_META.clear()
            if ok and not same and os.path.realpath(g).startswith(os.path.realpath(LIB).rstrip("/") + "/"):
                start_index()
        except Exception as e:
            _git.update(ok=False, msg=f"{type(e).__name__}: {e}", phase="Failed")
        finally:
            _git["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return settings()


def theme():
    try:
        mode = json.loads((Path.home() / ".config/m3-shell/state.json").read_text()).get("mode", "dark")
    except (OSError, ValueError):
        mode = "dark"
    try:
        colors = json.loads((Path.home() / ".config/quickshell/quicksettings/colors.json").read_text())
        return {"mode": mode, "colors": colors.get(mode) or colors.get("dark")}
    except (OSError, ValueError):
        return {"mode": mode, "colors": None}


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send(self, body, ctype="application/json", cache=None, code=200):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, separators=(",", ":")).encode()
        if len(body) > 4096 and "gzip" in self.headers.get("Accept-Encoding", "") and not ctype.startswith("image"):
            body = gzip.compress(body, 5)
            gz = True
        else:
            gz = False
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache or "no-store")
        if gz:
            self.send_header("Content-Encoding", "gzip")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = u.path
        try:
            if p == "/":
                return self.send((WEB / "index.html").read_bytes(), MIME[".html"])
            if p.startswith("/static/"):
                f = (WEB / p[8:]).resolve()
                if f.parent != WEB.resolve() or not f.is_file():
                    return self.send({"error": "not found"}, code=404)
                return self.send(f.read_bytes(), MIME.get(f.suffix, "application/octet-stream"))
            if p in ("/icon", "/icon.png", "/icon.svg"):
                f = fc.APP / "icons" / Path(CFG.get("icon") or fc.DEFAULTS["icon"]).name
                if not f.is_file():
                    f = fc.APP / "icons" / fc.DEFAULTS["icon"]
                return self.send(f.read_bytes(), MIME.get(f.suffix, "application/octet-stream"))
            if p == "/api/fonts":
                return self.send(fonts_payload())
            if p == "/api/status":
                return self.send(read_status())
            if p == "/api/settings":
                return self.send(settings(q.get("root"), q.get("lib")))
            if p == "/api/google":
                return self.send(dict(_git))
            if p == "/api/tags":
                return self.send(tags_search(q.get("q", "")) if q.get("q") else tags_info())
            if p == "/api/theme":
                return self.send(theme())
            if p.startswith("/api/face/"):
                d = face_detail(int(p[10:]))
                return self.send(d or {"error": "not found"}, code=200 if d else 404)
            if p.startswith("/thumb/"):
                r = db().execute("SELECT thumb FROM faces WHERE id=?", (int(p[7:]),)).fetchone()
                if not r:
                    return self.send(b"", "image/webp", code=404)
                return self.send(r[0], "image/webp", "max-age=31536000, immutable")
            if p == "/render":
                size = max(8, min(int(q.get("s", fc.THUMB_SIZE)), 400))
                h = max(16, min(int(q.get("h", fc.THUMB_H)), 600))
                fid = int(q["id"])
                img, _ = render(fid, q.get("t", "")[:200] or sample(fid), size, h)
                return self.send(img, "image/webp", "max-age=86400")
            self.send({"error": "not found"}, code=404)
        except BrokenPipeError:
            pass
        except Exception as e:
            self.send({"error": f"{type(e).__name__}: {e}"}, code=500)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        p = urlparse(self.path).path
        try:
            if p == "/api/enable":
                return self.send({"enabled": set_enabled(body["paths"], bool(body["on"]))})
            if p == "/api/reveal":
                reveal(body["path"])
                return self.send({"ok": True})
            if p == "/api/settings":
                return self.send(save_settings(body))
            if p == "/api/pick":
                return self.send({"path": pick_folder(body.get("start"), body.get("title") or "Choose a folder")})
            if p == "/api/google":
                if body.get("root"):
                    save_settings({"google_root": body["root"]})
                return self.send(google_job(CFG.get("google_root") or LIB))
            if p == "/api/tags":
                a = body.get("action")
                if a == "fast":
                    start_tagger(True)
                elif a == "stop":
                    stop_tagger()
                elif a == "warm" and tg.available() and TAGS["model"] is None and not TAGS["loading"]:
                    TAGS["used"] = time.time()
                    threading.Thread(target=lambda: (tags_model(), tags_matrix()), daemon=True).start()
                return self.send(tags_info())
            if p == "/api/rescan":
                start_index(bool(body.get("full")))
                return self.send(read_status())
            self.send({"error": "not found"}, code=404)
        except Exception as e:
            self.send({"error": f"{type(e).__name__}: {e}"}, code=500)


def main():
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else CFG["port"]
    if "--no-scan" not in sys.argv:
        start_index()
    threading.Thread(target=tags_watch, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    print(f"Font Maniac on http://127.0.0.1:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
