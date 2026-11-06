# Tasks: Redesign Tech Tree DAG Nodes

## 1. Frontend Cytoscape Node Styling & Data Mapping

- [x] 1.1 In `src/adapters/api/static/app.js`, update node data construction in `renderTechTree()` to format labels with `[SHORT-ID]`, expanded title (up to 58 chars), and a compact corner status badge (`STATUS_BADGES`), mapping readiness state to `borderColor`
- [x] 1.2 In `src/adapters/api/static/app.js`, update the Cytoscape stylesheet to apply a uniform `#2b2d31` card background, `250×76px` dimensions, `text-max-width: 225px`, high-contrast text `#f2f3f5`, and dynamic border colors
- [x] 1.3 In `src/adapters/api/static/app.js`, adjust Dagre layout parameters (`nodeSep: 50`, `rankSep: 85`) to ensure comfortable spacing between expanded cards

## 2. Fallback View & Style Alignment

- [x] 2.1 In `src/adapters/api/static/styles.css`, verify and align fallback node list rendering and legend styles to match the dark slate card presentation

## 3. Verification & Quality Checks

- [x] 3.1 Run `uv run pytest tests/test_web_dashboard_api.py` and verify all static dashboard tests pass
- [x] 3.2 Run `make check` (ruff lint, ruff format check, and full test suite) and verify zero regressions

## Workflow follow-up

- Archive the change after the project's review requirements are satisfied.
- Rebuild application container via `make docker-build` if testing live in container.
