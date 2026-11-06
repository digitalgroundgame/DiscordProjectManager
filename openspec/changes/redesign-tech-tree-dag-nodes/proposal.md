# Proposal: Redesign Tech Tree DAG Node Visual Styling

## Why

In the Web Dashboard & Portal, the interactive Tech Tree DAG currently displays active (in-progress) nodes using white text on an amber/yellow background (`#f0b232`), producing an illegible ~1.6:1 contrast ratio that fails WCAG accessibility standards. Furthermore, task titles are prematurely truncated at only 24 characters, while large centered all-caps status text (`COMPLETE`, `ACTIVE`, `AVAILABLE`, `LOCKED`) unnecessarily occupies valuable card real estate that is already communicated by node colors and the toolbar legend.

Redesigning the Tech Tree DAG nodes will resolve the critical contrast defect, allow tasks to display substantive multi-line titles (up to 55–60 characters), and elevate visual clarity using a modern dark slate card design with colored state accents and a compact corner status badge.

## What Changes

- **Dark Slate Card Design**: Transition Tech Tree DAG nodes to a uniform dark slate background (`#2b2d31`) matching the portal dark theme, with a state-colored border outline/accent (`complete`: `#23a55a`, `active`: `#f0b232`, `available`: `#5865f2`, `locked`: `#6d6f78`, `blocked`/`overdue`: `#f23f43`).
- **Accessible Uniform Contrast**: Render text using high-contrast light typography (`#f2f3f5` / `#949ba4`) across all node states, permanently eliminating white-on-yellow contrast issues without requiring font color inversions.
- **Expanded Title Visibility**: Increase node dimensions to `245×76px` with a `text-max-width` of `225px`, expanding the title truncation ceiling from 24 characters to ~55–60 characters with clean 2-line wrapping.
- **Compact Corner Status Indicator**: Replace the large, centered all-caps status string (`\n${n.state.toUpperCase()}`) with a subtle, non-intrusive corner status indicator (e.g., `● ACTIVE`, `✓ COMPLETE`, `○ AVAILABLE`, `🔒 LOCKED`).
- **Maintained Critical Path & Overdue Styling**: Preserve overdue highlighting (`#f23f43` border) and critical-path highlighting (white border and edge emphasis).

## Capabilities

### New Capabilities
<!-- None -->

### Modified Capabilities
- `web-dashboard-portal`: Updates the Tech Tree DAG visual node presentation requirement to specify high-contrast accessible typography, expanded title visibility, dark slate card styling with state border accents, and compact corner status indicator placement.

## Impact

- **Frontend Code**: `src/adapters/api/static/app.js` (Cytoscape node data mapping, styling, and dimensions in `renderTechTree()`) and `src/adapters/api/static/styles.css` (fallback view styling).
- **APIs & Backend**: No breaking backend API changes or database schema alterations; `/api/projects/{id}/tech-tree` payload remains identical.
- **Testing**: Frontend asset tests in `tests/test_web_dashboard_api.py` continue to verify script serving, DOM hooks, and XSS safety.
