# Change manifest — PRD Revision 6 / ADR-016

Owner decision: the AI dashboard builder must create dashboards **inside the
Daylight TUI using the TUI standards and structure**. The Revision-5 script
model is replaced. Choices recorded: declarative spec + native render (1A),
dedicated Dashboard tab (2C), replace the script/HTML path (3A).

## PRD §5.8 (FR-800) before → after

| Req | Revision 5 | Revision 6 |
| --- | --- | --- |
| FR-801 | Builder tab; prompt for the current directory | Builder tab hosts the prompt, conversation, questions, and saved list; the dashboard renders elsewhere |
| FR-802 | Agent produces a self-contained **Python script** | Agent produces a **JSON dashboard specification**; it must not generate or execute code |
| FR-803 | Questions before the script | Questions before the specification |
| FR-804 | Script executed in a **sandbox** (subprocess, timeout, rlimits) | **Spec contract**: versioned JSON, allowlisted block kinds/sources, bounded `top_n`, no executable content; invalid specs rejected with a precise message |
| FR-805 | stdout streamed into a text **Preview** pane | **Native rendering** in a dedicated **Dashboard** tab using `SummaryCard`/`BarChart`/`ShareBar`/`Histogram`/`CalendarHeatmap`, theme tokens, and `dashboard.tcss`; `Ctrl+R` recomputes |
| FR-806 | Save name/prompt/**script**; reload and re-run | Save name/prompt/**spec**/directory; reload and render; Revision-5 script artifacts are legacy (readable, not executed/rendered) |
| FR-807 | Generated-script contract (stdlib Python, stdout) | **Data-source allowlist** (`summary`, `extension_counts`, `extension_sizes`, `category_sizes`, `size_buckets`, `monthly_counts`, `permission_counts`, `daily_counts`, `largest_files`, `insights`) |
| FR-808 | Managers own process/HTTP/filesystem access | Managers own the spec parser/validator, data binder, stats walk, HTTP, and persistence; the UI never executes agent output |

## PRD §9.1 (out of scope)

- Removed: "OS-level sandbox isolation" (the sandbox is gone).
- Added: generated code execution of any kind, arbitrary expressions/shell,
  agent-authored modules, arbitrary data sources/SQL/network, external chart
  libraries, image/SVG/HTML/browser rendering.
- Replaced: "guaranteeing generated-code quality" with "guaranteeing the
  aesthetic quality of a specification".

## Screens / keybindings

- Screen 9 **Builder**: now prompt + conversation + saved list; opens the
  native Dashboard tab.
- Screen 10 **Dashboard** (new): native, specification-driven dashboard for a
  directory, opened from the Builder, saved list, or palette.
- `Ctrl+6` remains Builder. The Dashboard tab is dynamic and needs no fixed
  keybinding.

## ADR

- **ADR-016** added: declarative specification + strict validator + native
  renderer + dynamic Dashboard tab; script/sandbox path removed. Rejected
  alternatives: generated Textual module (in-process code), styled script pane,
  HTML/SVG.
- ADR-014 remains historical context; its script/sandbox decision is superseded
  by ADR-016 for the Builder.
- ADR-015 (opencode v2 transport) remains in force.

## Cards

- DC-029 — spec model, validator, and data binder (`models/`, `managers/`).
- DC-030 — native Dashboard page + dynamic tab (`components/`, `app.py`, `.tcss`).
- DC-031 — Builder returns/validates a spec, opens the Dashboard tab, persists
  specs, handles legacy artifacts.
- DC-032 — v2 stub end-to-end spec flow, invalid-spec degradation, snapshots,
  docs.
- DC-033 — remove the unused sandbox manager/model/tests and script remnants.
