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
4. ~~**Right-panel tabsheet contents**~~ — resolved: Tab 1 is the directory
   view, Tab 2 is the file viewer, shown only when a text file is opened.
   See ADR-011.

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

---

## ADR-011 — Right panel becomes a two-tab tabsheet

### Context
The delivered app stacks a directory view on top and a file viewer on the bottom
of the right panel. The owner wants these as two switchable tabs instead of a
single stacked region, with the file viewer shown only when it has something to
show.

### Decision
Replace the single right panel with a **two-tab tabsheet**:
- **Tab 1 — Directory**: the inactive directory listing, always visible.
- **Tab 2 — Viewer**: the internal file viewer, shown (and brought to the
  front) only when a text file is opened; otherwise hidden or empty.

The viewer tab preserves ADR-010's "never fullscreen, opens on the right when the
left is focused" rule.

### Consequences
- The right side keeps the directory in context while a file is previewed, in
  separate tabs rather than a stacked split.
- The viewer tab is conditionally visible: empty/absent until a text file is
  opened, then it activates and holds the preview.
- Snapshot tests and the `.panel-mount` contract change; DC-002's mount point
  moves into the viewer tab.

---

## ADR-012 — Sortable column headers with a visible order indicator

### Context
Sorting was reachable only via the keyboard (`s` / `r`) and the active sort was
not visually obvious. The owner wants mouse-driven sorting plus a clear indicator.

### Decision
Make each listing column header clickable: a click sorts that column ascending,
a second click sorts it descending. The current sort column and direction are
shown with an arrow icon next to the header. Sorting logic stays in the
manager/table-builder layer (ADR-006); the widget only exposes the click and
renders the indicator.

### Consequences
- Keyboard sorting (`s`/`r`) remains and stays in sync with the header indicator.
- The table builder returns sort key + direction so the widget can render the arrow.

## ADR-013 — Directory statistics dashboard: dependency-free charts and a stats worker

### Context
The owner wants a read-only "Stats" tab showing the current directory as a
dashboard of cards and charts (bar, share/donut, calendar heatmap, histogram).
The project's rules: filesystem access only through `managers/` (ADR-003), all
colors from theme tokens (ADR-004), and no new dependencies without
authorization.

### Decision
- Add a Stats page as a fifth tab (`components/modules/stats_page.py`),
  wired through the existing page/tab/palette registry and `Ctrl+5`.
- Statistics are computed in a new `managers/stats.py`: a recursive walk with a
  safety cap (entry count + elapsed budget) that never touches the UI. The
  computation runs on a Textual worker thread
  (`@work(thread=True, exclusive=True, group="stats")`), and results are cached
  per directory.
- Charts are rendered dependency-free with Rich primitives — block-run bars, a
  colored-cell calendar heatmap, and a donut / 100%-stacked share bar — using
  colors read from the `Theme` model. No plotting library is added.
- The dashboard recomputes when shown, when the active directory changes while
  visible, and via an explicit refresh binding.

### Consequences
- No new runtime dependency; packaging is unchanged.
- Recursive scans of very large trees stop at the cap and are labeled "(partial)".
- Chart fidelity is terminal-grade (block/braille), not an axis plotting library.

### Alternatives considered
- `textual-plotext`/`plotext`: real axes and line plots, but adds a dependency
  and does not natively cover calendar heatmaps or pie/donut shares; rejected to
  keep the project dependency-free.

---

## ADR-014 — AI dashboard builder: embedded opencode server + sandboxed script runner

### Context
The owner requested a POC "AI dashboard builder": the user describes a dashboard
in natural language, the harness generates Python, runs it in a sandbox, and
shows the result live, then saves it for reuse. The project rules are strict:
filesystem/process access only through `managers/` (ADR-003), no new third-party
dependency without authorization, and the app must keep working when the feature
is unavailable.

### Decision
- **Embed opencode as a managed subprocess.** The builder starts
  `opencode serve` on a loopback port, discovers the port from the server's
  startup line, and drives it over its HTTP API using only the Python standard
  library (`urllib.request`, `json`, `threading`). No new Python dependency.
- **Session protocol.** Create a session (`POST /session`), send the user's
  request asynchronously (`POST /session/{id}/prompt_async`), and consume the
  server-sent event stream (`GET /event`) to stream assistant text parts. Pending
  clarifying questions (`GET /question`) are answered with
  `POST /question/{id}/reply`; permission requests (`GET /permission`) are
  answered with `POST /permission/{id}/reply`.
- **Generated-script contract.** The agent is instructed to return a single
  self-contained Python script (standard library only, plus the already-present
  `rich`) that reads data from its working directory and prints a text/ANSI
  dashboard to stdout. The harness extracts the fenced code block.
- **Sandbox.** The script runs via `subprocess` with `python -I`, a temporary
  working directory, a wall-clock timeout, and POSIX `resource` limits (CPU,
  address space, file size, no core dumps). stdout is streamed line-by-line to
  the UI. This is a best-effort POC boundary, not OS-level isolation.
- **Persistence.** Saved dashboards (name, prompt, script, timestamps) are stored
  as JSON under the per-user data directory and can be reloaded and re-run.
- **Graceful degradation.** If the `opencode` binary is missing or the server
  fails to start, the builder reports an actionable message and the rest of the
  app is unaffected.

### Consequences
- No new runtime dependency; packaging is unchanged.
- The feature requires the `opencode` binary at runtime and is therefore
  optional and POC-scoped.
- The sandbox limits blast radius (time, memory, file size, no shell) but does
  not isolate the network or the filesystem beyond a temporary working directory.
- All process/HTTP/filesystem logic stays in `managers/`, so it is unit-testable
  with fakes and never blocks the UI thread.

### Alternatives considered
- **`opencode run --format json` one-shot per turn**: simpler, but it cannot
  carry a multi-turn clarifying-question conversation or stream partial output;
  rejected.
- **A third-party HTTP client (`httpx`/`requests`)**: ergonomic, but adds a
  dependency; the standard library is sufficient for a loopback JSON API.
- **A real container sandbox (Docker/`bubblewrap`)**: stronger isolation, but
  adds an external runtime dependency and is out of scope for a POC.
