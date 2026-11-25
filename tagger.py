#!/usr/bin/env python3
"""Style index ("tags"): one image-text embedding per font face, so plain words like "groovy 70s"
or "horror" find fonts that look like that.

Usage: tagger.py [--fast] [--limit N]
  default   background pace: low CPU/GPU priority, small batches, pauses between them
  --fast    full speed: big GPU batches, every CPU core renders

Each face is rendered as a square card with the same neutral text (never its name, the model reads
text in images) and embedded with a SigLIP2 image encoder. Vectors live in cache/tags.sqlite keyed by
(file sha1, face index), so rescans and duplicate copies don't redo work. Progress: cache/tags-status.json.
Needs the optional packages in requirements-tags.txt.
"""
import io
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import fontcore as fc

MODEL, PRETRAINED = "ViT-B-16-SigLIP2-256", "webli"
CARD = 256
LINES = ("Aa Gg Rr Ss", "Handgloves", "Quick 2580")
TAGS_DB = fc.CACHE / "tags.sqlite"
TAGS_STATUS = fc.CACHE / "tags-status.json"
MODELS_DIR = fc.CACHE / "models"


def model_env():
    """Keep downloaded weights inside the app folder, not ~/.cache."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(MODELS_DIR / "hf"))
    os.environ.setdefault("TORCH_HOME", str(MODELS_DIR / "torch"))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


def available():
    import importlib.util
    return all(importlib.util.find_spec(m) for m in ("torch", "open_clip", "numpy"))


def connect():
    fc.CACHE.mkdir(exist_ok=True)
    db = sqlite3.connect(TAGS_DB, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS emb(sha1 TEXT, idx INT, v BLOB, PRIMARY KEY(sha1, idx));
    CREATE TABLE IF NOT EXISTS skip(sha1 TEXT, idx INT, err TEXT, PRIMARY KEY(sha1, idx));
    CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
    """)
    row = db.execute("SELECT v FROM meta WHERE k='model'").fetchone()
    want = f"{MODEL}/{PRETRAINED}/{CARD}/{'|'.join(LINES)}"
    if row and row[0] != want:          # card or model changed: old vectors don't compare
        db.executescript("DELETE FROM emb; DELETE FROM skip;")
    db.execute("INSERT OR REPLACE INTO meta VALUES('model', ?)", (want,))
    db.commit()
    return db


def status(**kw):
    tmp = TAGS_STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps({"time": time.time(), **kw}))
    os.replace(tmp, TAGS_STATUS)


# ---------------------------------------------------------------- card rendering

def card(path, idx, scripts):
    """Square greyscale card: dark text on white, each line scaled to fill the width."""
    from PIL import Image, ImageDraw
    data, pidx = fc.pil_data(path, idx)
    lines = LINES
    if "latin" not in (scripts or ""):           # symbol / non-Latin faces: show what they have
        tt = fc.open_faces(data, ".ttc" if data[:4] == b"ttcf" else ".ttf")[pidx if data[:4] == b"ttcf" else 0][1]
        s = fc.sample_text(tt.getBestCmap() or {}, scripts or "")
        lines = (s[:6], s[6:12]) if len(s) > 6 else (s,)
    img = Image.new("L", (CARD, CARD), 255)
    draw = ImageDraw.Draw(img)
    pad, slot = 12, (CARD - 24) / len(lines)
    for i, text in enumerate(lines):
        size = int(slot * 0.62)
        font = fc.load_pil(data, pidx, size)
        box = draw.textbbox((0, 0), text, font=font, anchor="ls")
        w, h = box[2] - box[0], box[3] - box[1]
        if w <= 0 or h <= 0:
            continue
        scale = min((CARD - 2 * pad) / w, slot * 0.92 / h)
        if abs(scale - 1) > 0.04:
            size = max(6, int(size * scale))
            font = fc.load_pil(data, pidx, size)
            box = draw.textbbox((0, 0), text, font=font, anchor="ls")
            w, h = box[2] - box[0], box[3] - box[1]
        x = (CARD - w) / 2 - box[0]
        y = pad + slot * i + (slot - h) / 2 - box[1]
        draw.text((x, y), text, font=font, fill=0, anchor="ls")
    if img.getbbox() is None and Image.eval(img, lambda p: 255 - p).getbbox() is None:
        raise ValueError("nothing rendered")
    return img


def card_job(job):
    sha1, idx, path, scripts = job
    try:
        img = card(path, idx, scripts)
        buf = io.BytesIO()
        img.save(buf, "PNG", compress_level=1)
        return sha1, idx, buf.getvalue(), None
    except Exception as e:
        return sha1, idx, None, f"{type(e).__name__}: {e}"[:200]


# ---------------------------------------------------------------- model

def load_model(device):
    model_env()
    import open_clip
    import torch
    model, _, preprocess = open_clip.create_model_and_transforms(MODEL, pretrained=PRETRAINED, device=device)
    model.eval()
    if device == "cuda":
        model = model.half()
    return model, preprocess, open_clip.get_tokenizer(MODEL), torch


def todo(db):
    main = fc.connect()
    rows = main.execute("""SELECT x.sha1, f.idx, MIN(f.path), MAX(f.scripts) FROM faces f JOIN files x ON x.path = f.path
        WHERE x.sha1 IS NOT NULL GROUP BY x.sha1, f.idx ORDER BY MIN(f.family)""").fetchall()
    main.close()
    done = set(db.execute("SELECT sha1, idx FROM emb UNION SELECT sha1, idx FROM skip"))
    return [r for r in rows if (r[0], r[1]) not in done], len(rows)


def main():
    fast = "--fast" in sys.argv
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0
    if not fast:
        os.nice(19)
    db = connect()
    jobs, total = todo(db)
    have = total - len(jobs)
    if limit:
        import random
        random.seed(1)
        jobs = random.sample(jobs, min(limit, len(jobs)))
    status(state="loading", mode="fast" if fast else "background", done=have, total=total)
    if not jobs:
        status(state="done", mode="fast" if fast else "background", done=have, total=total)
        return
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if not fast:
        torch.set_num_threads(2)
    model, preprocess, _, torch = load_model(device)
    batch = 128 if fast else 16
    t0, n = time.time(), 0
    status(state="indexing", mode="fast" if fast else "background", done=have, total=total, device=device)

    from concurrent.futures import ProcessPoolExecutor
    from PIL import Image
    pool = ProcessPoolExecutor(os.cpu_count() if fast else 1)
    parent = os.getppid()
    try:
        for i in range(0, len(jobs), batch):
            if os.getppid() != parent:   # the app closed: stop, the next start resumes from here
                break
            res = list(pool.map(card_job, jobs[i:i + batch], chunksize=4))
            ok = [r for r in res if r[2]]
            for sha1, idx, _, err in res:
                if err:
                    db.execute("INSERT OR REPLACE INTO skip VALUES(?,?,?)", (sha1, idx, err))
            if ok:
                x = torch.stack([preprocess(Image.open(io.BytesIO(r[2])).convert("RGB")) for r in ok]).to(device)
                with torch.no_grad():
                    if device == "cuda":
                        x = x.half()
                    v = model.encode_image(x)
                    v = torch.nn.functional.normalize(v.float(), dim=-1).half().cpu().numpy()
                db.executemany("INSERT OR REPLACE INTO emb VALUES(?,?,?)",
                               [(r[0], r[1], v[j].tobytes()) for j, r in enumerate(ok)])
            db.commit()
            n += len(res)
            rate = n / (time.time() - t0)
            status(state="indexing", mode="fast" if fast else "background", done=have + n, total=total,
                   device=device, rate=round(rate, 1))
            print(f"\r{have + n}/{total}  {rate:.0f} faces/s", end="", flush=True)
            if not fast:
                if device == "cuda":
                    torch.cuda.empty_cache()
                time.sleep(0.5)
    finally:
        pool.shutdown(cancel_futures=True)
    print()
    status(state="done", mode="fast" if fast else "background", done=have + n, total=total,
           seconds=round(time.time() - t0, 1))


if __name__ == "__main__":
    main()
