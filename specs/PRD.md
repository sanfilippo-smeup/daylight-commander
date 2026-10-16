# Product Requirements Document — Daylight Commander (working title)

> A user-friendly, keyboard-first terminal file manager in the spirit of
> Midnight Commander, built on Textual.

| Field | Value |
| --- | --- |
| Status | Draft — Revision 3 |
| Product | Terminal file manager (TUI) |
| Target users | Developers / technical users |
| Platform | Python >= 3.13, terminal (256-color / truecolor) |

---

## 1. Problem statement

Midnight Commander (MC) is powerful but intimidating: dense dual-pane layout,
cryptic `F1`–`F10` keybindings, dated visuals, and little discoverability.
Newer terminal users want the speed of a keyboard-driven file manager without
memorizing a function-key matrix.

**Daylight Commander** keeps MC's speed and keyboard-first ethos, but makes it
approachable through clear labels, a command palette, mouse support, inline
previews, and modern theming.

## 2. Goals and non-goals

### Goals
- Navigate, browse, and preview the filesystem entirely from the terminal.
- Discover every action without a manual (footer hints, command palette, menus).
- Preview common file types (text, Markdown, JSON, YAML) in a split pane and
  hand off binary files to the OS default application.
- Feel fast: instant navigation, lazy rendering, non-blocking file operations.
- Be safe: confirm destructive actions, show clear progress, no accidental data loss.

### Non-goals
- Not a full remote/virtual filesystem client (FTP/SSH/SFTP panels) in v1.
- Not a hex editor or binary editor in v1 (may hand off externally).
- Not a shell replacement or terminal multiplexer.
- Not a GUI desktop app; terminal only.

## 3. Target users

**Persona: the terminal developer** — lives in the shell, uses `git`, `vim`,
`tmux`; wants a file manager that respects keyboard-first muscle memory,
supports the mouse when convenient, and never gets in the way.

Secondary: ops engineers and power users who manage many files across projects
and servers.

## 4. Core principles

1. **Discoverable** — the footer shows the high-frequency global operations,
   page headers show contextual hints, and the command palette exposes **every**
   action with fuzzy search; nothing is reachable only by a memorized key.
2. **Keyboard-first, mouse-friendly** — full operation via keyboard; mouse
   clicking, scrolling, and selection also work.
3. **Preview-first** — see file contents before deciding to open them.
4. **Safe by default** — confirm destructive operations; support undo where the
   platform allows (trash instead of permanent delete when possible).
5. **Extensible & clean** — modular code so new panes, viewers, and actions can
   be added without rewiring the core.

## 5. Functional requirements

### 5.1 File listing / navigation (FR-100)
- **FR-101** — List the contents of a directory in a sortable, filterable table.
- **FR-102** — Navigate directories with `Enter`. Going up is an explicit
  two-step: `Backspace` moves the cursor to the `..` parent row (clearing any
  active filter first so the row is reachable) without changing directory, and
  `Enter` on that row ascends to the parent. At the filesystem root there is no
  `..` row and `Backspace` is a no-op.
- **FR-103** — Dual-pane layout by default: a directory panel on the left and a
  two-tab tabsheet on the right. Tab 1 is the directory view (always visible);
  Tab 2 is the file viewer, shown only when a text file is opened. The left panel
  is where you browse (single-pane mode remains available).
- **FR-104** — Filter-as-you-type (`/` or typing filters the visible rows). The
  filter is **cleared automatically when entering a new directory** so the next
  listing starts unfiltered.
- **FR-105** — Sort by name, size, modified time, or type; ascending/descending.
  The **active sort column and direction are always visible in the panel header**,
  and **clicking a column header sorts by that column** (ascending first, then
  toggling to descending on a second click), indicated by an arrow icon next to
  the header.
- **FR-106** — Show file metadata in the listing: size (human-readable), modified
  time, permissions, and an icon/emoji for the type.
- **FR-107** — Show hidden files via a toggle; remember the preference. The
  toggle lives in Settings → Defaults → Hidden files and persists in the config;
  there is no dedicated runtime keybinding for it.
- **FR-108** — Breadcrumb / path bar showing the current directory.
- **FR-109** — **Type-ahead prefix navigation**: while the left panel is focused,
  each keystroke moves the cursor to the first entry whose name starts with the
  accumulated prefix. If no entry matches the prefix, the cursor moves to the next
  entry in alphabetical order. A repeated keystroke advances the cursor to the
  next entry with the same prefix, and so on; when no further entry shares the
  prefix, the cursor moves to the next entry in alphabetical order.

### 5.2 File operations (FR-200)
- **FR-201** — Copy, move, rename, and delete files and directories.
- **FR-202** — Create files and directories.
- **FR-203** — Multi-select for batch operations (`Space` toggles selection).
  Selected rows are visibly marked (bold, with a leading `*`), and the mark
  survives a re-sort or re-filter.
- **FR-204** — Destructive operations show a confirmation modal.
- **FR-205** — Long operations run non-blocking with a progress indicator and
  cancel option.
- **FR-206** — Delete moves to OS trash when available; permanent delete is an
  explicit separate action.
- **FR-207** — List symlinks as entries in the directory listing; they are
  navigable and operable like other entries. Showing a resolved link *target*
  and creating symlinks from the UI are **deferred** past v1 (never scheduled in
  a delivery card).
- **FR-208** — Change permissions / ownership (chmod/chown) from a properties
  view. **Deferred past v1** (explicitly out of scope in DC-003); the properties
  view is read-only for permissions and ownership.

### 5.3 Viewers (FR-300)
- **FR-301** — **Plain text / code viewer**: display UTF-8 text with line
  numbers, search, and syntax-aware large-file handling; pretty-format JSON and
  YAML for readability.
- **FR-302** — **Markdown viewer**: render Markdown to rich text (headings,
  lists, code blocks, tables, links) inside the terminal.
- **FR-303** — **Detail / properties view**: stat info (size, inode, times,
  permissions, owner, group, MIME type, checksum) with copyable fields.
- **FR-304** — Read-only by default; editing hands off to the right editor:
  text-based files to `$EDITOR` (vim), non-text files to the OS default app.
- **FR-305** — **Format-aware internal viewer**: render `.md`, JSON, YAML, and
  plain/source code inline; binary files are never rendered inline and instead
  open in the OS default viewer (e.g. Preview on macOS).
- **FR-306** — **Tab preview**: the internal viewer opens in the right
  tabsheet's Viewer tab (Tab 2) — focused on the left tree, the viewer is on the
  right. It is shown only when a text file is opened and is never fullscreen
  (it fills the tab, not the whole app). The preview fills the full vertical
  height of the tab and stays visible while the left listing is navigated:
  moving the cursor never switches the right panel away from its Viewer tab.

### 5.4 External launchers (FR-400)
- **FR-401** — **File launcher**: open a file with the OS default application
  (e.g. `open`/`xdg-open`); e.g. PDFs, images.
- **FR-402** — **Link launcher**: detect and open URLs (in Markdown/text files)
  with the default browser, after an explicit confirm.
- **FR-403** — Launching is opt-in (never auto-opens); show what will open and
  with which app. The confirmation dialog has the **"Open" button focused by
  default**: pressing `Enter` triggers the open action, and the "Cancel" button
  is reachable via `Tab`.

### 5.5 Navigation & app shell (FR-500)
- **FR-501** — Persistent header with app title, current path, and page tabs.
- **FR-502** — Footer with the active keybinding hints for the current view.
- **FR-503** — Command palette (`Ctrl+P`) exposing every action with fuzzy search.
- **FR-504** — Tabbed navigation between: Files, Viewers, Properties, Settings.
- **FR-505** — Multiple themes, switchable at runtime; remember the choice.

### 5.6 Settings & config (FR-600)
- **FR-600** — Central config for keybindings, defaults, theme, and symbols.
- **FR-601** — Config stored per-user (e.g. `~/.config/daylight/`); editable
  from the UI where practical.

## 6. Screens / views

| # | Screen | Purpose |
| --- | --- | --- |
| 1 | **Files (list/table)** | Primary browsing surface; default landing view |
| 2 | **Detail / Properties** | Full metadata for the selected entry |
| 3 | **Markdown viewer** | Rendered preview of `.md` files |
| 4 | **Plain text viewer** | Read-only preview of text files |
| 5 | **External file launcher** | Hand off a file to the OS default app |
| 6 | **External link launcher** | Hand off a URL to the default browser |
| 7 | **Settings** | Theme, keybindings, defaults |

## 7. Proposed default keybindings

| Key | Action |
| --- | --- |
| arrows | Move cursor |
| type letters | Type-ahead: jump to the first entry whose name has the typed prefix |
| `Enter` | Open / enter directory (or ascend when on the `..` row) |
| `Backspace` | Move cursor to the `..` parent row (clears an active filter first); no-op at root |
| `Space` | Toggle selection |
| `F3` | View (md/json/yaml/text inline; binary → OS viewer) |
| `F4` | Edit (text → `$EDITOR`, non-text → OS default app) |
| `F5` | Copy |
| `F6` | Move |
| `Shift+F6` | Rename |
| `F7` | New directory |
| `Shift+F7` | New file |
| `F8` | Delete (to trash) |
| `Shift+F8` | Wipe (delete permanently) |
| `F9` | Open externally (file or URL) |
| `Alt+Enter` | Properties |
| `Ctrl+S` | Cycle sort column |
| `Ctrl+R` | Reverse sort order |
| `Ctrl+A` / `Ctrl+Shift+A` | Select all / clear selection |
| `Ctrl+C` | Copy field (properties view) |
| `Ctrl+G` | Next match (viewer find) |
| `/` | Filter / search |
| `Ctrl+P` | Command palette |
| `Ctrl+T` | Switch pane |
| `Ctrl+Q` | Quit |

> All bindings are configurable via `config`. The footer shows the
> high-frequency operations, page headers show contextual hints, and the
> command palette (`Ctrl+P`) exposes every action.

## 8. Non-functional requirements

- **Performance** — directory listing of 100k entries renders without blocking
  the UI (lazy/virtualized rows); operations are async.
- **Reliability** — no data loss: confirms on destructive ops; safe copy
  (atomic write where possible).
- **Portability** — works on macOS and Linux; Windows best-effort.
- **Accessibility** — full keyboard operation; theme contrast readable in
  256-color and truecolor terminals.
- **Testability** — core managers and form logic covered by unit tests; UI
  snapshots via `pytest-textual-snapshot`.
- **Maintainability** — modular structure (pages, components, managers, models)
  with clean separation of UI and filesystem logic.

## 9. Out of scope (v1)

- Remote/network filesystems (FTP/SSH/SFTP panels).
- Hex/binary editor.
- Built-in archive browsing (may open externally).
- Plugin system.

## 10. Success metrics

- A new user can navigate, preview, and copy a file within 60 seconds of first
  launch, guided only by the on-screen hints.
- Every action is reachable from the command palette.
- Directory operations on large trees complete without freezing the UI.
