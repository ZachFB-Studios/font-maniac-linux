# Font Maniac

**A font manager for Linux that doesn't suck.**

https://github.com/user-attachments/assets/ec5c2fb6-a3ef-46af-959d-84d3f297d426

Have you ever tried to use the default Linux font manager? It struggles and freezes with just a small
number of fonts.

The freemium option? Doesn't even work well, and when I enable fonts in that app, they don't show up on
my system. And you want to sort your fonts or use some basic feature? Paywall.

So font managers on Linux suck. They crawl to a halt and can't browse any large collection. Claude
one-shot this in like 5 minutes. I had to move a few buttons and UI elements around, but it's imo the
only good font manager for Linux.


https://github.com/user-attachments/assets/29b35264-9037-45ef-aa75-104f7b66a7ed


## The big flexes

- **Huge libraries, no freezing.** Built and tested on a 27,000-font collection. The whole library is
  indexed once (about 20 seconds for 27k files on a desktop CPU); after that it opens instantly and only
  picks up new or changed fonts.
- **Google Fonts built in.** One button downloads every Google font (over 2,000 families), and another
  keeps them updated. You can filter by Google's own categories and by licence.
- **Enable a whole family in one click.** Every weight and style at once. Try getting a whole family
  picked for download on Google's website: it's hard af.
- **Enabled means enabled.** Fonts show up in every app right away, with nothing copied and nothing
  moved.
- **Search by look.** Switch the search box to **Tags** and type *groovy 70s*, *horror* or *wedding*:
  you get fonts that look like it, whatever they're called. Optional, picked in the installer.

<!-- MEDIA: screenshot of the family drawer + Enable family -->

## Everything else

- Filters by **measured shape**: serif, sans, slab, script, mono, display, plus weight, contrast,
  x-height, width and slant, read from the actual letters, not just the font's name
- Filters by **writing system**: Latin, Cyrillic, Greek, Arabic, Hebrew, CJK, Japanese, Korean, Thai and
  more
- Your own **preview text** and tile size
- **Search** by family, style or file name; `in:word` searches folder names
- Browse by **folder**, sort by name, weight, contrast, x-height, width, newest or folder
- Finds **duplicate files** anywhere in your collection
- Reads TTF, OTF, TTC/OTC, WOFF and WOFF2
- Light and dark mode (follows your system)
- Your fonts are never moved, renamed or deleted

<!-- MEDIA: screenshot of filters -->

## Install

```bash
git clone https://github.com/ZachFB-Studios/font-maniac-linux.git
cd font-maniac-linux
./install.sh
```

The installer walks you through it step by step:

1. **App folder.** Where the app itself goes. I keep mine in an Apps folder on my SSD, and I suggest
   something similar: your boot drive (`~/.local/share/font-maniac` is the default) or an Apps folder
   on any drive. You can also run it straight from the folder you cloned.
2. **Font collection.** Point the app to your own fonts folder, where you keep all your fonts, in any
   subfolders you like. I keep mine in `Assets/Fonts/`. Nothing in it gets moved or installed. Fonts
   you switch on are linked into `~/.local/share/fonts/FontManiac`, the standard per-user font
   folder every distro reads.
3. **Google Fonts.** Download now (1.3 GB download, about 4 GB on disk) or later from Settings.
4. **App menu entry.** Works on every distro and desktop: GNOME, KDE, XFCE, Hyprland/Sway launchers
   (rofi, wofi, fuzzel, walker), all of them.
5. **Icon.** Pick one of six colours (Lavender, Peach, Mint, Sky, Butter, Blush). Change it any time: run the installer again and pick **Update**.
6. **A `font-maniac` terminal command**, if you want one.

Then it sets up its own Python environment inside the app folder and indexes your fonts, so the first
start is instant.

**Update:** `git pull`, then `./install.sh` and pick **Update**. Your settings are kept. (Running it
straight from the cloned folder? Then `git pull` is all you need.)

**Uninstall:** `./install.sh --uninstall` (add `--dry-run` to see what it would remove first). It
removes the app, the menu entry, the command and the links for fonts you enabled. Your font files are
never touched.

### What you need

| | |
|---|---|
| **Required** | `python3` (3.8+, with `venv`), `fzf`, `curl`, `fontconfig` |
| **Recommended** | Chromium. Fallback: Chrome, Brave, Edge, Vivaldi, Thorium, Opera (Flatpak supported), then your default browser |
| **Optional** | `git` for Google Fonts, `kdialog` or `zenity` for the Browse… buttons, an NVIDIA GPU for fast style search |

The installer checks all of this and gives you the exact install command for your distro if something
is missing. On Debian/Ubuntu that's usually:

```bash
sudo apt install python3-venv fzf curl
```

## How it works

- Font Maniac is a small local server (Python, only on `127.0.0.1`) plus a web UI in an app window.
  Closing the window stops the server.
- Index and thumbnails are kept in `cache/` inside the app folder, and settings in `config.json` there.
  Change the fonts folder any time from Settings (gear icon, top right).
- **Enable** puts a link to the font in `~/.local/share/fonts/FontManiac/` and refreshes fontconfig.
  That's the standard per-user font folder on every distro, so every app sees it. **Disable** removes
  only that link.
- **Style search** is a second index: each font is drawn on a card and turned into a vector by an
  image-text model (SigLIP 2), so a typed description can be matched against how fonts look. It runs
  in the background after each scan, or at full speed from Settings, where you can also turn it off.
- **Google Fonts** is a shallow `git clone` of
  [github.com/google/fonts](https://github.com/google/fonts) into `<fonts folder>/Google`. **Update**
  is a `git pull`.

## License

MIT. See [LICENSE](LICENSE).
