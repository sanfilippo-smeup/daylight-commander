# Changelog

All notable changes to Daylight Commander are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **File types tab** (`Ctrl+7`) — ranks the active pane's tree by file
  extension: summary cards for files / distinct types / scan time, and a
  top-20 bar chart that folds extensionless files into a `(none)` row. It
  reuses the Stats worker scan and its entry cap, caches results per
  directory, follows the active pane while it is visible, and refreshes with
  `Ctrl+R`.
- Double `Enter` on a file opens it in the OS default application (with the
  same confirmation as any external launch), including plain-text files that a
  single `Enter` shows in the internal viewer.

### Changed

- The standalone `python -m daylight.filetype_dashboard` proof-of-concept is
  replaced by the in-app tab; the ranking rules stay in
  `managers/filetypes.py`, so they are unit-testable without a UI.
- The viewer reads every inline kind — Markdown, JSON, YAML and source text —
  whole, bounded by a 1 MiB / 10,000-line `VIEW_LIMIT`, and mounts it in the
  same native `VerticalScroll` Markdown already used. Mouse wheel, scrollbar,
  the viewer keys and find jumps all move one scroll position, and a resize no
  longer re-reads or re-windows the file. In the Files pane the body also takes
  arrow/page keys once Tab focuses it. Files above either limit show their first
  1 MiB / 10,000 lines with a notice.
- Search jumps to matches in every document; match highlighting is painted for
  documents up to 2,000 lines, and the status line says when it is off so a long
  file never silently loses the highlight.
- The viewer's `Ctrl+Up` / `Ctrl+Down` / `Ctrl+PageUp` / `Ctrl+PageDown`
  bindings are `priority` when a viewer is open, so the focused listing's
  `DataTable` no longer swallows the page keys with its horizontal scroll
  bindings. With no viewer open the keys fall through to the table again.
- Source lines are rendered with `text-wrap: nowrap` and `text-overflow:
  ellipsis`, so the gutter stays aligned and the document's height equals its
  line count.

### Fixed

- Every page is now reachable in macOS Terminal.app, which never transmits
  `Ctrl+1`…`Ctrl+7`: `Ctrl+N` walks to the next page tab and `Ctrl+B` to
  the previous one, wrapping at either end (palette entries "Next tab" /
  "Previous tab", both rebindable on the Settings page like every action). The
  digit shortcuts still work in terminals that do send them.
- Long Java and other source files no longer appear stuck in the viewer: their
  page keys, mouse wheel and scrollbar work exactly like Markdown's.

## [1.2.0] - 2026-10-22

### Added

- **AI dashboard builder** — a `Ctrl+6` **Builder** tab where a plain-language
  prompt is answered by an embedded [`opencode`](https://opencode.ai) agent with
  one declarative JSON dashboard specification (clarifying questions supported).
  The specification is validated against a strict allowlist and rendered in a
  dedicated **Dashboard** tab with the app's own components and theme tokens —
  summary cards, bar charts, share bars, histograms, calendar heatmap, and
  tables. No generated code, HTML, SVG, or ANSI output is ever rendered or
  executed (DC-024 … DC-032).
- **opencode v2 support** — HTTP Basic auth, the `/api` endpoints,
  `session.text.*` streaming, and `form.*` clarifying questions, with automatic
  detection of, and fallback to, the v1 protocol. When neither works, the
  Builder shows a hint and the rest of the app is unaffected (DC-027, DC-028;
  ADR-015).
- **Dashboard specification contract** — a versioned `schema_version: 1` model,
  a strict validator with path-bearing errors (1–20 blocks, allowlisted
  kind/source pairs, `top_n` 1–50, theme tokens, exact key sets), and a
  deterministic binder that turns allowlisted sources into render-ready data
  from `DirectoryStats` (DC-029).
- **Native Dashboard tab** — one reusable tab created on demand and bound to the
  dashboard's directory; `Ctrl+R` recomputes it and `Ctrl+S` / `Ctrl+L` save and
  reload the specification (DC-030, DC-031).
- **Dashboard persistence** — JSON specifications (name, prompt, spec, directory,
  timestamps) under `$XDG_DATA_HOME/daylight/dashboards`, falling back to
  `~/.local/share/daylight/dashboards`. Revision-5 script artifacts are listed
  as "(legacy)" and never executed or rendered (DC-031).
- Dashboard page snapshot plus v2 stub end-to-end coverage of the spec flow
  (DC-032).

### Changed

- README documents the native dashboard flow, its controls, the `opencode`
  prerequisite, legacy artifacts, and the POC limits.
- PRD re-baselined to **Revision 5** (`FR-801…FR-808`, `specs/CHANGES-v5.md`)
  and then **Revision 6** (native TUI dashboards, `specs/CHANGES-v6.md`).
  **ADR-014** (embedded opencode server + sandboxed script runner) is superseded
  for the Builder by **ADR-016** (declarative spec + native render); **ADR-015**
  (opencode v2 transport) remains in force.

### Fixed

- Builder worker lifecycle: worker groups are cancelled on unmount and UI posts
  are guarded after teardown, so the full test suite no longer hangs (DC-031).
- Opening a dashboard performs exactly one statistics scan instead of two
  (DC-030).
- The live `POST /session` 405 defect on opencode v2 is fixed by the
  version-aware client (DC-027).

### Notes

- The Builder is a proof-of-concept: single-user and local, bound to one
  directory per dashboard, and it requires the `opencode` CLI on `PATH`. The
  opencode v2 API is experimental upstream, so every agent reply is validated
  and never trusted.
- Gates for this release: `uv run ruff check .` clean and `uv run pytest -q` —
  802 passed, including 16 UI snapshots.

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