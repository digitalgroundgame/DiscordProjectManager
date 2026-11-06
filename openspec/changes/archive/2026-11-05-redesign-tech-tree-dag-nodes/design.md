# Design: Tech Tree DAG Node Visual Redesign

## Context

See `proposal.md` for motivation and background. Currently, `src/adapters/api/static/app.js` renders Tech Tree DAG nodes using Cytoscape.js with Dagre layout. In the current implementation:
1. `STATE_COLORS` assigns `#f0b232` to the `active` state.
2. Cytoscape node style sets a static `color: "#ffffff"`, causing severe contrast failure (1.6:1) on active nodes.
3. Node labels are constructed as `[${n.short_id}] ${n.title.length > 26 ? n.title.slice(0, 24) + "…" : n.title}\n${n.state.toUpperCase()}` inside a `195×62px` box, truncating titles after only 24 characters.

## Goals / Non-Goals

**Goals:**
- Implement Approach 2 (Dark Slate Cards with colored accent borders): uniform `#2b2d31` card background with 3px state-colored borders.
- Guarantee WCAG AA/AAA compliance across all node states using crisp light typography (`#f2f3f5` and `#949ba4`).
- Expand title visibility to display up to 55–60 characters across 2 wrapped lines in a widened `250×76px` node container.
- Replace the large centered state text with a subtle corner status badge (e.g. `● ACTIVE`, `✓ COMPLETE`, `○ AVAILABLE`, `🔒 LOCKED`).
- Retain existing interactive behaviors: pan, zoom, click-to-inspect drawer (`openInspector`), orientation switching (`LR`/`TB`), and critical-path / overdue highlighting.

**Non-Goals:**
- Introducing external DOM/HTML overlay plugins (e.g., `cytoscape-node-html-label`) or altering Content Security Policy / script dependencies.
- Modifying backend APIs or database models; `/api/projects/{id}/tech-tree` JSON payload remains unchanged.
- Altering Kanban or Table Matrix views, which already use dark theme card styling.

## Decisions

### Decision 1: Dark Slate Card Canvas Integration
- **Choice**: Style Cytoscape nodes with `background-color: #2b2d31`, `border-color: data(borderColor)`, and `border-width: data(borderWidth)`.
- **State Colors**:
  - `complete`: `#23a55a`
  - `active`: `#f0b232`
  - `available`: `#5865f2`
  - `locked`: `#6d6f78`
  - `blocked` / `overdue`: `#f23f43`
  - `critical_path`: `#ffffff` (or 4px border width)
- **Rationale**: Keeps text colors uniformly light (`#f2f3f5`), which has >10:1 contrast against `#2b2d31`. It prevents having to flip text colors between dark and light, and produces a cohesive dark mode aesthetic that aligns with Discord and modern developer tooling.
- **Alternatives Considered**: Solid fill nodes with dynamically computed text color (Approach 1). Rejected because bright solid yellow/amber cards dominate the visual field and create jarring contrast jumps against neighboring dark nodes.

### Decision 2: Native Cytoscape Label Formatting with Option B Layout
- **Choice**: Construct the node label in JavaScript using native multi-line text:
  ```javascript
  const displayTitle = n.title.length > 58 ? n.title.slice(0, 56) + "…" : n.title;
  const statusBadge = STATUS_BADGES[n.state] || "● " + n.state.toUpperCase();
  const label = `[${n.short_id}] ${displayTitle}\n${statusBadge}`;
  ```
- **Rationale**: Works seamlessly with Cytoscape's HTML5 canvas renderer without introducing external node HTML dependencies or risking CSP script-src violations.
- **Alternatives Considered**: SVG background images or DOM overlay plugins. Rejected as unnecessary overhead for displaying title, ID, and status.

### Decision 3: Geometry & Layout Spacing
- **Dimensions**:
  - Node width: `250px` (was `195px`)
  - Node height: `76px` (was `62px`)
  - `text-max-width`: `225px` (was `175px`)
  - `font-size`: `11px`, `line-height`: normal
- **Dagre Layout Parameters**:
  - `nodeSep`: `50` (up from `45`)
  - `rankSep`: `85` (up from `80`)
  - `padding`: `30`
- **Rationale**: Comfortably accommodates 2 lines of task title text plus the short ID and corner status badge while avoiding crowded node clusters when panning.

## Risks / Trade-offs

- **[Risk]** Larger node dimensions might reduce the number of nodes visible in the default viewport before zooming.
  - **Mitigation**: The DAG view already features a "Fit View" (`dag-fit-btn`) button and mouse-wheel zooming, and Dagre's rank separation smoothly spreads the graph across the canvas.
- **[Risk]** Very long task titles (>60 chars) could overflow if not truncated.
  - **Mitigation**: Truncate titles at 58 characters with an ellipsis (`…`) so text never overflows the node box, while full descriptions remain viewable by tapping into the Node Inspector drawer.
