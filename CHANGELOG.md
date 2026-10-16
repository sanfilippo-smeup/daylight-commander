# Changelog

All notable changes to Daylight Commander are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-10-16

### Added

- **Directory statistics dashboard** — a read-only **Stats** tab (`Ctrl+5`) that
  shows the active directory as summary cards and dependency-free charts: disk
  usage by content category, top file extensions, a modification-timeline
  calendar heatmap + monthly bars, a size-distribution histogram, a permissions
  breakdown, and a largest / oldest / newest insights list. Computed on a worker
  thread with a safety cap (DC-023).
- Stats-dashboard screenshot in the README gallery.

### Changed

- PRD re-baselined to **Revision 4** (`FR-700…FR-709`) with **ADR-013**
  (dependency-free chart rendering + a `managers/stats.py` worker), recorded in
  `specs/CHANGES-v4.md`.

### Fixed

- The modification calendar heatmap now reports its **reference period**
  (start → end) with month/weekday labels, and a numeric activity **scale**
  (`0 … N/day`) instead of an unlabelled "less → more" legend.

## [1.0.1] - 2026-10-16

Documentation and specification patch. **No product-code changes** — the TUI
behavior is identical to 1.0.0.

### Added

- `docs/HANDOVER.md` — a delivery handover covering the delivered scope, the
  Triad+ control loop, run/verify commands, known risks, and the post-run
  Evaluator+ result.
- README sections: **"How this project was made"** (the multi-agent Triad+
  workflow) and **"Credits"** (crediting [`ainz-dg`](https://github.com/ainz-dg)
  for [Triad+](https://github.com/ainz-dg/triad-plus-engineering-loop)).
- `specs/CHANGES-v3.md` — the PRD Revision 3 change manifest.

### Changed

- Re-baselined the PRD to **Revision 3** (`specs/PRD.md`) to bring the written
  requirements in line with the owner follow-up cards DC-012…DC-022: the two-step
  go-up (`Backspace` → `..`, `Enter` ascends), the footer/palette hint split,
  visible selection marks, the viewer filling its tab, the hidden-files toggle
  living in Settings, and the Norton/mc F-key keybinding table.

### Fixed

- FR-207 wording corrected: it now states that symlinks are **listed** as normal
  entries; displaying a resolved link *target* and creating symlinks are
  explicitly deferred past v1.
- FR-301 wording corrected: YAML renders as plain text (YAML pretty-printing was
  descoped).

### Notes

- Post-run **Evaluator+** re-assessment of the delivered result returned
  **PASS**; the earlier finding (an over-claimed FR-207) was corrected here.

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