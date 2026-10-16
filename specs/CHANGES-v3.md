# Change manifest — PRD Revision 3

This document is the authoritative record of *what changed* between the
Revision 2 baseline and Revision 3. It is authored (not machine-diffed) from the
owner follow-up cards DC-012 … DC-022, which were delivered after the Revision 2
freeze but were not yet reflected in the PRD.

Revision 3 does not introduce new product intent: it brings the written
requirements back in line with behavior that was already implemented, verified,
and owner-accepted across DC-012 … DC-022.

## Baseline identity

| Item | v2 hash (frozen) | v3 hash (source) |
| --- | --- | --- |
| PRD | `533b304cf5c30353767c69233ea879ac5a0e6551f69ae094ff0f04dd6cfdb828` | `246f310b69e27f9f32791e17f2cce66a0387c7ec0498ad673f6f290f8b52bb99` |

> The v3 source hash is recorded in run-state `prd_rebaselines`. The ADR is
> unchanged in Revision 3.

## Functional requirement changes

| FR / section | Change | Before (Rev 2) | After (Rev 3) | Card |
| --- | --- | --- | --- | --- |
| Core principle #1 | Modified | "every binding is shown in the footer" | Footer shows high-frequency global operations; page headers show contextual hints; the command palette exposes every action | DC-019 |
| FR-102 | Modified | Go up with `..`/`Backspace` (single step) | `Backspace` moves the cursor to the `..` row (clearing an active filter first); `Enter` ascends; no-op at root | DC-017 |
| FR-107 | Clarified | Toggle hidden files; remember preference | Toggle lives in Settings → Defaults → Hidden files and persists; no dedicated runtime keybinding | DC-018 |
| FR-203 | Modified | Multi-select (`Space` toggles) | Selected rows are visibly marked (bold, leading `*`) and the mark survives re-sort/re-filter | DC-016 |
| FR-306 | Clarified | Viewer opens in the Viewer tab, never fullscreen | Viewer fills the full vertical height of the tab and stays visible while the left listing is navigated | DC-014, DC-015 |
| FR-502 | Clarified | Footer with the active keybinding hints | Footer shows high-frequency operations; remaining actions reachable via the command palette | DC-019 |
| §7 keybindings | Rewritten | Mixed letters + F-keys; `F7` new file/dir; `Ctrl+T` new tab/pane toggle | Norton/mc F-key scheme: no printable letter triggers an operation; adds `Shift+F6` rename, `Shift+F7` new file, `Shift+F8` wipe, `Alt+Enter` properties, `Ctrl+S`/`Ctrl+R` sort, `Ctrl+C` copy field, `Ctrl+G` next match, `Ctrl+Shift+A` clear selection; `F7` new directory; `Ctrl+T` switch pane; letters drive type-ahead | DC-012, DC-019 |
| FR-207 | Clarified / deferred | Symlink creation and display | Symlinks are listed as navigable/operable entries; resolved link-*target* display and symlink *creation* deferred past v1 | (never scheduled) |
| FR-208 | Deferred | Change permissions/ownership from a properties view | Deferred past v1 (out of scope in DC-003); properties view is read-only | DC-003 (descope) |

## Delivered cards not represented as functional requirements

These were delivered and are correct, but they are delivery/quality artifacts
rather than product behavior, so Revision 3 does not add FRs for them:

| Card | Outcome | Where it belongs |
| --- | --- | --- |
| DC-013 | Fix stale key hints left by the DC-012 remap | Fix (no FR) |
| DC-020 | JSON viewer no longer truncates long files to the 24-line fallback | Fix under FR-301 |
| DC-021 | Packaging — version 1.0.0, Apache-2.0, metadata + README | Delivery artifact |
| DC-022 | Documentation — CHANGELOG + CONTRIBUTING | Delivery artifact |

## Card decomposition

All listed cards are already `approved`; Revision 3 is a documentation
re-baseline, not new work.

| Card | Outcome | Card |
| --- | --- | --- |
| DC-012 | F-key key remap (Norton/mc scheme) | §7 |
| DC-013 | Stale hint strings fixed | fix |
| DC-014 | Viewer fills the tab vertically | FR-306 |
| DC-015 | Viewer stays visible while navigating | FR-306 |
| DC-016 | Visible selection marker | FR-203 |
| DC-017 | Backspace → `..`, Enter ascends | FR-102 |
| DC-018 | Remove `Ctrl+H` hidden-files binding | FR-107 |
| DC-019 | Hint placement: footer vs palette | principle #1, FR-502, §7 |
| DC-020 | JSON viewer truncation fix | fix (FR-301) |
| DC-021 | Packaging 1.0.0 / Apache-2.0 | delivery |
| DC-022 | CHANGELOG + CONTRIBUTING | delivery |
