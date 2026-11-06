# Tasks: Unify Portal Visual Styling

## 1. Tech Tree DAG Dual Badges & State Retention

- [x] 1.1 In `src/adapters/api/static/app.js`, update `createNodeSvg` so overdue tasks that are not complete render dual badges (`OVERDUE` + state badge) while preserving true state color on the left accent bar and ID
- [x] 1.2 In `src/adapters/api/static/app.js`, ensure completed tasks (`state === "complete"`) are strictly excluded from overdue tagging

## 2. Kanban Board State Accents & Badges

- [x] 2.1 In `src/adapters/api/static/app.js`, update `renderKanban()` to add readiness and overdue state classes to `.task-card`
- [x] 2.2 In `src/adapters/api/static/styles.css`, add state-colored `4px` left borders and scoped short ID colors for `.task-card`

## 3. Table Matrix Row Accents & Badges

- [x] 3.1 In `src/adapters/api/static/app.js`, update `renderMatrix()` to add state classes to `<tr>` rows and format the Readiness badge
- [x] 3.2 In `src/adapters/api/static/styles.css`, add `4px` left borders and state colors to `td.task-short-id` for table matrix rows

## 4. Modern Translucent Badges & Quality Verification

- [x] 4.1 In `src/adapters/api/static/styles.css`, update `.legend-pill` styles to be translucent with colored text across all states
- [x] 4.2 Run `uv run pytest tests/test_web_dashboard_api.py` and verify all static dashboard tests pass
- [x] 4.3 Run `make check` (ruff lint, ruff format check, and full test suite) and verify zero regressions

## Workflow follow-up

- Archive the change after the project's review requirements are satisfied.
- Rebuild application container via `make docker-build`.
