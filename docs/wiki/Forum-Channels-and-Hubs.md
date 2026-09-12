# 📌 Forum Channels & Interactive Hubs

Discord Forum Channels provide an ideal workspace for task management. DGG-PM automatically configures tags, pins an interactive control hub, and creates structured thread posts for every task.

---

## 🏷️ Standard Forum Tags Provisioning

When a project is bound to a `discord.ForumChannel` (or via `/pm project setup-forum`), the bot automatically configures standard project management tags up to Discord's 20-tag limit while preserving custom existing tags:

| Category | Standard Tags | Purpose |
| :--- | :--- | :--- |
| **Status** | `⏳ Not Started`, `🟡 In Progress`, `✅ Completed` | Matches and syncs with the task's execution state. |
| **Priority** | `🔴 High Priority`, `🟡 Normal Priority`, `🟢 Low Priority` | Visual urgency indicator. |
| **Assignment** | `👤 Unassigned` | Automatically applied when a task has no assignee; automatically removed when assigned. |
| **Project** | `📁 <Project Name>` | One tag per project bound to the forum, enabling easy filtering of tasks by project. |

---

## 🎛️ Pinned Control Hub (`📌 📊 Control Hub`)

In Discord Forum Channels, users cannot type slash commands at the forum root level. 

To make forum channels completely self-sufficient:
- When projects are linked to a forum channel, the bot automatically creates and **pins** a permanent thread post: **`📌 📊 [Channel Name / Project] • Control Hub`**.
- The post contains persistent interactive buttons:
  - **`➕ New Task`**: Opens the interactive Task Creation Builder (in multi-project channels, first prompts with a project selection menu).
  - **`👤 My Tasks`**: Launches a private personal dashboard showing assigned tasks, deadlines, and status.
  - **`📁 Projects Hub`**: Launches a private project directory workspace.
  - **`🌲 Visual Graph`**: Renders the interactive visual dependency DAG diagram (with project selector if multiple projects are bound).

### Single-Project vs. Multi-Project Forums

1. **Single-Project Hub**:
   - Focuses directly on the bound project with its mapped squad role and description.
2. **Multi-Project Hub (e.g. `#🌐-regional-organizing`)**:
   - Outlines all bound projects in Discord blockquote cards:
     ```text
     ### Bound Projects in this Channel

     **[INTL] International Chapter**
     > **Squad Role**: @international
     > *Global community organizing, language translation working groups, and timezone coordination.*

     **[MW] Midwest Chapter**
     > **Squad Role**: @midwest
     > *Regional organizing across Midwest counties, grassroots hubs, and community networks.*
     ```
   - When a user clicks **`➕ New Task`** or **`🌲 Visual Graph`**, an ephemeral selector prompts the user to choose which project container to target.

```
┌──────────────────────────────────────────────────────────┐
│  📌 📊 Mobile App • Control Hub (Public / Pinned)        │
│  Interactive management dashboard for Mobile App.        │
│                                                          │
│  [ ➕ New Task ] [ 👤 My Tasks ]                         │
│  [ 📁 Projects Hub ] [ 🌲 Visual Graph ]                 │
└──────────────────────────┬───────────────────────────────┘
                           │ (Alice clicks [ ➕ New Task ])
                           ▼
┌──────────────────────────────────────────────────────────┐
│  📝 Task Draft (Only visible to Alice • Ephemeral)       │
│  • Scope: 📁 Mobile App  • Priority: 🔵 Normal           │
│  • Assignee: 👤 Unassigned                               │
│                                                          │
│  [ 🚀 Create Task ] [ ✏️ Edit Details ] [ ❌ Cancel ]     │
│  [ 👤 Select Assignee Member (UserSelect)              ] │
│  [ 📅 Quick Due Date Preset Picker                     ] │
│  [ ⚡ Priority Dropdown (High / Normal / Low)           ] │
│  [ 👀 Watchers / CC Multi-Select                       ] │
└──────────────────────────────────────────────────────────┘
```

> [!TIP]
> **Zero Public Disruption**: All button interactions from the pinned hub respond **ephemerally** or open popup modals. The public pinned post remains pristine and is never altered or replaced when individual members interact with it.

---

## 🗂️ Task Workspace Threads & Action Cards

Every task created in a forum channel gets its own dedicated thread post:
1. **Title**: `[MOB-1] Implement OAuth login`
2. **Forum Tags**: Automatically matches status and priority tags (e.g. `🟡 In Progress`, `🔴 High Priority`).
3. **Forum Post Content (Preview Snippet)**:
   - Formatted Markdown task description at the top (under 1500 chars).
   - Clean inline metadata summary:
     `**Assignee**: @Alice • **Priority**: Normal • **Watchers**: @Bob`
4. **Rich Embed Card**: Detailed overview, progress status, due date, timestamps, prerequisite status, and version history.
5. **Interactive Action Card**:
   - **Primary Actions**: `[ 🚀 Start Task / 🔄 In Progress / ✅ Complete / 🔁 Reopen ]` `[ 📝 Add Note ]`
   - **Workspace Tools**: `[ ✏️ Edit Details ]` `[ 🔗 Dependencies ]` `[ 🎛️ Quick Controls ]`
     - **Edit Details Modal**: Live editing of title, description, due date, and watchers.
     - **Dependencies**: Interactive selector to link/unlink prerequisites and unlock blockers.
     - **Quick Controls**: Member assignee picker, priority selector, due date presets, and task archiving.

Updating any button instantly updates the database, refreshes the forum post preview snippet, updates the card embed, and synchronizes the forum tags without reloading or command typing.
