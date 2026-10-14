# Changelog

All notable changes to Daylight Commander are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-10-14

First stable release of **Daylight Commander**, a fast dual-pane TUI file
commander for the terminal, built with
[Textual](https://github.com/Textualize/textual).

### Added

- Runnable `daylight-commander` package (Python >= 3.13, Textual >= 1.0) with a
  `daylight` console script, packaged with `uv` and Hatchling.
- Dual-pane file listing with cursor navigation, sorting, a `/` filter, and a
  hidden-file toggle.
- File operations with confirmation dialogs: copy, move, rename, delete to
  trash, permanent wipe, new file, and new directory; multi-select with
  `Space` and `Ctrl+A`.
- Format-aware split-pane viewer: plain text/source, Markdown, and
  pretty-printed JSON, plus a properties view (size, permissions, owner,
  checksums) with per-field copy.
- Editor dispatch: text files open in `$EDITOR`, non-text files open with the
  system default handler.
- App shell with a persistent header/footer, tabs for Files, Viewers,
  Properties, and Settings, a `Ctrl+P` command palette generated from the
  binding table, and runtime theme switching.
- External launchers: open files with the system handler and URLs found in
  files with the default browser; settings persisted to
  `$XDG_CONFIG_HOME/daylight/config.json`.
- Two-tab viewer panel, so two documents can be kept open at once.
- Sortable column headers with a visible sort-order indicator.
- Type-ahead prefix navigation: typing jumps the cursor to matching entries.
- `Backspace` highlights the `..` parent row and `Enter` ascends to it.

### Changed

- Operation keys remapped to the Norton/mc F-key scheme (`F3` view, `F4` edit,
  `F5` copy, `F6` move, `F7` new directory, `F8` delete, `Shift+F6` rename,
  `Shift+F7` new file, `Shift+F8` wipe, `F9` open link, `Alt+Enter`
  properties), freeing printable letters for type-ahead.
- Key hints are generated from the binding table and split between the footer
  and panel headers by frequency of use.
- The active filter clears when entering a folder, so a stale filter never
  hides the contents of the directory you just opened.
- Removed the `Ctrl+H` hidden-files binding; hidden-file visibility is a
  Settings default that persists in the config.
- The Viewer and Properties tabs fill the full vertical space of the right
  panel instead of being capped at half height.

### Fixed

- Stale key hints left behind by the F-key remap now show the current keys.
- Navigating the listing no longer switches the right panel away from its
  Viewer tab; the preview stays visible while you move the cursor.
- Rows selected with `Space` are visibly highlighted.
- The open-externally confirmation dialog focuses `Open` by default; `Enter`
  opens and `Tab` reaches `Cancel`.
- The JSON viewer no longer truncates long files to a hard-coded 24-line
  window; the initial window matches the viewport and re-renders on resize.