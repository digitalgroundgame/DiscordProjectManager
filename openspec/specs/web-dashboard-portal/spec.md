# web-dashboard-portal Specification

## Purpose

Provides a browser-accessible, read-only web dashboard and portal overlay authenticated via Discord OAuth2, enabling guild members, leads, and server managers to explore Project Workspaces, filter Kanban and Table Matrix views, and inspect interactive Tech Tree DAGs.

## Requirements

### Requirement: Discord OAuth2 authentication and session establishment
The system SHALL authenticate browser users via Discord OAuth2 and establish a signed HTTP-only session cookie mapping the authenticated Discord identity to their live guild `ActorPrincipal` (roles and administrative status).

#### Scenario: Guild member completes Discord OAuth2 sign-in
- **GIVEN** a Discord user belongs to the configured server
- **WHEN** the user initiates sign-in at `/auth/login` and completes the Discord OAuth2 callback at `/auth/callback`
- **THEN** the system resolves their guild membership and roles, sets a signed HTTP-only session cookie, and redirects them to `/dashboard`

#### Scenario: Non-member attempts to access portal data
- **GIVEN** a Discord user is authenticated via OAuth2 but is not a member of the configured Discord server
- **WHEN** the user requests `/api/projects` or `/api/me`
- **THEN** the system denies access to server project workspaces with an authorization error indicating guild membership is required

#### Scenario: Development mode authentication fallback
- **GIVEN** the application is running with `ENVIRONMENT=development` and no `DISCORD_CLIENT_SECRET` is configured
- **WHEN** a developer navigates to `/auth/login`
- **THEN** the system establishes a development session principal without requiring an external Discord OAuth2 redirect

#### Scenario: User logs out of web portal
- **GIVEN** an authenticated user has an active portal session cookie
- **WHEN** the user requests `/auth/logout`
- **THEN** the system clears the session cookie and redirects to the login screen

### Requirement: Multi-project workspace listing and summary metrics
The system SHALL expose a read-only project roster endpoint (`/api/projects`) returning only the projects the caller is authorized to view, along with task status counts, overdue counts, and linked Squads.

#### Scenario: Authorized user loads accessible projects
- **GIVEN** active projects exist in the server with varying squad restrictions
- **WHEN** an authenticated user requests `/api/projects`
- **THEN** the response includes only projects visible to the user's `ActorPrincipal`, enriched with total task count, completed task count, overdue task count, and mapped squad names

### Requirement: Kanban and Table Matrix workspace views with filtering
The system SHALL provide a project workspace endpoint (`/api/projects/{project_id}/workspace`) and interactive browser views allowing users to group tasks by Status, Priority, or Assignee and filter tasks by Overdue State, Squad, Tags, Due Date, and search text.

#### Scenario: Grouping tasks on the Kanban board
- **GIVEN** an authenticated user is viewing an accessible project on the web portal
- **WHEN** the user switches the Kanban grouping control between Status, Priority, and Assignee
- **THEN** the portal reorganizes task cards into columns corresponding to the selected attribute

#### Scenario: Filtering tasks by Overdue State and Squad
- **GIVEN** a project contains both overdue and on-track tasks across multiple squads
- **WHEN** the user enables the Overdue filter or selects a specific Squad or Tag filter in the Kanban or Table Matrix view
- **THEN** only tasks matching all active filter criteria are displayed

### Requirement: Interactive Tech Tree DAG visualization and node inspection
The system SHALL provide a Tech Tree endpoint (`/api/projects/{project_id}/tech-tree`) and an interactive browser DAG canvas supporting pan, zoom, layout orientation switching (`LR` and `TB`), readiness state overlays (`complete`, `active`, `available`, `locked`, `blocked`), critical path highlighting, and a Node Inspector panel.

#### Scenario: Rendering project Tech Tree DAG with readiness states
- **GIVEN** a project has tasks connected by prerequisite dependencies
- **WHEN** an authenticated user opens the Tech Tree DAG view for that project
- **THEN** the portal renders each task node styled according to its live readiness state (`complete`, `active`, `available`, `locked`, or `blocked`) and draws directed edges from prerequisites to dependents

#### Scenario: Inspecting a task node in the Tech Tree DAG
- **GIVEN** the Tech Tree DAG canvas is displayed
- **WHEN** the user clicks a task node
- **THEN** the Node Inspector panel opens displaying the task's `short_id`, title, description, status, priority, readiness state, assignee, due date, prerequisite tasks, dependent tasks, and a direct "Open in Discord" link to its Thread Workspace and Task Action Card (or fallback Project Forum Channel)

### Requirement: Direct Discord deep-links across projects and tasks
The system SHALL compute canonical Discord jump URLs (`https://discord.com/channels/...`) for Projects (linking to the Project's Forum Channel) and Tasks (linking directly to the Task Action Card message in the Thread Workspace, or falling back to the Thread Workspace or Project Forum Channel) and expose "Open in Discord" actions across Project headers, Tech Tree Node Inspector panels, Kanban cards, and Table Matrix rows.

#### Scenario: User jumps from a web portal task to its Discord Task Action Card
- **GIVEN** a task has a provisioned `discord_thread_id` and `discord_message_id` in the server
- **WHEN** the user views the task in the Kanban board, Table Matrix, or Tech Tree Node Inspector
- **THEN** the portal renders an "Open in Discord" link targeting `https://discord.com/channels/{guild_id}/{discord_thread_id}/{discord_message_id}`

#### Scenario: User jumps from a project workspace view to its Discord Forum Channel
- **GIVEN** a project has a bound `discord_channel_id`
- **WHEN** the user views the project header in the web portal
- **THEN** the portal renders an "Open Project in Discord" link targeting `https://discord.com/channels/{guild_id}/{discord_channel_id}`
