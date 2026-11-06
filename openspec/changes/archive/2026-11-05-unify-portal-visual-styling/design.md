# Design: Unified Portal Visual Styling

## Context

See `proposal.md` for motivation. Currently:
1. In `src/adapters/api/static/app.js`, `createNodeSvg` sets `stateColor = "#f23f43"` when `isOverdue` is true, overwriting the task's true readiness state (active, available, locked) and rendering only an `OVERDUE` badge.
2. In `renderKanban()` and `renderMatrix()`, task cards and table rows use default grey borders, static grey `[PRJ-N]` short IDs, and legacy solid badge pills.

## Goals / Non-Goals

**Goals:**
- In Tech Tree DAG nodes: Support simultaneous presentation of true readiness state (`ACTIVE`, `AVAILABLE`, `LOCKED`) and `OVERDUE` status via dual pill badges, while preserving the true readiness color on the left accent bar and short ID. Completed tasks are explicitly excluded from overdue tagging.
- In Kanban cards: Apply a 4px state-colored left accent border, state-colored `[PRJ-N]` short ID, and translucent badge pills.
- In Table Matrix rows: Apply a 4px left accent border to the leading cell, state-colored `[PRJ-N]` short ID, and translucent readiness badges.
- Consistently eliminate decorative emojis across all badges (`OVERDUE`, `ACTIVE`, `AVAILABLE`, `COMPLETE`, `LOCKED`).

**Non-Goals:**
- Modifying backend APIs, database models, or payload structures.
- Changing task inspector drawer behaviors beyond adopting matching translucent badge styles.

## Decisions

### Decision 1: Dual Pill Badging in `createNodeSvg`
- **Logic**:
  ```javascript
  const isOverdue = Boolean(taskIsOverdue && state !== "complete");
  const stateColor = STATE_COLORS[state] || STATE_COLORS.locked;
  const isLocked = state === "locked";
  const shortIdColor = isLocked ? "#949ba4" : stateColor;
  ```
- **Badge Positioning**:
  - When `isOverdue` is true, calculate two badge rects:
    1. `OVERDUE` pill: red text `#f23f43`, background `rgba(242, 63, 67, 0.15)`.
    2. State pill (`ACTIVE`, `AVAILABLE`, `LOCKED`): state color, semi-transparent background.
  - When `isOverdue` is false, render only the single state badge.
  - When `state === "complete"`, only render the `COMPLETE` badge.

### Decision 2: Kanban Card Styling via State Classes
- **CSS Architecture**:
  - `.task-card` receives `.state-${readiness}` (e.g. `.state-active`, `.state-complete`, `.state-available`, `.state-locked`).
  - If `t.is_overdue && t.readiness_state !== "complete"`, also add `.state-overdue`.
  - `.task-card.state-active`: `border-left: 4px solid var(--state-active);`
  - `.task-card.state-complete`: `border-left: 4px solid var(--state-complete);`
  - `.task-card.state-available`: `border-left: 4px solid var(--state-available);`
  - `.task-card.state-locked`: `border-left: 4px solid var(--state-locked);`
  - `.task-card.state-overdue`: `border-left: 4px solid var(--state-blocked);`
  - `.task-card .task-short-id` inherits the state color via scoped CSS rules.

### Decision 3: Table Matrix Row Accent via State Classes
- **CSS Architecture**:
  - `<tr class="state-${safeState} ${t.is_overdue && t.readiness_state !== 'complete' ? 'is-overdue' : ''}">`
  - `tr.state-active td:first-child`: `border-left: 4px solid var(--state-active); color: var(--state-active);`
  - `tr.state-complete td:first-child`: `border-left: 4px solid var(--state-complete); color: var(--state-complete);`
  - `tr.state-available td:first-child`: `border-left: 4px solid var(--state-available); color: var(--state-available);`
  - `tr.state-locked td:first-child`: `border-left: 4px solid var(--state-locked); color: var(--text-muted);`
  - `tr.is-overdue td:first-child`: `border-left: 4px solid var(--state-blocked); color: var(--state-blocked);`

### Decision 4: Unified Translucent Badges (`.legend-pill`)
- Modernize `.legend-pill` across all views:
  - Base: `font-size: 0.72rem; font-weight: 700; padding: 0.2rem 0.55rem; border-radius: 999px; letter-spacing: 0.04em;`
  - `state-complete`: `background-color: rgba(35, 165, 90, 0.15); color: #23a55a;`
  - `state-active`: `background-color: rgba(240, 178, 50, 0.15); color: #f0b232;`
  - `state-available`: `background-color: rgba(88, 101, 242, 0.15); color: #5865f2;`
  - `state-locked`: `background-color: rgba(255, 255, 255, 0.06); color: #949ba4;`
  - `state-overdue` / `state-blocked`: `background-color: rgba(242, 63, 67, 0.15); color: #f23f43;`

## Risks / Trade-offs

- **[Risk]** Two badges on the Tech Tree DAG top header might compete with very long short IDs.
  - **Mitigation**: Node width is 260px; `[EVT-2]` is ~50px, and two badges take ~125px combined, leaving >70px breathing room.
- **[Risk]** Table cell borders could break collapse geometry.
  - **Mitigation**: Apply `border-left: 4px solid ...` directly to the `td:first-child`, which cleanly respects table borders without layout shift.
