# Architecture Decision Record — Daylight Commander

| Field | Value |
| --- | --- |
| Status | Accepted (Draft) |
| Date | 2026-09-25 |
| App | Terminal file manager (TUI) |

---

## ADR-001 — Use Textual for the terminal UI framework

### Context
We need a rich terminal UI: tables, modals, command palette, theming, mouse
support, and composable widgets. Building this on raw curses/ANSI is slow and
error-prone.

### Decision
Use **Textual** (>= 1.0) as the UI framework.

### Consequences
- Fast, declarative widget composition via `compose()`.
- Built-in `DataTable`, `CommandPalette`, modal screens, and reactive attributes.
- Theming via `ColorSystem` + design tokens, styled exclusively in `.tcss`.
- Keyboard bindings rendered automatically in the `Footer`.
- Large, active project with good docs and a snapshot-testing plugin.

### Alternatives considered
- **Raw curses / `unicurses`** — maximum control, far more effort, no mouse
  story, no styling system. Rejected.
- **`urwid`** — mature but dated visuals and less ergonomic styling. Rejected.
- **`ratatui` (Rust)** — excellent, but conflicts with the Python >= 3.13 /
  `uv` requirement. Rejected.

---

## ADR-002 — Package layout: modular `src/` structure

### Context
The app must be clean and reusable so viewers, panes, and actions can be added
later without touching the core.

### Decision
Adopt the layered structure below, keeping UI and filesystem logic separate:

```
src/daylight/
  __main__.py      # entry point -> console_script "daylight"
  app.py           # App(TextualApp): root layout, BINDINGS, COMMANDS, PAGES
  themes.py        # Theme model -> ColorSystem + get_css_variables()
  config.py        # keybindings, defaults, symbols
  styles/          # index.tcss + per-page .tcss
  components/
    modules/       # one file per page module (e.g. file_list.py)
  modals/          # ModalContainer + confirm/form screens
  forms/           # declarative Form/FormField schemas
  managers/        # filesystem + config access (no UI)
  models/          # data models (FileEntry, Theme, AppConfig)
```

### Consequences
- Clear dependency direction: `modules -> managers -> models`; `app` wires it all.
- Each page is a `Static` composing "module" sub-widgets that read shared state
  from their parent page and expose a `rebuild()` method.
- Styling lives only in `.tcss`; Python code has no inline styles (except a
  widget's own `DEFAULT_CSS`).

---

## ADR-003 — Filesystem access behind a `managers/` layer

### Context
UI widgets must never touch the disk directly; that would couple rendering to
I/O and make testing painful.

### Decision
Isolate all filesystem operations in `managers/` (e.g. `FileManager`), exposing
functions like `list_dir(path)`, `stat(path)`, `copy`, `move`, `delete`,
`open_external`, `open_link`. UI modules call these only.

### Consequences
- Unit-testable with a temp directory fixture; no TUI needed.
- Async operations (large copies/deletes) can be offloaded without UI changes.
- Cross-platform launchers (`open`/`xdg-open`) and trash handling centralized.

---

## ADR-004 — Theming via design tokens and a `Theme` model

### Context
We need multiple built-in themes, switchable at runtime, with colors expressed
as design tokens rather than hardcoded values.

### Decision
Define a `Theme` model with color fields; map it to a Textual `ColorSystem`
via `to_color_system()`. The `App` implements `get_css_variables()` to expose
theme colors as CSS variables (`$accent`, `$surface`, `$panel`, `$background`).
Switching a theme calls `refresh_css()`.

### Consequences
- All `.tcss` uses tokens; adding a theme = new token values, no CSS edits.
- Active theme persists in user config.

---

## ADR-005 — Navigation model: persistent header + paged content

### Context
The app has several distinct views (Files, viewers, Properties, Settings) that
share a header and footer.

### Decision
Implement a `PAGES` list (`[{"name", "class"}]`) on the `App`, mount one page
at a time into a `.content` container, and drive it from `Tabs` via
`on_tabs_tab_activated` (unmount current, mount selected). Viewers and external
launchers are opened as modal/pushed screens rather than tabs where transient.

### Consequences
- Persistent header/footer across pages; clean multi-page app.
- Transient viewers use `push_screen`, keeping tab state simple.

---

## ADR-006 — Data table as the primary listing surface

### Context
The file listing must be fast, sortable, filterable, and keyboard/mouse
navigable.

### Decision
Use Textual `DataTable` with `cursor_type="row"` and `zebra_stripes=True`.
Build rows (Rich markup: colors, emoji, italics) in a dedicated "table builder"
module separate from the widget. Row actions (edit/delete/preview) are triggered
by `RowHighlighted` / `RowSelected` events.

### Consequences
- Sorting and filtering stay in the manager/table-builder layer, not in the widget.
- Rich markup enables type icons and metadata styling in the listing.

---

## ADR-007 — Declarative forms for create/edit actions

### Context
New file/folder, rename, and settings edits are repetitive form UIs.

### Decision
Model a `Form`/`FormField` schema supporting `string`, `number`, `integer`,
`boolean`, `autocomplete`, `date`, `hidden`. Generate the input widgets from the
schema. Support `deepcopy` of a form blueprint with populated defaults to reuse
a single definition for create vs. edit.

### Consequences
- Forms are defined as data, not bespoke widgets; easy to add fields.
- Reusable `ModalContainer` (scrollable + header close/back + footer) hosts forms
  and confirmations via `push_screen`.

---

## ADR-008 — Config centralized in `config.py`

### Context
Keybindings, defaults, and symbols should not be scattered.

### Decision
Centralize them in `config.py`, referenced by `BINDINGS` and by module code
(e.g. `CONFIG.hotkeys.new`, `CONFIG.symbols.amount_positive`).

### Consequences
- Single source of truth; future YAML/user-override can replace the module
  without touching call sites.

---

## ADR-009 — Tooling and conventions

### Decision
- Python >= 3.13, dependencies and packaging via `uv` and `pyproject.toml`.
- `black` for formatting; `ruff` with rules `E`, `F` for linting.
- Type-hint everything; `compose()` annotated with `ComposeResult`.
- Tests with `pytest`, plus `pytest-textual-snapshot` for UI snapshots.
- `[project.scripts]` entry point: `daylight = "daylight.__main__:cli"`.

### Consequences
- Consistent style enforced by tooling rather than review.
- Runnable via `uv tool install .` -> `daylight`.

---

## Open questions

1. **Trash vs. permanent delete default** — decide per-platform default and how
   `FR-206` maps to `trash`/`rm`.
2. ~~**Dual-pane vs. single-pane default**~~ — resolved: dual-pane is the
   default; the inactive panel hosts the internal viewer (FR-306).
3. **Markdown rendering engine** — choose between Textual's `Markdown` widget
   (preferred) or a custom renderer for tighter control.

## ADR-010 — Internal viewer: split-pane, format-aware, never fullscreen

### Context
MC's viewer/editor are fullscreen and use poor editors. Daylight must keep the
user in context: preview inline in the *other* panel and edit with the user's
own tools.

### Decision
- The internal viewer opens in the **inactive panel** (focused left tree →
  viewer right; focused right tree → viewer left). It is never a fullscreen
  modal.
- The viewer is **format-aware**: `.md` renders via Textual `Markdown`; JSON and
  YAML are pretty-printed; plain/source text shows with line numbers; binary
  files are never rendered inline and instead launch the OS default viewer
  (e.g. `open` on macOS).
- Editing dispatches by type: text-based files to `$EDITOR` (vim by default),
  non-text files to the OS default application.

### Consequences
- Dual-pane becomes the default layout so the viewer has a natural home.
- File-type detection (text/binary/MIME) lives in `managers/`, keeping the UI
  free of disk and sniffing logic.
- No custom editor is built; the user's `$EDITOR` and OS defaults are reused.
