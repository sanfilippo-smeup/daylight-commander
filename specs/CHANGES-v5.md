# Change manifest — PRD/ADR Revision 5

Authoritative record of what changed between Revision 4 and Revision 5: the
addition of the **AI dashboard builder** (Builder tab), an owner-requested POC
delivered on the isolated feature branch `feat/ai-dashboard-builder`.

## Baseline identity

| Item | v4 hash (frozen) | v5 hash (source) |
| --- | --- | --- |
| PRD | `91e6a1f768c77c232334b94a261958b664995e974f36ddd316ae820f9d8438e1` | `5f360b97eab68c854034697255daf4c2840150faf78fe9a0540746122787210b` |
| ADR | `87d9d8a47ca31c46812d5ba0a70c1665af04cc3cb28f153fa37889a4010e5f84` | `acf67195117cd573634f0c829adbc3b06d3a0612d865620bed843eb7c07691a3` |

## Functional requirement changes

| FR | Change | Summary | Card |
| --- | --- | --- | --- |
| FR-801 | Added | Builder tab (tab strip, palette, `Ctrl+6`) for natural-language dashboard requests | DC-025 |
| FR-802 | Added | Embedded `opencode serve` subprocess driven over its HTTP API; graceful degradation when absent | DC-024 |
| FR-803 | Added | Clarifying-question flow: present agent questions, return answers to the same session | DC-026 |
| FR-804 | Added | Sandboxed script execution: isolated subprocess, temp cwd, timeout, POSIX rlimits, no shell | DC-024 |
| FR-805 | Added | Live preview: stream stdout as produced; surface stderr and non-zero exit | DC-025 |
| FR-806 | Added | Save / reload / re-run generated dashboards, persisted per user | DC-024, DC-025 |
| FR-807 | Added | Generated-script contract: self-contained stdlib Python, prints a text/ANSI dashboard, exits 0 | DC-024 |
| FR-808 | Added | All process/HTTP/filesystem access in `managers/`; UI never spawns processes or opens sockets | DC-024, DC-025 |
| §6 | Modified | Added **Builder** to the Screens/views table | DC-025 |
| §7 | Modified | Added `Ctrl+6` (Builder) to default keybindings | DC-025 |
| §9.1 | Added | Explicit POC out-of-scope: no OS-level isolation, no arbitrary packages, no remote/multi-user | — |

## Architecture decision changes

| ADR | Change | Summary | Card |
| --- | --- | --- | --- |
| ADR-014 | Added | Embedded opencode server (stdlib HTTP) + sandboxed script runner + per-user dashboard store; no new dependency | DC-024, DC-025, DC-026 |

## Card decomposition

| Card | Outcome | Depends on |
| --- | --- | --- |
| DC-024 | AI builder harness: opencode client, sandbox runner, dashboard store, models | none (extends the delivered shell) |
| DC-025 | Builder tab: prompt, live preview, save/load, shell wiring | DC-024 |
| DC-026 | Clarifying-question flow, graceful degradation, end-to-end integration test, docs | DC-024, DC-025 |
