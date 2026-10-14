# Contributing to Daylight Commander

Thanks for helping improve Daylight Commander! This document explains how to
set up a development environment, how to run the checks, and the conventions
the codebase follows.

## Prerequisites

- Python >= 3.13
- [uv](https://docs.astral.sh/uv/) — manages the virtual environment,
  dependencies, and builds.

## Setup

Clone the repository, then let uv create the environment and install all
dependencies, including the dev group (`black`, `pytest`,
`pytest-textual-snapshot`, `ruff`):

```bash
uv sync
```

## Run

Start the TUI from the checkout:

```bash
uv run daylight
```

## Test

Run the full test suite:

```bash
uv run pytest -q
```

## Lint

Check the code with ruff (rules `E` and `F`, line length 100 — see
`pyproject.toml`):

```bash
uv run ruff check .
```

## Format

Format the code with black (line length 100, target Python 3.13):

```bash
uv run black .
```

## Build

Produce a wheel and an sdist in `dist/`:

```bash
uv build
```

## Layout

Source lives in `src/daylight/`:

- `app.py` — the Textual `App`; wires everything together.
- `components/modules/` — one file per page module (file listing, viewer,
  properties, settings).
- `modals/` — confirmation and form screens.
- `managers/` — filesystem and config access; no UI code here.
- `models/` — data models; no UI and no filesystem access here.
- `styles/` — all styling, in `.tcss` files only.
- `themes.py`, `config.py` — theme definitions, keybindings, and defaults.

Two rules keep the codebase predictable:

1. **Filesystem access goes only through `managers/`.** UI code never touches
   the disk.
2. **Styling lives only in `.tcss` files.** No inline styles in Python (a
   widget's own `DEFAULT_CSS` is the only exception).

## Contribution workflow

1. Create a feature branch from the repository's default branch with a
   descriptive name.
2. Make the smallest change that solves the problem.
3. Add or adjust focused tests for `managers/` and `models/` changes.
4. Run the checks before submitting:

   ```bash
   uv run ruff check .
   uv run pytest -q
   ```

5. Open a pull request with a clear description of the change.

## License

By contributing, you agree that your contributions are licensed under the
[Apache License, Version 2.0](LICENSE).