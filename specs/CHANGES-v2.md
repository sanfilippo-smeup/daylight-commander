# Change manifest — PRD/ADR Revision 2

This document is the authoritative record of *what changed* between the v1
baseline and Revision 2. It is authored (not machine-diffed) from the owner's
`PRD-2-notes.md`. Each change maps to a functional requirement (FR) or an
architecture decision (ADR) and, where applicable, to a feature card.

## Baseline identity

| Item | v1 hash (frozen) | v2 hash (source) |
| --- | --- | --- |
| PRD | `93a9d453ab0a21010183558d12ef77dec9eddc03d53cb1c3134e713f814256d4` | `533b304cf5c30353767c69233ea879ac5a0e6551f69ae094ff0f04dd6cfdb828` |
| ADR | `ef54c16b8a859c4af2330b35a236e465465b750c94e735e98dc5eed128cd63b2` | `872ffee8434f3bb185c23d32d8c65fa3aaa2c1e199fb07c88afde2f61bb98536` |

## Functional requirement changes

| FR | Change | Before | After | Card |
| --- | --- | --- | --- | --- |
| FR-103 | Modified | Dual-pane: left directory, right single inactive panel hosting the viewer. | Right panel is a two-tab tabsheet: Tab 1 directory (always visible), Tab 2 file viewer (shown only when a text file is opened). | DC-010 |
| FR-104 | Modified | Filter-as-you-type; filter persisted across navigation. | Filter clears automatically when entering a new directory. | DC-008 |
| FR-105 | Modified | Sort via keyboard only; no visible sort state. | Active sort column + direction always visible in the header; clicking a column header sorts (asc → desc) with an arrow indicator. | DC-009 |
| FR-109 | Added | (none) | Type-ahead prefix navigation in the left panel. | DC-007 |
| FR-306 | Modified | "Split-pane preview": viewer opens in the inactive panel. | "Tab preview": viewer opens in the right tabsheet's Viewer tab, shown only when a text file is opened. | DC-010 |
| FR-403 | Modified | Launch confirm dialog without specified default focus. | "Open" button focused by default; `Enter` triggers open; `Cancel` reachable via `Tab`. | DC-011 |

## Architecture decision changes

| ADR | Change | Summary | Card |
| --- | --- | --- | --- |
| ADR-011 | Added | Right panel becomes a two-tab tabsheet (directory + conditional viewer). | DC-010 |
| ADR-012 | Added | Sortable column headers with a visible order indicator (sorting logic stays in the manager/table-builder layer). | DC-009 |
| ADR open question #4 | Resolved | Right-panel tabsheet contents: Tab 1 directory, Tab 2 viewer. | DC-010 |

## Card decomposition

| Card | Outcome | Depends on |
| --- | --- | --- |
| DC-007 | Type-ahead prefix navigation (FR-109) | DC-002 |
| DC-008 | Filter clears on folder entry (FR-104) | DC-002 |
| DC-009 | Sortable column headers + visible sort indicator (FR-105, ADR-012) | DC-002 |
| DC-010 | Right panel two-tab tabsheet (FR-103, FR-306, ADR-011) | DC-002, DC-004 |
| DC-011 | Open-externally dialog focus/Enter/Tab (FR-403) | DC-006 |
