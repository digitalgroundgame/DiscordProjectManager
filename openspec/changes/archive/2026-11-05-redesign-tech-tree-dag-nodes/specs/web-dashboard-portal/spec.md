# Spec Delta: web-dashboard-portal

## MODIFIED Requirements

### Requirement: Interactive Tech Tree DAG visualization and node inspection
The system SHALL provide a Tech Tree endpoint (`/api/projects/{project_id}/tech-tree`) and an interactive browser DAG canvas supporting pan, zoom, layout orientation switching (`LR` and `TB`), readiness state overlays (`complete`, `active`, `available`, `locked`, `blocked`), critical path highlighting, and a Node Inspector panel. Task nodes in the DAG canvas SHALL render as dark slate cards with high-contrast text, a colored border accent matching readiness state, an expanded multi-line title, and a compact corner status indicator.

#### Scenario: Rendering project Tech Tree DAG with readiness states
- **GIVEN** a project has tasks connected by prerequisite dependencies
- **WHEN** an authenticated user opens the Tech Tree DAG view for that project
- **THEN** the portal renders each task node as a dark slate card styled with a colored border accent according to its live readiness state (`complete`, `active`, `available`, `locked`, or `blocked`), crisp high-contrast light text, a compact corner status badge, and draws directed edges from prerequisites to dependents

#### Scenario: Legibility of active in-progress nodes
- **GIVEN** a task is in an active (in-progress) readiness state
- **WHEN** the DAG view renders the active task node
- **THEN** the node text remains high contrast and legible against the dark slate background, accentuated by an amber active border and compact active status badge

#### Scenario: Expanded title visibility on task nodes
- **GIVEN** a task has a title longer than 24 characters (up to 60 characters)
- **WHEN** the DAG view renders the node card
- **THEN** the node wraps and displays the full title across multiple lines rather than aggressively truncating at 24 characters

#### Scenario: Inspecting a task node in the Tech Tree DAG
- **GIVEN** the Tech Tree DAG canvas is displayed
- **WHEN** the user clicks a task node
- **THEN** the Node Inspector panel opens displaying the task's `short_id`, title, description, status, priority, readiness state, assignee, due date, prerequisite tasks, dependent tasks, and a direct "Open in Discord" link to its Thread Workspace and Task Action Card (or fallback Project Forum Channel)
