# Daylight Commander

**Daylight Commander** is a fast, dual-pane TUI file commander for the terminal,
built with [Textual](https://github.com/Textualize/textual). It brings the
classic Norton Commander workflow — two independent directory panes, F-key file
operations, and keyboard-first navigation — to a modern, themable Python
application.

## Screenshots

| | |
| --- | --- |
| ![Daylight Commander](docs/screenshots/screenshot-1.png) | ![Daylight Commander](docs/screenshots/screenshot-2.png) |
| ![Daylight Commander](docs/screenshots/screenshot-3.png) | ![Daylight Commander](docs/screenshots/screenshot-4.png) |
| ![Daylight Commander](docs/screenshots/screenshot-5.png) | ![Daylight Commander](docs/screenshots/screenshot-6.png) |
| ![Daylight Commander](docs/screenshots/screenshot-7.png) | |

## Features

- **Dual-pane listing** — two independent directory panes with cursor
  navigation, type-ahead jumping, and hidden-file filtering (`/`).
- **File operations** — copy, move, rename, delete to trash, permanent wipe,
  new file, and new directory, all with confirmation dialogs.
- **Format-aware viewer** — inspect files in a viewer with pretty-printed
  JSON, line counts, and a find function; keep two viewer tabs open at once.
- **Sortable columns** — cycle sort keys and reverse the order on demand.
- **Command palette** — every action is reachable from the palette; the
  palette and the key footer are generated from the same binding table, so
  they can never disagree.
- **Runtime theming** — switch color themes live from the settings page.
- **Properties page** — detailed file metadata (size, permissions, owner,
  checksums) with per-field copy.
- **External launches** — open files with the system handler, edit in
  `$EDITOR`, and open URLs found in files with the default browser.

## Install

Daylight Commander requires **Python ≥ 3.13**. It is distributed via GitHub
(not PyPI); use [uv](https://docs.astral.sh/uv/) or `pip` to install it.

### Option 1 — install directly from GitHub (no checkout, no manual download)

With uv:

```bash
uv tool install "git+https://github.com/sanfilippo-smeup/daylight-commander.git"
```

Or with pip:

```bash
pip install "git+https://github.com/sanfilippo-smeup/daylight-commander.git"
```

Then run `daylight`.

### Option 2 — install from a release asset

Download `daylight_commander-1.0.0-py3-none-any.whl` from the
[latest release](https://github.com/sanfilippo-smeup/daylight-commander/releases/latest), then:

```bash
uv tool install ./daylight_commander-1.0.0-py3-none-any.whl
# or
pip install ./daylight_commander-1.0.0-py3-none-any.whl
```

### Option 3 — build from a source checkout

Clone the repository, then build or install locally:

```bash
git clone https://github.com/sanfilippo-smeup/daylight-commander.git
cd daylight-commander
uv build              # → dist/daylight_commander-1.0.0-py3-none-any.whl + .tar.gz
uv tool install .     # install the local package as a tool
```

### Option 4 — run without installing

```bash
git clone https://github.com/sanfilippo-smeup/daylight-commander.git
cd daylight-commander
uv run daylight
```

## Quick start

```bash
daylight              # if installed (options 1–3)
uv run daylight       # from a source checkout (option 4)
```

The TUI opens with two directory panes. Move the cursor with the arrow keys,
press `Ctrl+T` to switch panes, and use the F-keys below for file
operations. Press `Ctrl+P` for the command palette; every
page (Files `Ctrl+1`, Viewers `Ctrl+2`, Properties `Ctrl+3`, Settings
`Ctrl+4`) is also reachable from the palette and its tab.

## Keybindings

| Key | Action |
| --- | --- |
| `F3` | View file |
| `F4` | Edit file in `$EDITOR` |
| `F5` | Copy to other pane |
| `F6` | Move to other pane |
| `F7` | New directory |
| `F8` | Delete (to trash) |
| `Shift+F6` | Rename |
| `Shift+F7` | New file |
| `Shift+F8` | Wipe (delete permanently) |
| `F9` | Open link externally (URL under the cursor, with confirmation) |
| `Alt+Enter` | File properties |
| `Ctrl+S` | Cycle sort column |
| `Ctrl+R` | Reverse sort order |
| `Ctrl+C` | Copy field (properties page) |
| `Ctrl+G` | Next match (viewer find) |
| `Ctrl+P` | Command palette |
| `Ctrl+T` | Switch pane |
| `Ctrl+Q` | Quit |

Navigation extras: arrows move the cursor, `Backspace` highlights the `..`
parent row (press `Enter` to ascend),
`/` starts a filter, `Space` selects, `Ctrl+A` selects all, and `Escape`
closes the viewer.

## Configuration

Settings (theme, keybindings, defaults) are edited on the built-in settings
page (`Ctrl+4`) and persisted per user to `$XDG_CONFIG_HOME/daylight/config.json`
(`~/.config/daylight/config.json` by default). Custom keybindings override the
defaults shown above; the footer and command palette always reflect the active
bindings.

## License

Licensed under the [Apache License, Version 2.0](LICENSE).