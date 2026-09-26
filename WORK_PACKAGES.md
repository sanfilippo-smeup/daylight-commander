# Work packages — Daylight Commander

> Decomposition of `specs/PRD.md` into ordered Triad engineering work packages.
> Each package maps to a feature card in `.triad-control/projects/daylight-commander/features/`.

## Package map

| WP | Card | Title | FR coverage | Depends on |
| --- | --- | --- | --- | --- |
| 1 | DC-001 | Project scaffold & foundation | (foundation) | — |
| 2 | DC-002 | Dual-pane file listing & navigation | FR-100 | DC-001 |
| 3 | DC-003 | File operations | FR-200 | DC-002 |
| 4 | DC-004 | Viewers & editors | FR-300 | DC-002 |
| 5 | DC-005 | App shell, command palette, tabs, theming | FR-500, FR-600, FR-505 | DC-002, DC-004 |
| 6 | DC-006 | External launchers & final integration | FR-400, FR-600 | DC-001..DC-005 |

## Step 1 — Scaffold & foundation (DC-001)

Establish the installable package skeleton and the data/IO boundary before any
UI work.

- `pyproject.toml` with `uv`; `[project.scripts] daylight = "daylight.__main__:cli"`.
- `src/daylight/` per ADR-002; `models/` dataclasses (`FileEntry`, `Theme`, `AppConfig`).
- `config.py` (keybindings/defaults/symbols), `themes.py` (`Theme -> ColorSystem`).
- `managers/` (`FileManager`, `ConfigManager`) — the ONLY disk-touching layer.
- Minimal `app.py` + `__main__.py` that boots a Textual `App` and quits on `Ctrl+Q`.
- Unit tests for models + managers against `tmp_path`.

## Step 2 — Dual-pane file listing & navigation (DC-002)

The primary browsing surface.

- Two side-by-side directory panels with a single active-panel focus model.
- `DataTable` listing with `cursor_type="row"`, zebra stripes, type icons/emoji.
- Sort (name/size/time/type), filter-as-you-type, hidden-file toggle (persisted).
- Navigation (`Enter` down, `..`/`Backspace` up), breadcrumb/path bar.
- Lazy/virtualized rows so a 100k-entry directory stays non-blocking.
- The inactive panel exposes a stable mount point that the viewer (DC-004) uses.

## Step 3 — File operations (DC-003)

All mutations, with safety guarantees.

- `FileManager` copy/move/rename/delete/create + trash (fallback to explicit `rm`).
- Confirmation modals for destructive ops; non-blocking progress with cancel.
- Multi-select via `Space` for batch operations; declarative forms for create/rename.

## Step 4 — Viewers & editors (DC-004)

Format-aware, split-pane, never fullscreen.

- Internal viewer opens in the **inactive panel** (focused left → viewer right,
  focused right → viewer left); never a fullscreen modal.
- Format dispatch inline: `.md` → Textual `Markdown`; JSON/YAML → pretty-print;
  plain/source code → line numbers + search + syntax-aware large-file handling.
- Binary files are never rendered inline → OS default viewer (Preview on macOS).
- Properties view (stat, inode, times, perms, owner, group, MIME, checksum).
- Editor dispatch: text → `$EDITOR`/vim (`F4`/`e`); non-text → OS default app.
- Text/binary + MIME detection lives in `managers/`, not the UI.

## Step 5 — App shell & discoverability (DC-005)

Ties the pages together and makes every action discoverable.

- Persistent header (title, path, tabs) + footer (active keybinding hints).
- `Ctrl+P` command palette with fuzzy search exposing every action.
- Tabbed navigation Files/Viewers/Properties/Settings.
- Runtime theme switching via design tokens (`get_css_variables`), persisted.

## Step 6 — Launchers & final integration (DC-006)

Opt-in external handoff and end-to-end cohesion.

- `open_external`/`open_link` (`open`/`xdg-open`), confirm-before-launch.
- Settings screen for keybindings/defaults (persisted).
- Full integration pass + final gates; 60s first-launch walkthrough.

## Verification (every package)

Each package must pass both control-plane gates before review:

```bash
uv run ruff check .    # gate: lint
uv run pytest -q       # gate: test
```

The Triad verifier produces environment-derived evidence; the coding agent only
reports agent-reported claims and never marks a gate passed on its own.
