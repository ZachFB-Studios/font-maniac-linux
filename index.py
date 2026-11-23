#!/usr/bin/env python3
"""Incremental indexer: scans the library, (re)indexes new or changed font files.

Usage: index.py [--full]     progress goes to stdout and cache/status.json
"""
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import fontcore as fc

COLS = ("path", "idx", "family", "style", "full", "ps", "weight", "width", "italic", "mono", "variable",
        "axes", "panose", "category", "cat_src", "serif", "contrast", "xh", "wr", "stem", "slant",
        "scripts", "glyphs", "version", "vendor", "designer", "tw", "thumb")


def status(**kw):
    tmp = fc.STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps({"time": time.time(), **kw}))
    os.replace(tmp, fc.STATUS)


def scan(root):
    found = {}
    stack = [root]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    elif e.is_file() and os.path.splitext(e.name)[1].lower() in fc.FONT_EXT:
                        st = e.stat()
                        found[e.path] = (st.st_size, st.st_mtime)
        except OSError:
            pass
    return found


def main():
    full = "--full" in sys.argv
    cfg = fc.config()
    db = fc.connect()
    status(state="scanning", done=0, total=0)
    found = scan(cfg["library"])
    known = {p: (s, m) for p, s, m in db.execute("SELECT path, size, mtime FROM files")}
    todo = [p for p, sm in found.items() if full or known.get(p) != sm]
    gone = [p for p in known if p not in found]
    for p in gone:
        db.execute("DELETE FROM faces WHERE path=?", (p,))
        db.execute("DELETE FROM files WHERE path=?", (p,))
    db.commit()
    print(f"{len(found)} font files, {len(todo)} to index, {len(gone)} gone", flush=True)

    t0, done, errors = time.time(), 0, 0
    status(state="indexing", done=0, total=len(todo))
    if todo:
        todo.sort()
        with ProcessPoolExecutor(os.cpu_count()) as pool:
            for path, size, mtime, sha1, err, faces in pool.map(fc.process_file, todo, chunksize=24):
                db.execute("DELETE FROM faces WHERE path=?", (path,))
                db.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,COALESCE((SELECT added FROM files WHERE path=?),?))",
                           (path, size, mtime, sha1, err, path, time.time()))
                for f in faces:
                    f["path"] = path
                    db.execute(f"INSERT INTO faces({','.join(COLS)}) VALUES({','.join('?' * len(COLS))})",
                               [f[c] for c in COLS])
                done += 1
                errors += bool(err)
                if done % 250 == 0 or done == len(todo):
                    db.commit()
                    rate = done / (time.time() - t0)
                    status(state="indexing", done=done, total=len(todo), errors=errors)
                    print(f"\r{done}/{len(todo)}  {rate:.0f} files/s  {errors} errors", end="", flush=True)
        print()
    db.commit()
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    n = db.execute("SELECT COUNT(*) FROM faces").fetchone()[0]
    status(state="done", done=done, total=len(todo), errors=errors, faces=n, gone=len(gone),
           seconds=round(time.time() - t0, 1))
    print(f"done: {n} faces indexed in total, {errors} unreadable files, {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
