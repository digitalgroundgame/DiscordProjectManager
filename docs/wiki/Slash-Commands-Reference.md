# ⌨️ Slash Commands Reference (`/pm`)

All DGG-PM bot commands are grouped under the single `/pm` top-level namespace to prevent slash command clutter and collisions with other bots.

---

## 📌 Root Commands

| Command | Parameters | Description | Permission Required |
| :--- | :--- | :--- | :--- |
| **`/pm menu`** | None | Opens the interactive Master Project Management Control Hub. | `@everyone` |
| **`/pm help`** | None | Displays the command guide and documentation overview. | `@everyone` |
| **`/pm settings`** | `notify_preference` (optional: `dm`, `channel`, `both`, `silent`) | Configures personal notification delivery preferences. | `@everyone` |
| **`/pm setup-hub`** | `channel` (optional) | Posts and pins an interactive PM Control Center in a Forum or Text Channel. | `Manage Server` |
| **`/pm tree`** | `project_name` (required)<br>`orientation` (optional: `lr`, `tb`) | Renders the visual Civ-style tech tree dependency DAG graph. | `@everyone` |

---

## ⚡ Task Commands (`/pm task <command>`)

| Subcommand | Parameters | Description |
| :--- | :--- | :--- |
| **`create`** | `project_name` (required)<br>`title` (required)<br>`assignee` (optional)<br>`due` (optional)<br>`priority` (optional: `high`, `normal`, `low`)<br>`cc` (optional)<br>`description` (optional) | Creates a new task in a project container, provisions a dedicated thread / forum post, and attaches an interactive action card. |
| **`assign`** | `task` (required)<br>`assignee` (optional) | Assigns or unassigns a member from a task (validated against mapped project team roles). |
| **`status`** | `task` (required)<br>`status` (required: `not_started`, `in_progress`, `completed`)<br>`notes` (optional) | Updates task status with CAS optimistic concurrency control and syncs thread tags. |
| **`list`** | `project_name` (optional)<br>`user` (optional)<br>`status` (optional: `active`, `inProgress`, `notStarted`, `completed`, `all`) | Displays an interactive paginated task board with dynamic filters. |
| **`depend`** | `task` (required)<br>`depends_on` (required) | Links a prerequisite dependency (`task` requires `depends_on` to finish first). |
| **`undepend`** | `task` (required)<br>`depends_on` (required) | Unlinks/removes a prerequisite dependency relationship. |
| **`history`** | `task` (required) | Displays the full timestamped audit log of all changes, notes, and status transitions. |
| **`archive`** | `task` (required) | Archives a task and closes its associated Discord thread. |
| **`unarchive`** | `task` (required) | Restores an archived task. |
| **`watchers`** | `task` (required)<br>`action` (required: `add`, `remove`, `clear`)<br>`member` (optional) | Manages watcher CC subscriptions on a task. |

---

## 📁 Project Commands (`/pm project <command>`)

| Subcommand | Parameters | Description | Permission Required |
| :--- | :--- | :--- | :--- |
| **`create`** | `name` (required)<br>`prefix` (required)<br>`role` (required: `@Role`)<br>`channel` (optional)<br>`description` (optional)<br>`category` (optional) | Creates a project container, maps the squad Discord role, and automatically provisions standard tags + pinned Control Hub if a forum channel is linked. | `Manage Server` |
| **`tree`** | `project_name` (required)<br>`orientation` (optional: `lr`, `tb`) | Renders the interactive visual dependency graph for the project. | `@everyone` |
| **`role`** | `project_name` (required)<br>`role` (required: `@Role`)<br>`action` (required: `add`, `remove`) | Maps or unmaps additional Discord squad roles to a project container (for cross-functional squads). | `Manage Server` |
| **`squad`** | `project_name` (required)<br>`squad_name` (required)<br>`action` (required: `add`, `remove`) | Maps or unmaps a functional squad to a project container (alias: `team`). | `Manage Server` |
| **`lead`** | `project_name` (required)<br>`user` (required: `@Member`)<br>`action` (required: `add`, `remove`) | Designates or removes a Squad Lead for the project's squads. | `Manage Server` OR Active Squad Lead |
| **`list`** | None | Lists all active project containers and their bound Discord channels. | `@everyone` |
| **`archive`** | `project_name` (required) | Archives a project container and cascades thread archiving. | `Manage Server` |
| **`unarchive`** | `project_name` (required) | Restores an archived project container and reopens task threads. | `Manage Server` |
| **`setup_forum`** | `forum` (required) | Automatically configures standard PM tags on a Discord Forum Channel. | `Manage Server` |

---

## 👥 Squad Commands (`/pm squad <command>`)

*(Backwards compatible alias: `/pm team <command>`)*

| Subcommand | Parameters | Description | Permission Required |
| :--- | :--- | :--- | :--- |
| **`create`** | `role` (required)<br>`squad_name` (optional) | Creates a functional squad mapped to an existing Discord Server Role. | `Manage Server` |
| **`lead`** | `action` (required: `add`, `remove`)<br>`squad_name` (required)<br>`user` (required) | Designates or removes a Squad Lead by squad name. | `Manage Server` OR Active Squad Lead |
| **`list`** | None | Displays all squads, designated leads, and live Discord role member counts. | `@everyone` |
