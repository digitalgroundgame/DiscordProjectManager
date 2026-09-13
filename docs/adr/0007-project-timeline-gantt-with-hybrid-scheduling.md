# 0007: Project Timeline (Gantt) with Hybrid CPM Scheduling and Dual Visualization

The project management platform introduces **Project Timeline**—a calendar-indexed Gantt visualization—as a first-class companion view alongside the existing Directed Acyclic Graph (DAG) **Tech Tree**. While the Tech Tree visualizes pure topological unlocking dependencies without requiring calendar dates, the Project Timeline visualizes temporal concurrency, execution spans, milestones, and deadlines across calendar time.

To support low-friction workflows where contributors do not always specify explicit start dates or durations, the platform employs a **Hybrid Scheduling Engine**: tasks can optionally define an explicit `start_at` timestamp, but missing start and duration parameters are inferred algorithmically using Critical Path Method (CPM) heuristics based on prerequisite completion, creation time, priority tiers, and due dates.

## Considered Options

- **Option 1: Complete Replacement of Tech Tree with Gantt Chart**:
  - *Rejected*: The Tech Tree DAG is foundational for gamified "unlocking" workflows, async open-source contributors, and dependency bottleneck analysis. Forcing all projects into calendar timelines breaks workflows where work is prerequisite-driven rather than calendar-scheduled.
- **Option 2: Mandatory Explicit Start Dates & Durations**:
  - *Rejected*: Forcing Discord users to provide start dates and duration estimates for every task creates excessive friction during task creation, leading to stale or abandoned project tracking.
- **Option 3: Purely Inferred Schedules Stored in Ephemeral Memory**:
  - *Rejected*: Does not allow project leads to intentionally schedule future task execution windows or fix start dates when planning sprints.
- **Option 4: Dual Visualization with Hybrid CPM Scheduling (Selected)**:
  - *Accepted*: Both views are retained and switchable via toggle controls. `start_at` is added as an optional indexed database column on `tasks`. When missing, task start dates and durations are computed on-demand from dependency chains and priority weights (Urgent: 1d, High: 2d, Normal: 4d, Low: 7d). Visual output defaults to an auto-adaptive high-resolution Pillow PNG in Discord embeds, with an export option for Mermaid Gantt markdown.

## Consequences

- **Domain Model**: `CONTEXT.md` explicitly recognizes `Project Timeline` and `Inferred Schedule` as canonical domain concepts distinct from `Tech Tree`.
- **Database & Schema**: Adds a nullable `start_at` column to `TaskTable`, `Task` domain model, and Alembic migrations.
- **Rendering Pipeline**: An extensible `PillowGanttRenderer` produces technical blueprint-themed timeline images with auto-adaptive time headers (Days/Weeks/Months), status-colored task bars, dependency connectors, and a "Today" indicator line.
- **Discord Interaction**: Adds `/pm project timeline` slash command and integrates a `📊 Timeline` / `🌲 Tech Tree` toggle button inside `TechTreeViewer` and project Control Hubs.
- **Performance & Reliability**: Schedule inference runs in $O(V + E)$ topological time during visualization rendering, requiring no write-amplification or background polling in Postgres.
