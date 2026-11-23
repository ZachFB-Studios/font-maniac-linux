#!/usr/bin/env bash
# Font Maniac: start the local server (it rescans for new fonts in the background) and open it
# in an app window. Closing the window stops the server.
# Problems (server won't start, browser broken or missing) show up as a desktop notification.
set -euo pipefail
APP="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
NAME="Font Maniac"
CLASS=font-maniac
PROFILE="$APP/cache/browser"         # Chromium's profile; other browsers get browser-<name>
PY="$APP/.venv/bin/python"
[ -x "$PY" ] || PY=python3          # no venv: hope the system Python has fontTools + Pillow
mkdir -p "$APP/cache"
LOG="$APP/cache/server.log"

# Chromium-family browsers, best first. Any of them can open the page as its own window (--app).
BROWSERS=(chromium chromium-browser ungoogled-chromium google-chrome-stable google-chrome
          brave brave-browser microsoft-edge-stable microsoft-edge vivaldi-stable vivaldi
          thorium-browser opera)
# The same browsers installed as Flatpaks, tried when none of the above works.
FLATPAKS=(org.chromium.Chromium io.github.ungoogled_software.ungoogled_chromium com.google.Chrome
          com.brave.Browser com.microsoft.Edge com.vivaldi.Vivaldi)

# Tell the user something went wrong: terminal + notification (or a dialog when there is no
# notification daemon). Never blocks the launcher.
say() {  # say <title> <message>
    printf '%s: %s\n%s\n' "$NAME" "$1" "$2" >&2
    if command -v notify-send >/dev/null && notify-send -u critical -a "$NAME" "$NAME: $1" "$2" 2>/dev/null; then :
    elif command -v kdialog >/dev/null; then kdialog --title "$NAME" --sorry "$1"$'\n\n'"$2" >/dev/null 2>&1 &
    elif command -v zenity >/dev/null; then zenity --warning --title "$NAME" --text "$1"$'\n\n'"$2" >/dev/null 2>&1 &
    fi
}

# The install command for this distro's package manager.
pkg_hint() {
    if   command -v pacman  >/dev/null; then echo "sudo pacman -S chromium"
    elif command -v apt     >/dev/null; then echo "sudo apt install chromium"
    elif command -v dnf     >/dev/null; then echo "sudo dnf install chromium"
    elif command -v zypper  >/dev/null; then echo "sudo zypper install chromium"
    elif command -v xbps-install >/dev/null; then echo "sudo xbps-install chromium"
    else echo "install Chromium (or Chrome, Brave, Edge, Vivaldi) with your package manager"
    fi
}

if ! cfg=$("$PY" -c "import sys;sys.path.insert(0,'$APP');import fontcore;c=fontcore.config();print(c['port'],c.get('browser') or '-')" 2>&1); then
    say "can't read its settings" "$(tail -n 3 <<<"$cfg")"$'\n\n'"Run the installer again and pick Update."
    exit 1
fi
read -r PORT BROWSER <<<"$cfg"
URL="http://127.0.0.1:$PORT"

if curl -fs "$URL/api/status" >/dev/null 2>&1; then
    started=""                       # already running (another window): just open it
else
    "$PY" "$APP/server.py" --port "$PORT" >"$LOG" 2>&1 &
    started=$!
    for _ in $(seq 100); do
        curl -fs "$URL/api/status" >/dev/null 2>&1 && break
        kill -0 "$started" 2>/dev/null || break      # server died: stop waiting
        sleep 0.1
    done
    if ! curl -fs "$URL/api/status" >/dev/null 2>&1; then
        kill "$started" 2>/dev/null || true
        say "the server didn't start" "$(tail -n 4 "$LOG")"$'\n\n'"Full log: $LOG"
        exit 1
    fi
fi

# Pick a browser that actually runs. `--version` loads the real binary, so a broken install
# (e.g. a missing library after a system update) fails here instead of failing silently later.
CHECK="$APP/cache/browser-check.log"
works() { timeout 10 "$1" --version >/dev/null 2>"$CHECK"; }
first_err="" chosen=""
cands=()
[ "$BROWSER" != "-" ] && cands+=("$BROWSER")
cands+=("${BROWSERS[@]}")
for b in "${cands[@]}"; do
    command -v "$b" >/dev/null || continue
    if works "$b"; then chosen=$b; break; fi
    [ -n "$first_err" ] || first_err="$b won't start: $(head -c 300 "$CHECK")"
done
if [ -z "$chosen" ] && command -v flatpak >/dev/null; then
    for id in "${FLATPAKS[@]}"; do
        flatpak info "$id" >/dev/null 2>&1 && { chosen="flatpak:$id"; break; }
    done
fi
[ -n "$first_err" ] && [ -n "$chosen" ] && say "using $chosen instead" "$first_err"

if [ -n "$chosen" ]; then
    # Each browser gets its own profile folder: sharing one between browsers can break it.
    if [[ $chosen == flatpak:* ]]; then
        # Flatpak browsers only see their own files, so let them into cache/ for the profile.
        prof="$APP/cache/browser-${chosen#flatpak:}"; mkdir -p "$prof"
        run=(flatpak run --filesystem="$prof" "${chosen#flatpak:}")
    else
        prof="$PROFILE"; [ "$chosen" = chromium ] || prof="$APP/cache/browser-$(basename "$chosen")"
        run=("$chosen")
    fi
    t0=$SECONDS
    if ! "${run[@]}" --app="$URL" --class="$CLASS" --user-data-dir="$prof" \
            --no-first-run --no-default-browser-check >/dev/null 2>"$APP/cache/browser.log" \
       && (( SECONDS - t0 < 5 )); then
        say "$chosen closed right away" "$(tail -n 3 "$APP/cache/browser.log")"$'\n\n'"Log: $APP/cache/browser.log"
    fi
else
    if [ -n "$first_err" ]; then
        say "no working Chromium browser" "$first_err"$'\n\nFix: '"$(pkg_hint)"$'\nOpening in your default browser for now.'
    else
        say "no Chromium browser found" "It opens in a normal tab for now. For its own window: $(pkg_hint)"
    fi
    if ! xdg-open "$URL" >/dev/null 2>&1; then
        say "couldn't open any browser" "Open $URL yourself. Fix: $(pkg_hint)"
    fi
    [ -n "$started" ] && wait "$started"   # normal tab: the server keeps running until logout
fi
[ -n "$started" ] && kill "$started" 2>/dev/null || true
