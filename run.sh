#!/usr/bin/env bash
# Font Maniac: start the local server (it rescans for new fonts in the background) and open it
# in an app window. Closing the window stops the server.
set -euo pipefail
APP="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PY="$APP/.venv/bin/python"
[ -x "$PY" ] || PY=python3          # no venv: hope the system Python has fontTools + Pillow
mkdir -p "$APP/cache"

read -r PORT BROWSER < <("$PY" -c "import sys;sys.path.insert(0,'$APP');import fontcore;c=fontcore.config();print(c['port'],c['browser'] or '-')")
URL="http://127.0.0.1:$PORT"

if curl -fs "$URL/api/status" >/dev/null 2>&1; then
    started=""                       # already running (another window): just open it
else
    "$PY" "$APP/server.py" --port "$PORT" >"$APP/cache/server.log" 2>&1 &
    started=$!
    for _ in $(seq 50); do curl -fs "$URL/api/status" >/dev/null 2>&1 && break; sleep 0.1; done
fi

# Any Chromium-family browser can open the page as its own app window (--app).
if [ "$BROWSER" = "-" ]; then
    BROWSER=""
    for b in chromium chromium-browser google-chrome-stable google-chrome brave brave-browser \
             microsoft-edge-stable microsoft-edge vivaldi-stable vivaldi; do
        command -v "$b" >/dev/null && { BROWSER=$b; break; }
    done
fi

if [ -n "$BROWSER" ]; then
    "$BROWSER" --app="$URL" --class=font-maniac --user-data-dir="$APP/cache/browser" \
        --no-first-run --no-default-browser-check >/dev/null 2>&1 || true
else
    xdg-open "$URL"                  # normal browser tab; the server keeps running until logout
    [ -n "$started" ] && wait "$started"
fi
[ -n "$started" ] && kill "$started" 2>/dev/null || true
