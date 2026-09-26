# Product Requirements Document — Daylight Commander (working title)

> A user-friendly, keyboard-first terminal file manager in the spirit of
> Midnight Commander, built on Textual.

| Field | Value |
| --- | --- |
| Status | Draft |
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
`tmux`; wants a file manager that respects muscle memory (Vim-style `hjkl`),
supports the mouse when convenient, and never gets in the way.

Secondary: ops engineers and power users who manage many files across projects
and servers.

## 4. Core principles

1. **Discoverable** — every binding is shown in the footer; the command palette
   lists every action.
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
- **FR-102** — Navigate directories with `Enter`, go up with `..`/Backspace.
- **FR-103** — Dual-pane layout by default: two side-by-side directory panels.
  The active panel is where you browse; the inactive panel hosts the internal
  viewer while previewing a file (single-pane mode remains available).
- **FR-104** — Filter-as-you-type (`/` or typing filters the visible rows).
- **FR-105** — Sort by name, size, modified time, or type; ascending/descending.
- **FR-106** — Show file metadata in the listing: size (human-readable), modified
  time, permissions, and an icon/emoji for the type.
- **FR-107** — Show hidden files via a toggle; remember the preference.
- **FR-108** — Breadcrumb / path bar showing the current directory.

### 5.2 File operations (FR-200)
- **FR-201** — Copy, move, rename, and delete files and directories.
- **FR-202** — Create files and directories.
- **FR-203** — Multi-select for batch operations (space toggles selection).
- **FR-204** — Destructive operations show a confirmation modal.
- **FR-205** — Long operations run non-blocking with a progress indicator and
  cancel option.
- **FR-206** — Delete moves to OS trash when available; permanent delete is an
  explicit separate action.
- **FR-207** — Symlink creation and display (show link targets).
- **FR-208** — Change permissions / ownership (chmod/chown) from a properties view.

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
- **FR-306** — **Split-pane preview**: the internal viewer opens in the
  inactive panel — focused on the left tree, the viewer is on the right, and
  vice-versa. Never fullscreen.

### 5.4 External launchers (FR-400)
- **FR-401** — **File launcher**: open a file with the OS default application
  (e.g. `open`/`xdg-open`); e.g. PDFs, images.
- **FR-402** — **Link launcher**: detect and open URLs (in Markdown/text files)
  with the default browser, after an explicit confirm.
- **FR-403** — Launching is opt-in (never auto-opens); show what will open and
  with which app.

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
| `hjkl` / arrows | Move cursor |
| `Enter` | Open / enter directory |
| `Backspace` | Go to parent directory |
| `Space` | Toggle selection |
| `F3` or `v` | View in inactive panel (md/json/yaml/text inline; binary → OS viewer) |
| `F4` or `e` | Edit (text → vim/`$EDITOR`, non-text → OS default app) |
| `F5` or `c` | Copy |
| `F6` or `m` | Move |
| `F7` or `n` | New file/dir |
| `F8` or `d` | Delete |
| `F9` or `o` | Open externally |
| `/` | Filter / search |
| `Ctrl+P` | Command palette |
| `Ctrl+T` | New tab / pane toggle |
| `Ctrl+Q` | Quit |

> All bindings are configurable via `config`; the footer always shows the active set.

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
