#!/usr/bin/env bash
# Name: Font Maniac installer
# Description: Install, update or remove Font Maniac, a font manager for big font collections
# Hold: no
#
# A step-by-step terminal installer (bash + fzf). It asks where the app goes, where your fonts
# are, whether to download Google Fonts, and which shortcuts to add, then:
#   - copies the app (or runs it from this folder), makes a Python venv inside it
#     (fonttools, pillow, brotli), and writes config.json
#   - indexes your fonts once, so the first start is instant
#   - adds an app-menu entry with the icon you pick and an optional `font-maniac` command
# Run it again to update or change settings; `./install.sh --uninstall` removes everything it
# added. Your font files are never moved, changed or deleted.
set -uo pipefail

SELF=$(readlink -f "$0")
SRC=$(dirname "$SELF")
DATA=${XDG_DATA_HOME:-$HOME/.local/share}
CONF_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/font-maniac
RECORD=$CONF_DIR/install.conf
BIN_DIR=${FM_BIN_DIR:-$HOME/.local/bin}
APPS_DIR=$DATA/applications
ICON_DIR=$DATA/icons/hicolor/512x512/apps
ENABLE_DIR=$DATA/fonts/FontManiac
APP_FILES=(fontcore.py index.py server.py run.sh install.sh requirements.txt README.md LICENSE web icons)
GOOGLE_URL=https://github.com/google/fonts
# key, name: the icons in icons/ (make them with dev/make-icons.py)
ICONS=(lavender Lavender peach Peach mint Mint sky Sky butter Butter blush Blush 3d "3D cards")
icon_file() { [[ $1 == 3d ]] && echo font-maniac.png || echo "font-maniac-$1.svg"; }
DRY=0

# ------------------------------------------------ colours (Material 3 palette if this desktop has one)
P='#aac7ff' ON='#e2e2e9' ONV='#c4c6d0' SC='#3e4759' ONSC='#dae2f9' OV='#44474f' T='#9fd49b' ERR='#ffb4ab'
if command -v jq >/dev/null && [[ -r ~/.config/quickshell/quicksettings/colors.json ]]; then
    pal=$(jq -c --arg m "$(jq -r '.mode // "dark"' ~/.config/m3-shell/state.json 2>/dev/null)" '.[$m] // .dark' \
          ~/.config/quickshell/quicksettings/colors.json 2>/dev/null)
    c() { local v; v=$(jq -r --arg k "$1" '.[$k] // empty' <<<"$pal" 2>/dev/null); echo "${v:-$2}"; }
    P=$(c primary "$P"); ON=$(c on_surface "$ON"); ONV=$(c on_surface_variant "$ONV")
    SC=$(c secondary_container "$SC"); ONSC=$(c on_secondary_container "$ONSC")
    OV=$(c outline_variant "$OV"); T=$(c tertiary "$T"); ERR=$(c error "$ERR")
fi
fg() { local h=${1#\#}; printf '\e[38;2;%d;%d;%dm' "0x${h:0:2}" "0x${h:2:2}" "0x${h:4:2}"; }
B=$'\e[1m' R=$'\e[0m'
BORDER=rounded
[[ $(jq -r '.rounding // 14' ~/.config/m3-shell/state.json 2>/dev/null) == 0 ]] && BORDER=sharp

# ------------------------------------------------ small helpers
title() { clear; printf '\n  %s%s%s%s\n\n' "$(fg "$P")" "$B" "$1" "$R"; }
say()   { printf '  %s\n' "$*"; }
dim()   { printf '  %s%s%s\n' "$(fg "$ONV")" "$*" "$R"; }
ok()    { printf '  %s%s✓ %s%s\n' "$(fg "$T")" "$B" "$*" "$R"; }
bad()   { printf '  %s%s%s\n' "$(fg "$ERR")" "$*" "$R"; }
key()   { printf '\n  %spress any key %s' "$(fg "$ONV")" "$R"; read -rsn1 _; echo; }
die()   { bad "$*"; key; exit 1; }
plural() { (( $1 == 1 )) || echo s; }
tilde() { [[ $1 == "$HOME"* ]] && echo "~${1#"$HOME"}" || echo "$1"; }

# Turn whatever got pasted (quotes, file://, %20, trailing /, ~) into a plain absolute path.
clean_path() {
    local p=$1
    p=${p#"${p%%[![:space:]]*}"}; p=${p%"${p##*[![:space:]]}"}
    p=${p#\'}; p=${p%\'}; p=${p#\"}; p=${p%\"}; p=${p#file://}
    [[ $p == *%[0-9A-Fa-f][0-9A-Fa-f]* ]] && p=$(printf '%b' "${p//%/\\x}")
    [[ $p == "~" || $p == "~/"* ]] && p=$HOME${p:1}
    [[ $p == /* ]] || return 1
    [[ $p == / ]] || p=${p%/}
    printf '%s' "$p"
}

# The distro's install command for a list of packages (only for telling the user).
pkg_cmd() {
    if command -v pacman >/dev/null; then echo "sudo pacman -S --needed $*"
    elif command -v apt >/dev/null; then echo "sudo apt install $*"
    elif command -v dnf >/dev/null; then echo "sudo dnf install $*"
    elif command -v zypper >/dev/null; then echo "sudo zypper install $*"
    elif command -v xbps-install >/dev/null; then echo "sudo xbps-install $*"
    else echo "install: $*"; fi
}

# ------------------------------------------------ fzf menus
FZF=(fzf --ansi --layout=reverse --height=100% --border="$BORDER" --margin=1,2 --padding=1,2 --info=hidden
     --pointer='▌' --separator='─' --delimiter=$'\t' --with-nth=2 --prompt='  '
     --color="bg:-1,bg+:$SC,fg:$ON,fg+:$ONSC,hl:$P,hl+:$P,border:$OV,separator:$OV,header:$ONV,prompt:$P,pointer:$P,label:$P,query:$ON,gutter:-1")

# row KEY NAME [DETAILS]
row() { printf '%s\t%s%s%s%s   %s%s%s\n' "$1" "$(fg "$P")" "$B" "$2" "$R" "$(fg "$ONV")" "${3:-}" "$R"; }

# menu TITLE HEADER [fzf args…] < rows   → prints the chosen key; returns 1 on Esc
menu() {
    local out t=$1 h=$2
    shift 2
    out=$(SHELL=bash "${FZF[@]}" "$@" --border-label=" $t " --header="$h"$'\n') || return 1
    printf '%s' "${out%%$'\t'*}"
}
HINT='Enter  choose     Esc  back'

# A GUI folder picker, when there's a display and kdialog/zenity.
gui_pick() {
    [[ -n ${WAYLAND_DISPLAY:-}${DISPLAY:-} ]] || return 1
    if command -v kdialog >/dev/null; then kdialog --title "$2" --getexistingdirectory "$1" 2>/dev/null
    elif command -v zenity >/dev/null; then zenity --file-selection --directory --title "$2" --filename "$1/" 2>/dev/null
    else return 1; fi
}
have_gui() { [[ -n ${WAYLAND_DISPLAY:-}${DISPLAY:-} ]] && { command -v kdialog || command -v zenity; } >/dev/null; }

# browse START TITLE → prints the chosen folder; returns 1 on Esc
browse() {
    local cur=$1 hidden=0 pick d name dirs
    [[ -d $cur ]] || cur=$HOME
    while :; do
        pick=$(
            {
                row @use "✓ Use this folder" "$(tilde "$cur")"
                row @paste "Type or paste a path"
                have_gui && row @gui "Open a folder picker window"
                [[ $cur != / ]] && row @up ".." "up one folder"
                if ((hidden)); then row @hide "Hide hidden folders"; else row @show "Show hidden folders"; fi
                shopt -s nullglob
                dirs=("$cur"/*/)
                ((hidden)) && dirs+=("$cur"/.[!.]*/)
                for d in "${dirs[@]}"; do
                    name=${d%/}; name=${name##*/}
                    printf '%s\t  %s%s/%s\n' "${d%/}" "$(fg "$ON")" "$name" "$R"
                done
                shopt -u nullglob
            } | menu "$2" "$(tilde "$cur")"$'\n'"Enter  open / choose     type  filter     Esc  back"
        ) || return 1
        case $pick in
            @use) printf '%s' "$cur"; return 0 ;;
            @up) cur=$(dirname "$cur") ;;
            @show) hidden=1 ;;
            @hide) hidden=0 ;;
            @paste) d=$(ask_path "$2" "$cur") && { printf '%s' "$d"; return 0; } ;;
            @gui) d=$(gui_pick "$cur" "$2") && [[ -n $d ]] && { printf '%s' "$d"; return 0; } ;;
            *) cur=$pick ;;
        esac
    done
}

# ask_path TITLE → prints a cleaned absolute path (empty answer = back)
# The line starts empty: a pre-filled "~/" plus a pasted /full/path would end up inside home.
ask_path() {
    local ans p
    title "$1" >/dev/tty
    dim "Paste or type the full path, starting with / or ~   (Tab completes, empty goes back)" >/dev/tty
    echo >/dev/tty
    while :; do
        read -rep "  folder: " ans </dev/tty 2>/dev/tty || return 1
        [[ -n ${ans// } ]] || return 1
        if p=$(clean_path "$ans"); then
            printf '  %s→ %s%s\n' "$(fg "$ONV")" "$(tilde "$p")" "$R" >/dev/tty
            printf '%s' "$p"; return 0
        fi
        bad "Start the path with / or ~ (for example /mnt/drive/Fonts or ~/Fonts)." >/dev/tty
    done
}

count_fonts() {
    timeout 20 find "$1" -type f \( -iname '*.ttf' -o -iname '*.otf' -o -iname '*.ttc' -o -iname '*.otc' \
        -o -iname '*.woff' -o -iname '*.woff2' \) 2>/dev/null | wc -l
}

# ------------------------------------------------ checks
check_deps() {
    local miss=() opt=() hard=0 b found=""
    command -v fzf >/dev/null || { miss+=(fzf); hard=1; }
    command -v python3 >/dev/null || { miss+=(python3); hard=1; }
    if command -v python3 >/dev/null; then
        python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' || { bad "Python 3.8 or newer is needed."; hard=1; }
        python3 -c 'import venv, ensurepip' 2>/dev/null || {
            command -v apt >/dev/null && miss+=(python3-venv) || miss+=(python-venv); hard=1; }
    fi
    command -v curl >/dev/null || { miss+=(curl); hard=1; }
    command -v fc-cache >/dev/null || { miss+=(fontconfig); hard=1; }
    command -v git >/dev/null || opt+=("git (to download Google Fonts)")
    for b in chromium chromium-browser google-chrome-stable google-chrome brave brave-browser \
             microsoft-edge-stable microsoft-edge vivaldi-stable vivaldi; do
        command -v "$b" >/dev/null && { found=$b; break; }
    done
    [[ -n $found ]] || opt+=("a Chromium-based browser (chromium, chrome, brave, edge, vivaldi) for the app window; without one it opens in a normal browser tab")
    if ((hard)); then
        printf '\nFont Maniac needs a few things first:\n\n  %s\n\n' "$(pkg_cmd "${miss[@]}")"
        exit 1
    fi
    OPTIONAL=("${opt[@]}")
}

# ------------------------------------------------ steps (each returns 1 to go back)
step_app() {
    local pick here=0 p
    [[ -f $SRC/.git/HEAD || -d $SRC/.git ]] && here=1
    pick=$(
        {
            [[ -n $APP ]] && row "$APP" "$(tilde "$APP")" "current"
            [[ $APP == "$DATA/font-maniac" ]] || row "$DATA/font-maniac" "$(tilde "$DATA")/font-maniac" "recommended"
            row "$HOME/Apps/font-maniac" "~/Apps/font-maniac"
            row "$SRC" "Run it from this folder" "$(tilde "$SRC")"
            row @browse "Browse…" "pick a folder; the app goes in font-maniac/ inside it"
            row @paste "Type or paste a path"
        } | menu "Where should the app go?" "The app is small (a few MB plus its font index).   $HINT"
    ) || return 1
    case $pick in
        @browse) p=$(browse "$HOME" "Pick a folder for the app") || return 2 ;;
        @paste) p=$(ask_path "Where should the app go?" "$HOME") || return 2 ;;
        *) p=$pick ;;
    esac
    if [[ $pick == @* && $p != "$SRC" && ${p##*/} != font-maniac ]]; then p=$p/font-maniac; fi
    if [[ -d $p && $p != "$SRC" && ! -f $p/fontcore.py ]] && [[ -n $(ls -A "$p" 2>/dev/null) ]]; then
        title "Where should the app go?"; bad "$(tilde "$p") already has other files in it."
        dim "Pick a new or empty folder."; key; return 2
    fi
    APP=$p
}

step_lib() {
    local pick p n prev=${LIB:-}
    pick=$(
        {
            [[ -n $prev && -d $prev ]] && row "$prev" "$(tilde "$prev")" "current"
            [[ $prev == "$HOME/Fonts" ]] || row "$HOME/Fonts" "~/Fonts" "recommended  ·  $([[ -d $HOME/Fonts ]] && echo "exists" || echo "will be created")"
            row @browse "Browse…"
            row @paste "Type or paste a path"
        } | menu "Where's your font collection?" "This is where your full font collection is stored, for browsing.
Keep every font you own in here, sorted into any subfolders you like. Another drive is fine.
Nothing in it gets moved or installed: fonts you switch on are linked into
$(tilde "$ENABLE_DIR") automatically.

$HINT"
    ) || return 1
    case $pick in
        @browse) p=$(browse "${prev:-$HOME}" "Pick your font collection folder") || return 2 ;;
        @paste) p=$(ask_path "Where's your font collection?" "${prev:-$HOME}") || return 2 ;;
        *) p=$pick ;;
    esac
    if [[ ! -d $p ]]; then
        if [[ $p == "$HOME/Fonts" ]]; then mkdir -p "$p"
        else title "Where's your font collection?"; bad "$(tilde "$p") isn't a folder."; key; return 2; fi
    fi
    title "Where's your font collection?"
    dim "Counting fonts in $(tilde "$p")…"
    n=$(count_fonts "$p")
    if ((n)); then ok "Found $n font file$(plural "$n")."
    else dim "No fonts in there yet. That's fine: add some, or download Google Fonts next."; fi
    if [[ $p == "$DATA/fonts"* || $p == "$HOME/.fonts"* ]]; then
        echo; bad "These fonts are already installed on your system, so enabling them here would add them twice."
        dim "Font Maniac is meant for a collection that isn't installed yet."
    fi
    LIB=$p
    sleep 1
}

step_google() {
    local pick g=$LIB/Google
    if ! command -v git >/dev/null; then GOOGLE=later; ((BACKING)) && return 1; return 0; fi
    if [[ -d $g/.git ]]; then
        pick=$( { row update "Update Google Fonts now" "$(tilde "$g")"; row later "Not now"; } |
            menu "Google Fonts" "You already have Google Fonts in your fonts folder.   $HINT") || return 1
    else
        pick=$( {
            row now "Download now" "into $(tilde "$g")  ·  1.3 GB download, about 4 GB on disk"
            row later "Later" "any time from Settings (gear icon) in the app"
        } | menu "Google Fonts" "Every Google font (over 2,000 families) with categories and licences.   $HINT") || return 1
    fi
    GOOGLE=$pick
}

step_launcher() {
    local pick
    pick=$( {
        row yes "Add it to the app menu" "$(tilde "$APPS_DIR")/font-maniac.desktop"
        row no "No menu entry"
    } | menu "App menu" "GNOME, KDE, XFCE and launchers like rofi, wofi, fuzzel and walker all read this folder, on every distro.   $HINT") || return 1
    LAUNCHER=$pick
}

step_cmd() {
    local pick note=""
    [[ :$PATH: == *":$BIN_DIR:"* ]] || note="  ·  $(tilde "$BIN_DIR") isn't on your PATH yet"
    pick=$( {
        row yes "Add a font-maniac command" "$(tilde "$BIN_DIR")/font-maniac$note"
        row no "No command"
    } | menu "Terminal command" "Lets you start it by typing font-maniac.   $HINT") || return 1
    CMD=$pick
}

step_icon() {
    local i k n prev=${ICON:-lavender} pv=()
    if command -v chafa >/dev/null; then
        pv=(--preview-window='right,45%,border-left' --preview "f=\$(case {1} in 3d) echo font-maniac.png;; *) echo font-maniac-{1}.svg;; esac); chafa -f symbols -s \${FZF_PREVIEW_COLUMNS}x\${FZF_PREVIEW_LINES} \"$SRC/icons/\$f\" 2>/dev/null || echo 'No preview for this one.'")
    fi
    ICON=$( {
        for ((i = 0; i < ${#ICONS[@]}; i += 2)); do
            [[ ${ICONS[i]} == "$prev" ]] && row "${ICONS[i]}" "${ICONS[i+1]}" "current"
        done
        for ((i = 0; i < ${#ICONS[@]}; i += 2)); do
            k=${ICONS[i]} n=${ICONS[i+1]}
            [[ $k == "$prev" ]] || row "$k" "$n" "$([[ $k == 3d ]] && echo "soft 3D render" || echo "flat, sharp at every size")"
        done
    } | menu "Pick an icon" "For the app menu, dock and browser tab. Change it any time: run the installer and pick Update.   $HINT" "${pv[@]}"
    ) || { ICON=$prev; return 1; }
}

step_confirm() {
    local g i
    case $GOOGLE in now) g="download now" ;; update) g="update now" ;; *) g="later" ;; esac
    menu "Ready to install" "Esc  back" < <(
        row go "Install" "Enter"
        printf ' \t\n'
        row - "App" "$(tilde "$APP")$([[ $APP == "$SRC" ]] && echo "  (this folder)")"
        row - "Font collection" "$(tilde "$LIB")"
        row - "Google Fonts" "$g"
        row - "App menu" "$LAUNCHER"
        row - "Command" "$CMD"
        row - "Icon" "$(for ((i = 0; i < ${#ICONS[@]}; i += 2)); do [[ ${ICONS[i]} == "$ICON" ]] && echo "${ICONS[i+1]}"; done)"
        row - "Enabled fonts" "linked into $(tilde "$ENABLE_DIR")"
    ) | grep -qx go || return 1
}

# ------------------------------------------------ install
bar() { (($1 > 0)) && printf "%${1}s" "" | sed 's/ /━/g'; }

spin() { # spin PID MESSAGE
    local f='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏' i=0
    tput civis
    while kill -0 "$1" 2>/dev/null; do
        printf '\r  %s%s%s %s' "$(fg "$P")" "${f:i++%10:1}" "$R" "$2"; sleep 0.1
    done
    tput cnorm; printf '\r\e[K'
    wait "$1"
}

index_progress() {
    local py=$APP/.venv/bin/python top st done total pct w=40 fill
    "$py" "$APP/index.py" >"$APP/cache/install-index.log" 2>&1 &
    local pid=$!
    tput civis
    while kill -0 "$pid" 2>/dev/null; do
        read -r st done total < <("$py" -c "import json;s=json.load(open('$APP/cache/status.json'));print(s.get('state','-'),s.get('done',0),s.get('total',0))" 2>/dev/null || echo "- 0 0")
        if [[ $st == indexing ]] && ((total)); then
            pct=$((done * 100 / total)); fill=$((pct * w / 100))
            printf '\r  %s%s%s%s%s %s%3d%%%s  %s%d / %d fonts%s\e[K' "$(fg "$P")" "$(bar $fill)" \
                "$(fg "$OV")" "$(bar $((w - fill)))" "$R" "$B" "$pct" "$R" "$(fg "$ONV")" "$done" "$total" "$R"
        else
            printf '\r  %slooking for fonts…%s\e[K' "$(fg "$ONV")" "$R"
        fi
        sleep 0.4
    done
    tput cnorm; printf '\r\e[K'
    wait "$pid"
}

write_config() {
    "$APP/.venv/bin/python" - "$APP/config.json" "$LIB" "$ENABLE_DIR" "$(icon_file "$ICON")" <<'EOF'
import json, os, sys
path, lib, enable, icon = sys.argv[1:]
try:
    cfg = json.load(open(path))
except (OSError, ValueError):
    cfg = {}
cfg.update(library=lib, enable_dir=enable, icon=icon)
cfg.setdefault("google_root", lib)
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
os.replace(tmp, path)
EOF
}

do_install() {
    local f n
    title "Installing Font Maniac"
    trap 'tput cnorm' EXIT

    if [[ $APP != "$SRC" ]]; then
        mkdir -p "$APP" || die "Couldn't create $(tilde "$APP"). Do you have write access there?"
        for f in "${APP_FILES[@]}"; do
            [[ -e $SRC/$f ]] || continue
            if [[ -d $SRC/$f ]]; then mkdir -p "$APP/$f" && cp -a "$SRC/$f/." "$APP/$f/"
            else cp -a "$SRC/$f" "$APP/$f"; fi
        done
        ok "Copied the app to $(tilde "$APP")"
    fi
    mkdir -p "$APP/cache"
    chmod +x "$APP/run.sh" "$APP/install.sh" 2>/dev/null

    if [[ ! -x $APP/.venv/bin/python ]]; then
        python3 -m venv "$APP/.venv" >"$APP/cache/install.log" 2>&1 &
        spin $! "Making a Python environment inside the app folder" ||
            { tail -5 "$APP/cache/install.log"; die "Couldn't make the Python environment."; }
    fi
    "$APP/.venv/bin/python" -m pip install --upgrade --disable-pip-version-check -q -r "$APP/requirements.txt" \
        >>"$APP/cache/install.log" 2>&1 &
    spin $! "Installing fontTools, Pillow and Brotli" ||
        { tail -8 "$APP/cache/install.log" | sed 's/^/  /'; die "pip couldn't install them. Are you online?"; }
    ok "Python packages ready"

    write_config || die "Couldn't write $(tilde "$APP")/config.json."
    ok "Settings saved"

    case $GOOGLE in
        now)
            say "Downloading Google Fonts (1.3 GB, this takes a while)…"; echo
            git clone --depth 1 --progress "$GOOGLE_URL" "$LIB/Google" && ok "Google Fonts downloaded" ||
                bad "The download failed. You can try again later from Settings in the app." ;;
        update)
            git -C "$LIB/Google" pull --ff-only --progress && ok "Google Fonts updated" || bad "The update failed." ;;
    esac

    echo; say "Indexing your fonts (only this first time; later starts just pick up changes)"
    if index_progress; then
        n=$("$APP/.venv/bin/python" -c "import json;print(json.load(open('$APP/cache/status.json')).get('faces',0))" 2>/dev/null)
        ok "Indexed ${n:-your} font face$(plural "${n:-2}")"
    else
        bad "Indexing hit a problem (see $(tilde "$APP")/cache/install-index.log). The app will retry when it starts."
    fi

    local icon_path="$APP/icons/$(icon_file "$ICON")"
    if [[ $LAUNCHER == yes ]]; then
        mkdir -p "$APPS_DIR"
        cat >"$APPS_DIR/font-maniac.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Font Maniac
GenericName=Font Manager
Comment=Browse, filter and enable fonts from a big collection
Exec="$APP/run.sh"
Icon=$icon_path
Categories=Graphics;Utility;
Keywords=font;fonts;typeface;typography;google fonts;
Terminal=false
StartupWMClass=chrome-127.0.0.1__-Default
EOF
        command -v update-desktop-database >/dev/null && update-desktop-database -q "$APPS_DIR" 2>/dev/null
        ok "Added Font Maniac to the app menu (icon: $(icon_file "$ICON"))"
    elif [[ -f $APPS_DIR/font-maniac.desktop ]]; then
        mv -f "$APPS_DIR/font-maniac.desktop" "$CONF_DIR/font-maniac.desktop.off" 2>/dev/null
    fi
    if [[ $CMD == yes ]]; then
        mkdir -p "$BIN_DIR" && ln -sfn "$APP/run.sh" "$BIN_DIR/font-maniac" && ok "Added the font-maniac command"
    fi

    mkdir -p "$CONF_DIR"
    { printf 'APP=%q\nLIB=%q\nLAUNCHER=%q\nCMD=%q\nICON=%q\n' "$APP" "$LIB" "$LAUNCHER" "$CMD" "$ICON"; } >"$RECORD"

    echo; ok "Font Maniac is installed"
    [[ $LAUNCHER == yes ]] && dim "Open it from your app menu" || dim "Start it with $(tilde "$APP")/run.sh"
    [[ $CMD == yes ]] && dim "or type font-maniac in a terminal."
    dim "Run this installer again any time to update or change settings."
    command -v notify-send >/dev/null && notify-send -i "$icon_path" "Font Maniac" "Installed" 2>/dev/null
    key
}

# ------------------------------------------------ uninstall
del() { # del PATH...   (prints instead with --dry-run)
    if ((DRY)); then printf '  %swould remove%s %s\n' "$(fg "$ONV")" "$R" "$*"; else rm -rf -- "$@"; fi
}

do_uninstall() {
    [[ -f $RECORD ]] || { title "Uninstall Font Maniac"; say "Font Maniac isn't installed (no $(tilde "$RECORD"))."; key; exit 0; }
    # shellcheck disable=SC1090
    source "$RECORD"
    local n=0 keep_app=0
    [[ -d $ENABLE_DIR ]] && n=$(find "$ENABLE_DIR" -maxdepth 1 -type l | wc -l)
    [[ $APP == "$SRC" || ! -f $APP/fontcore.py || $APP == "$HOME" || $APP == / ]] && keep_app=1
    menu "Uninstall Font Maniac" "Your font files are never touched.   Esc  cancel" < <(
        row go "Remove Font Maniac" "Enter"
        printf ' \t\n'
        row - "App menu entry + icon" "$(tilde "$APPS_DIR")/font-maniac.desktop"
        row - "Command" "$(tilde "$BIN_DIR")/font-maniac"
        row - "Enabled fonts" "$n font$(plural "$n") switched off (only the links in $(tilde "$ENABLE_DIR"))"
        if ((keep_app)); then row - "App folder" "kept: $(tilde "$APP")"
        else row - "App folder" "$(tilde "$APP")  (app, settings and font index)"; fi
    ) | grep -qx go || exit 0

    title "Uninstalling Font Maniac"
    del "$APPS_DIR/font-maniac.desktop"
    [[ -f $ICON_DIR/font-maniac.png ]] && del "$ICON_DIR/font-maniac.png"
    [[ -L $BIN_DIR/font-maniac ]] && del "$BIN_DIR/font-maniac"
    if [[ -d $ENABLE_DIR ]]; then
        if ((DRY)); then del "$n link$(plural "$n") in $ENABLE_DIR"
        else find "$ENABLE_DIR" -maxdepth 1 -type l -delete; rmdir "$ENABLE_DIR" 2>/dev/null; fc-cache >/dev/null 2>&1 & fi
    fi
    ((keep_app)) || del "$APP"
    del "$CONF_DIR"
    command -v update-desktop-database >/dev/null && ((!DRY)) && update-desktop-database -q "$APPS_DIR" 2>/dev/null
    echo; if ((DRY)); then ok "Dry run: nothing was removed"; else ok "Font Maniac is removed"; fi
    ((keep_app)) && [[ $APP == "$SRC" ]] && dim "This folder is still here; delete it yourself if you're done with it."
    key
}

# ------------------------------------------------ main
usage() {
    cat <<EOF
Usage: ./install.sh [--uninstall] [--dry-run]

Installs Font Maniac step by step (run it again to update or change settings).
  --uninstall   remove the app menu entry, command, enabled-font links and app folder
  --dry-run     with --uninstall: only list what would be removed
Your font files are never moved, changed or deleted.
EOF
}

ACTION=install
for a in "$@"; do
    case $a in
        --uninstall) ACTION=uninstall ;;
        --dry-run) DRY=1 ;;
        -h|--help) usage; exit 0 ;;
        *) usage; exit 1 ;;
    esac
done
[[ -t 0 && -t 1 ]] || { echo "Run this in a terminal: ./install.sh"; exit 1; }

check_deps
[[ $ACTION == uninstall ]] && { do_uninstall; exit 0; }

APP="" LIB="" GOOGLE=later LAUNCHER=yes CMD=no ICON=lavender
if [[ -f $RECORD ]]; then
    # shellcheck disable=SC1090
    source "$RECORD"
    # Settings changed inside the app are saved in config.json and win over this installer's record.
    cfg_lib=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("library", ""))' \
              "$APP/config.json" 2>/dev/null)
    [[ -n $cfg_lib ]] && LIB=$cfg_lib
    pick=$( {
        row update "Update" "copy this version over $(tilde "$APP"), keep all settings"
        row change "Change settings" "go through the steps again"
        row remove "Uninstall"
    } | menu "Font Maniac is already installed" "$HINT") || exit 0
    case $pick in
        update) [[ -d $APP ]] || { title "Update"; bad "$(tilde "$APP") is gone; choose Change settings."; key; exit 1; }
                step_icon || { clear; echo "Nothing was changed."; exit 0; }
                do_install; exit 0 ;;
        remove) do_uninstall; exit 0 ;;
    esac
fi

if ((${#OPTIONAL[@]})); then
    title "Font Maniac"
    say "Optional, install later if you want:"; echo
    for o in "${OPTIONAL[@]}"; do dim "·  $o"; done
    key
fi

steps=(step_app step_lib step_google step_launcher step_icon step_cmd step_confirm)
i=0 BACKING=0
while ((i < ${#steps[@]})); do
    ${steps[i]}
    case $? in
        0) ((i++)); BACKING=0 ;;
        1) BACKING=1; ((i == 0)) && { clear; echo "Nothing was installed."; exit 0; }; ((i--)) ;;
        *) ;;   # 2 = stay on this step (sub-screen went back)
    esac
done
do_install
