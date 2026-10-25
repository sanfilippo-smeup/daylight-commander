# Handover — Daylight Commander

- **Project:** Daylight Commander (`daylight-commander` v1.0.0, Apache-2.0)
- **Owner / author:** Mauro Sanfilippo
- **Handover date:** 2026-10-16
- **Repository:** https://github.com/sanfilippo-smeup/daylight-commander
- **Control workspace:** `.triad-control/` (Triad+ project-control workspace, not
  tracked by the product repository)
- **Triad+ engineering loop:** **1.18.0** (upgraded from 1.12.0 on 2026-10-16)

## Executive summary

Daylight Commander is a fast, dual-pane TUI file commander for the terminal,
built with [Textual](https://github.com/Textualize/textual) on Python ≥ 3.13. It
is fully delivered and released as **v1.0.0**. Twenty-two feature cards
(DC-001 … DC-022) were implemented, verified with environment-derived gate
evidence, independently reviewed, and committed under the Triad+ engineering
loop. The package builds from source, is documented, tagged `v1.0.0`, and
published as a GitHub Release.

Since the v1.0.0 delivery, the product repository gained documentation updates
(README install options and a screenshots gallery) and, on 2026-10-16, this
handover plus new README sections describing the credits and the
multi-agent workflow used to build the project.

## Releases after 1.0.0

This handover body is the v1.0.0 delivery record. The releases below were cut
afterwards from the same repository.

| Release | Date | Scope | Spec baseline |
| --- | --- | --- | --- |
| 1.0.1 | 2026-10-16 | Documentation and specification patch; **no product-code change** (README sections, this handover, FR-207/FR-301 wording fixes) | PRD Revision 3 |
| 1.1.0 | 2026-10-16 | Read-only **Stats** tab (`Ctrl+5`) with dependency-free charts computed on a worker thread and a safety cap | PRD Revision 4, ADR-013 |
| 1.2.0 | 2026-10-22 | **AI dashboard builder**: Builder tab (`Ctrl+6`), declarative specification contract, native **Dashboard** tab, opencode v2 with a v1 fallback; saved specifications under `$XDG_DATA_HOME/daylight/dashboards` (Revision-5 script artifacts are legacy and inert) | PRD Revision 6, ADR-014 / ADR-015 / ADR-016 |
| 1.3.0 | 2026-10-25 | **Terminal page navigation** (`Ctrl+N` / `Ctrl+B`, wrapping, Dashboard included while mounted) and **three Files search modes** (Filter, recursive names, recursive content; `Ctrl+F` cycles, `F2` viewer find); also delivers the File types tab (`Ctrl+7`) and the bounded native viewer with double-Enter open since 1.2.0 | PRD Revision 6 (frozen) |
| 1.3.1 | 2026-10-25 | Internal cleanup, no user-visible change: removes the unused Revision-5 sandbox manager/model/tests and the dead Builder preview styles (DC-033) | PRD Revision 6 (frozen) |

Release 1.3.0 was developed on `feat/filetype-and-viewer` and delivers cards
DC-034 … DC-037 on top of 1.2.0. Its gates were `uv run ruff check .` clean and
`uv run pytest -q` — 884 passed, including 16 UI snapshots; Evaluator+ returned
PASS (DC-EVAL-007). The frozen PRD Revision 6 and ADR were not modified.

Release 1.3.1 lands the revived DC-033 cleanup (card from the 1.2.0 cycle) on
`main` from `feat/dc-033-sandbox-cleanup`: the unused sandbox manager, model and
tests plus the dead Builder preview styles are removed with no user-visible
change. Gates: `uv run ruff check .` clean, `uv run pytest -q` — 868 passed,
16 UI snapshots (the 16 fewer are the deleted sandbox tests); Evaluator+
returned PASS (DC-EVAL-008).

Release 1.2.0 was developed on `feat/ai-dashboard-builder` and delivers cards
DC-024 … DC-032. Its gates were `uv run ruff check .` clean and
`uv run pytest -q` — 802 passed, including 16 UI snapshots. Assets:
`daylight_commander-1.2.0-py3-none-any.whl` and `.tar.gz`.

## Triad+ control loop

The project was driven by Triad+ — a lightweight engineering loop that
separates implementation, review, and orchestration. The control workspace
lives in `.triad-control/` and is intentionally kept out of the product
repository (`.gitignore`), matching the Triad+ rule that the Control Workspace
stays separate from product repositories.

| Role | Display name | Model |
| --- | --- | --- |
| Orchestrator | Coordinator | `ollama-cloud/deepseek-v4.1-flash` |
| Developer | Builder | `ollama-cloud/glm-5.3-flash` |
| Reviewer | Critic | `ollama-cloud/deepseek-v4.1-flash` |
| Evaluator+ | — | disabled |

- **Host adapter:** OpenCode (explicit-dispatch verification).
- **Managed assets:** `.opencode/agents/`, `.opencode/commands/triad.md`,
  `.opencode/skills/triad-loop-*` and `triad-model-configuration`,
  `.triad-runtime/`, and the managed `AGENTS.md` role-run block.
- **Team source of truth:** `.triad-control/.triad-plus/team.json`.
- **Upgrade evidence (2026-10-16):** `npx triad-plus@1.18.0 upgrade --host
  opencode --control .triad-control --apply` — installed `1.12.0 → 1.18.0`,
  manifest `OK`, `team.json` unchanged. `doctor` reports CLI `1.18.0`,
  installed `1.18.0`, runtime OK, verifier OK, all shared skills OK, and the
  three role models materialized as configured.

Per-card chain: Builder implements one bounded card with tests → `triad-verify`
records real gate results → Critic reviews the candidate and evidence and
approves / requests rework / blocks → Coordinator makes the state transition and
commits exactly one commit per approved card.

## Delivery record

- **Cards:** DC-001 … DC-022, all `approved` (plus metadata commit `233fc2d`
  setting the author to Mauro Sanfilippo).
- **Gates:** `uv run ruff check .` clean and `uv run pytest -q` — 559 passed
  including 14 UI snapshots — across every card, via environment-derived
  verifier evidence.
- **Branches:** `main` (default) and `feat/daylight-commander/product-repository`.
- **Release:** tag `v1.0.0` and GitHub Release with
  `daylight_commander-1.0.0-py3-none-any.whl` and `.tar.gz`.
- **PRD baseline:** `specs/PRD.md` frozen at Revision 2, SHA-256
  `533b304cf5c30353767c69233ea879ac5a0e6551f69ae094ff0f04dd6cfdb828`
  (snapshot `artifacts/prd.md` in the control workspace).

### Card-by-card results

| Card | Result | Outcome |
| --- | --- | --- |
| DC-001 | approved | Runnable package skeleton (models, config, theme, managers) |
| DC-002 | approved | Dual-pane virtualized listing, sort/filter/navigate, hidden toggle |
| DC-003 | approved | Copy/move/rename/delete/create, multi-select, confirm, progress |
| DC-004 | approved | Format-aware split-pane viewer + editor dispatch (YAML pretty-print descoped) |
| DC-005 | approved | App shell, command palette, tabs, runtime theming |
| DC-006 | approved | External launchers, settings, end-to-end integration |
| DC-007 | approved | Type-ahead prefix navigation |
| DC-008 | approved | Filter clears on folder entry |
| DC-009 | approved | Sortable column headers + visible sort indicator |
| DC-010 | approved | Right panel two-tab tabsheet (directory + conditional viewer) |
| DC-011 | approved | Open-externally dialog focus/Enter/Tab |
| DC-012 | approved | Key remap off printable letters (Norton/mc F-key scheme) |
| DC-013 | approved | Fix stale key hints left by the DC-012 remap |
| DC-014 | approved | Viewer tab fills the full vertical space |
| DC-015 | approved | Keep the viewer visible while navigating the listing |
| DC-016 | approved | Selected rows look selected (bold `*` marker, spacebar) |
| DC-017 | approved | Backspace highlights the `..` parent row; Enter ascends |
| DC-018 | approved | Remove the `ctrl+h` hidden-files binding (toggle stays in Settings) |
| DC-019 | approved | Hint rebalance — footer F-keys, palette for the rest |
| DC-020 | approved | JSON viewer no longer truncates to the 24-line fallback |
| DC-021 | approved | Packaging — version 1.0.0, Apache-2.0, metadata + README |
| DC-022 | approved | Documentation — CHANGELOG + CONTRIBUTING |

## How to run and verify

```bash
uv run daylight          # launch the TUI
uv run ruff check .      # lint gate
uv run pytest -q         # test gate
uv build                 # build wheel + sdist
```

Install options (GitHub source, release asset, local source checkout) are
documented in [`README.md`](../README.md#install).

## Known risks and deferred items

- **Snapshot baselines** embed the machine temp path; regenerate on another
  machine before relying on them.
- **Timing-sensitive 100k-listing performance tests** are load-sensitive
  (thresholds relaxed 2× per owner decision).
- **Optional follow-up:** a `black` formatting-hygiene pass (`black --check`
  reports 16 files that would reformat; `black` is not a gate).
- **Not performed (owner decision):** PyPI publication, CI, and any additional
  distribution channels beyond the GitHub release.
- **Evaluator+:** enabled (`ollama-cloud/gpt-oss:120b`); post-run verdict PASS —
  see [the section below](#evaluator-post-run-assessment).

## Evaluator+ (post-run assessment)

After delivery, **Evaluator+** (Triad+'s optional fresh, blind, post-run review —
not a fourth role and never a repair controller) assessed the result against the
PRD. Before it ran, the PRD was re-baselined to **Revision 3**
(`specs/PRD.md`, `specs/CHANGES-v3.md`) to reflect the owner follow-up cards
DC-012…DC-022.

- **Verdict: PASS** — result record in the control workspace
  (`artifacts/evaluator-plus/DC-EVAL-002.json`).
- Evaluated candidate: commit that includes Revision 3 of the PRD; the
  environment-derived gates were reproduced independently —
  `uv run ruff check .` clean and `uv run pytest -q` 559 passed (14 snapshots).
- One intermittent snapshot-timing flake was observed during evaluation
  (`test_viewer_search_snapshot`), with byte-identical expected/actual frames —
  not a product defect.
- An earlier evaluation returned FAIL against an uncommitted, over-claimed
  revision; it was corrected (FR-207 now states only that symlink *listing* is
  delivered; resolved link-target display and symlink creation are deferred),
  and the evaluation was re-run to PASS.
- Owner-accepted deferrals excluded from failure counting: FR-207 link-target
  display + symlink creation, FR-208 chmod/chown (out of scope in DC-003), and
  YAML pretty-printing (YAML renders as plain text).

An Evaluator+ verdict never reopens or modifies the delivered run.

## Owner-controlled items

- Demo service: disabled (`demo.enabled: false`); none started or stopped.
- Feedback does not reopen the delivered run: an in-scope defect becomes a
  follow-up feature card, a changed objective becomes a successor project.

## Practical test

| Step | Command / action | Expected |
| --- | --- | --- |
| Install | `uv tool install .` (or `uv run daylight`) | `daylight` command available / TUI opens |
| Browse | arrows, type-ahead, `Ctrl+S`/`Ctrl+R`, `/`, `Space`, `Ctrl+A` | listing navigates / sorts / filters / selects |
| File ops | `F5` copy, `F6` move, `F7` new dir, `F8` delete, `Shift+F6` rename, `Shift+F7` new file, `Shift+F8` wipe | operation with confirmation |
| View | `F3` on a text/JSON file, `F4` to edit | format-aware viewer; JSON fully scrollable |
| Quit | `Ctrl+Q` | app exits |

---

This handover reflects the delivered v1.0.0 project state, plus the
documentation and Triad+ 1.18.0 upgrade changes and the PRD Revision 3
re-baseline (with the post-run Evaluator+ PASS) made on 2026-10-16.
