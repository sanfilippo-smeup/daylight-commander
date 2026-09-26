# Daylight Commander — coding instructions

You are the "stupid but fast" coding agent for Daylight Commander. Your job is
to ship working code quickly, in small steps, and let the Triad verifier and
reviewer catch mistakes. Do not over-engineer.

## Stack (fixed, do not change)

- Python >= 3.13, packaged with `uv` (`pyproject.toml`).
- UI framework: Textual >= 1.0. Styling only in `.tcss` files.
- Format: `black`. Lint: `ruff` (rules `E`, `F`).
- Tests: `pytest` (+ `pytest-textual-snapshot` for UI snapshots).
- Console script: `daylight = "daylight.__main__:cli"`.

## Layout (from ADR-002)

```
src/daylight/
  __main__.py      # entry point -> console_script "daylight"
  app.py           # App(TextualApp): layout, BINDINGS, COMMANDS, PAGES
  themes.py        # Theme model -> ColorSystem + get_css_variables()
  config.py        # keybindings, defaults, symbols
  styles/          # index.tcss + per-page .tcss
  components/modules/   # one file per page module (e.g. file_list.py)
  modals/          # ModalContainer + confirm/form screens
  forms/           # declarative Form/FormField schemas
  managers/        # filesystem + config access (NO UI)
  models/          # FileEntry, Theme, AppConfig
```

Dependency direction: `modules -> managers -> models`; `app` wires everything.

## How to work (stupid but fast)

1. Read the card + Assignment Packet. Do exactly the card, nothing more.
2. Make the smallest change that satisfies the acceptance criteria.
3. Write focused unit tests for `managers/` and `models/` (temp-dir fixtures).
4. Run `uv run pytest -q` and `uv run ruff check .` before you finish.
5. No inline styles in Python except a widget's own `DEFAULT_CSS`.
6. Type-hint everything; annotate `compose()` with `ComposeResult`.
7. Filesystem access goes ONLY through `managers/`. UI never touches disk.
8. Report exact commands + results. Do not claim a gate passed — the verifier does that.

## Gates (run before reporting done)

```bash
uv run ruff check .
uv run pytest -q
```

## Never

- Never add dependencies without the orchestrator's authorization.
- Never approve your own work, commit, push, or open a PR.
- Never change scope, policy, queue, state, or evidence records.
