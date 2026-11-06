# Spec Delta: web-dashboard-portal

## MODIFIED Requirements

### Requirement: Kanban and Table Matrix workspace views with filtering
The system SHALL provide a project workspace endpoint (`/api/projects/{project_id}/workspace`) and interactive browser views allowing users to group tasks by Status, Priority, or Assignee and filter tasks by Overdue State, Squad, Tags, Due Date, and search text. Task cards in the Kanban board and rows in the Table Matrix SHALL render with state-colored left accent borders, state-colored `[PRJ-N]` short IDs, and translucent badge pills matching the system readiness color scheme.

#### Scenario: Grouping tasks on the Kanban board
- **GIVEN** an authenticated user is viewing an accessible project on the web portal
- **WHEN** the user switches the Kanban grouping control between Status, Priority, and Assignee
- **THEN** the portal reorganizes task cards into columns corresponding to the selected attribute
- **AND** cards render with a left accent border and short ID colored according to their live readiness state or overdue status, with translucent badge pills without decorative emojis

#### Scenario: Table Matrix row state accents and badge styling
- **GIVEN** an authenticated user opens the Table Matrix view
- **WHEN** the matrix rows are rendered
- **THEN** each row displays a left accent border on its leading cell and short ID styled in the readiness state color, with a matching translucent readiness badge in the Readiness column

#### Scenario: Filtering tasks by Overdue State and Squad
- **GIVEN** a project contains both overdue and on-track tasks across multiple squads
- **WHEN** the user enables the Overdue filter or selects a specific Squad or Tag filter in the Kanban or Table Matrix view
- **THEN** only tasks matching all active filter criteria are displayed

### Requirement: Interactive Tech Tree DAG visualization and node inspection
The system SHALL provide a Tech Tree endpoint (`/api/projects/{project_id}/tech-tree`) and an interactive browser DAG canvas supporting pan, zoom, layout orientation switching (`LR` and `TB`), readiness state overlays (`complete`, `active`, `available`, `locked`, `blocked`), critical path highlighting, and a Node Inspector panel. Task nodes in the DAG canvas SHALL render as dark slate cards with high-contrast text, a colored border accent matching readiness state, an expanded multi-line title, and compact status indicators. When an uncompleted task is overdue, the node SHALL display both its true readiness state and an overdue badge simultaneously. Completed tasks SHALL NOT be tagged as overdue.

#### Scenario: Rendering project Tech Tree DAG with readiness states
- **GIVEN** a project has tasks connected by prerequisite dependencies
- **WHEN** an authenticated user opens the Tech Tree DAG view for that project
- **THEN** the portal renders each task node as a dark slate card styled with a colored border accent according to its live readiness state (`complete`, `active`, `available`, `locked`, or `blocked`), crisp high-contrast light text, a compact corner status badge, and draws directed edges from prerequisites to dependents

#### Scenario: Simultaneous readiness and overdue presentation
- **GIVEN** an uncompleted task (`active`, `available`, or `locked`) is overdue
- **WHEN** the DAG view renders the node card
- **THEN** the node preserves its true readiness color on the left accent bar and short ID, and displays dual badges in the top right header with both `OVERDUE` and its readiness state

#### Scenario: Exclusion of completed tasks from overdue tagging
- **GIVEN** a task is marked `complete` and its historical due date has passed
- **WHEN** the DAG view renders the node card
- **THEN** the node displays only the green `COMPLETE` badge and green left accent bar, and does not display an overdue indicator

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
