# DGG-PM Domain Context

Discord-native project management platform designed to eliminate context-switching by embedding task tracking directly into Discord text channels, threads, forum posts, and direct messages.

## Language

### Core Entities

**Task**:
A distinct unit of work anchored to a Project container with a title, status, priority, optional due date, assignee, watchers, and prerequisite dependencies.
_Avoid_: Issue, ticket, work item, todo, standalone task

**Overdue State**:
An orthogonal temporal condition of an active task whose target due date has passed without completion, preserving its underlying lifecycle status while triggering warning indicators and forum tags.
_Avoid_: Overdue status, expired status, late state


**Project**:
A high-level container aggregating related tasks, identified by a unique server prefix (e.g. `INF`), bound Discord channel, mapped Squad roles, and designated project lead.
_Avoid_: Workspace, board, category, epic

**Squad**:
A functional contributor group mapped 1:1 with a Discord Server Role.
_Avoid_: Team, group, department, user group

**Squad Lead**:
A contributor designated in the database to manage a Squad, whose authority is strictly contingent on actively holding the Squad's Discord Server Role.
_Avoid_: Team lead, manager, supervisor

**Squad Member**:
A Discord member verified to hold a Squad's mapped Discord Server Role and enrolled in the Squad roster.
_Avoid_: Team member, squad user, contributor, worker

**Project Lead**:
A designated member accountable for an entire Project container across all its assigned Squads.
_Avoid_: Project manager, scrum master

### Discord Workspaces & Interaction

**Project Workspace**:
The Discord presence and channel environment bound to a Project, comprising a Forum Channel (or Text Channel) configured with standardized PM tags, Squad role permissions, and a pinned Control Hub.
_Avoid_: Project channel, board, server section

**Thread Workspace**:
A dedicated Discord thread (within a Forum Channel or Text Channel) provisioned automatically for a Task to house discussion, activity logs, and status controls.
_Avoid_: Comment thread, channel, topic

**Task Action Card**:
The interactive root message embed and button matrix mounted inside a Thread Workspace allowing zero-command task mutations (status change, reassignment, notes, dependencies).
_Avoid_: Card, widget, button view

**Control Hub**:
The pinned administration and quick-action post located in a project's Forum Channel for browsing projects, creating tasks, and viewing tech trees.
_Avoid_: Pinned post, dashboard message, channel header

**Forum Channel**:
A native Discord forum channel configured with standardized PM tags (Status, Priority, Squad) and bound to a Project.
_Avoid_: Forum, board, category

### Asynchronous Pipeline & Dependencies

**Outbox Event**:
A transactional event record enqueued atomically with database mutations and dispatched asynchronously to Discord channels and DMs.
_Avoid_: Message event, webhook, notification record

**Tech Tree**:
A Directed Acyclic Graph (DAG) representing prerequisite dependency relationships between tasks in a project.
_Avoid_: Dependency graph, gantt chart, blocker list

**Notification Preference**:
A member-configured delivery setting (`DM`, `CHANNEL`, `BOTH`, or `SILENT`) that governs how task updates and watcher summaries are dispatched.
_Avoid_: Alert setting, notification channel, delivery mode
