# Proposal: Unify Portal Visual Styling Across Tech Tree, Kanban, and Matrix

## Why

While the Tech Tree DAG now features modern dark slate cards with state-colored accent borders, the Kanban Board and Table Matrix still use generic grey borders, static grey short IDs, and legacy solid badge pills. Additionally, within the Tech Tree DAG, overdue tasks previously overwrote their readiness state (e.g., an in-progress overdue task only displayed `OVERDUE`), losing vital context about whether a task is active, ready, or locked. 

Unifying the visual styling across all three workspace views establishes a cohesive design language: the Tech Tree DAG gains dual-badge support (`OVERDUE` alongside `ACTIVE`, `AVAILABLE`, or `LOCKED`, excluding completed tasks), while Kanban cards and Table Matrix rows adopt the matching left accent borders, state-colored short IDs, and translucent badge pills without decorative emojis.

## What Changes

- **Tech Tree DAG Dual Badging**:
  - Overdue tasks that are not complete display dual pill badges in the top right: `OVERDUE` (red) alongside their readiness state (`ACTIVE`, `AVAILABLE`, `LOCKED`).
  * The left accent bar and short ID always anchor the true readiness state color (Amber for active, Blurple for available, Grey for locked), preserving macro-level graph comprehension.
  * Completed tasks (`state === "complete"`) are strictly excluded from overdue tagging.
- **Kanban Board Cards**:
  * Adopt a `4px` left accent border matching the task's readiness state (`#f0b232` active, `#23a55a` complete, `#5865f2` available, `#6d6f78` locked, `#f23f43` overdue priority).
  * Render the `[PRJ-N]` short ID in the matching state color rather than static grey.
  * Update badge pills to translucent, low-saturation pills (`bg-color/15` with colored text) with clean text and zero emojis.
- **Table Matrix Rows**:
  * Add a `4px` left accent border to row leading cells reflecting readiness state.
  * Render the `[PRJ-N]` short ID in the matching state color.
  * Update the Readiness column to use matching translucent badge pills.
  * Format overdue rows with a red left accent border, red short ID, and `OVERDUE` badge pill while preserving metadata legibility.

## Capabilities

### New Capabilities
<!-- None -->

### Modified Capabilities
- `web-dashboard-portal`: Updates the Kanban, Table Matrix, and Tech Tree DAG view specifications to reflect unified state-colored left accent styling, state-colored short IDs, translucent badge pills, and simultaneous readiness + overdue representation.

## Impact

- **Frontend Assets**: `src/adapters/api/static/app.js` (`renderTechTree`, `renderKanban`, `renderMatrix`) and `src/adapters/api/static/styles.css` (`.task-card`, `.matrix-table`, `.legend-pill`).
- **APIs & Schema**: Zero backend API changes or database migrations required; works entirely with existing serialized payloads.
- **Testing**: `tests/test_web_dashboard_api.py` and overall test suite.
