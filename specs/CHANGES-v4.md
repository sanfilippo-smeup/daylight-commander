# Change manifest — PRD/ADR Revision 4

Authoritative record of what changed between Revision 3 and Revision 4: the
addition of the directory statistics dashboard (Stats tab), requested by the
owner after the v1.0.1 docs patch.

## Baseline identity

| Item | v3 hash (frozen) | v4 hash (source) |
| --- | --- | --- |
| PRD | `246f310b69e27f9f32791e17f2cce66a0387c7ec0498ad673f6f290f8b52bb99` | `91e6a1f768c77c232334b94a261958b664995e974f36ddd316ae820f9d8438e1` |
| ADR | `872ffee8434f3bb185c23d32d8c65fa3aaa2c1e199fb07c88afde2f61bb98536` | `87d9d8a47ca31c46812d5ba0a70c1665af04cc3cb28f153fa37889a4010e5f84` |

## Functional requirement changes

| FR | Change | Summary | Card |
| --- | --- | --- | --- |
| FR-701 | Added | Read-only Stats tab for the active directory (tab strip, palette, `Ctrl+5`) | DC-023 |
| FR-702 | Added | Summary cards: total size, file/dir counts, dirs:files ratio, hidden, symlinks | DC-023 |
| FR-703 | Added | Disk usage by content category + share chart; top extensions | DC-023 |
| FR-704 | Added | Modification timeline: calendar heatmap (~12 weeks) + monthly bars | DC-023 |
| FR-705 | Added | Size distribution histogram (size buckets) | DC-023 |
| FR-706 | Added | Permissions breakdown by mask | DC-023 |
| FR-707 | Added | Insights: top-N largest files, heaviest dirs, oldest/newest | DC-023 |
| FR-708 | Added | Non-blocking computation; recursive walk bounded by a safety cap, "(partial)" label | DC-023 |
| FR-709 | Added | Refresh on show / directory change / explicit action; per-directory cache | DC-023 |
| §6 | Modified | Added **Stats** to the Screens/views table | DC-023 |
| §7 | Modified | Added `Ctrl+5` (Stats) to default keybindings | DC-023 |

## Architecture decision changes

| ADR | Change | Summary | Card |
| --- | --- | --- | --- |
| ADR-013 | Added | Dependency-free chart rendering + a `managers/stats.py` worker; no new dependency | DC-023 |

## Card decomposition

| Card | Outcome | Depends on |
| --- | --- | --- |
| DC-023 | Directory statistics dashboard (Stats tab) | none (extends the delivered shell) |
