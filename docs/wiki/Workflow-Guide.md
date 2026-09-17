# 🚀 DGG-PM Workflow Guide

This guide walks you through setting up project containers, binding Discord roles and forum channels, and managing day-to-day task execution.

---

## 🏗️ Architecture & Flow Overview

```mermaid
flowchart TD
    subgraph Setup ["1. Instant Project Setup (1 Command)"]
        P["/pm project create\n(name + prefix + role + forum)"] --> F["Auto-Setup PM Forum Tags & Pinned Control Hub"]
        P --> R["Auto-Maps Discord Squad Role to Project"]
        R --> L["(Optional) Designate Leads (/pm project lead)"]
    end

    subgraph Execution ["2. Daily Workflows"]
        Hub["Pinned Forum Control Hub"] --> TaskCreate["Create Task (Modal / Slash)"]
        TaskCreate --> Thread["Auto-Created Thread / Post"]
        Thread --> Card["Interactive Action Card"]
        Card --> Mutate["Update Status / Priority / Notes / Assignee"]
    end

    Setup --> Execution
```

---

## ⚙️ Prerequisites & Discord Developer Portal Setup

Before creating projects and running commands in your server, configure your bot application and invite it to your Discord server:

1. **Bot Invite / Installation Link**:
   - Install the bot into your server using the official OAuth2 install link:
     [Invite Bot to Discord Server](https://discord.com/oauth2/authorize?client_id=1548482366245175297&permissions=395405814864&integration_type=0&scope=bot)
     ```text
     https://discord.com/oauth2/authorize?client_id=1548482366245175297&permissions=395405814864&integration_type=0&scope=bot
     ```
2. **Privileged Gateway Intents**:
   - Ensure **Server Members Intent** (`GuildMembers`) is enabled in the [Discord Developer Portal](https://discord.com/developers/applications) under **Bot** ➔ **Privileged Gateway Intents** (required for squad roster synchronization and self-healing lead checks).
   - *(Note: `Message Content` intent is explicitly **NOT** required).*
3. **Bot Permissions**:
   - The invite URL automatically requests the required permissions:
     - `Manage Roles`
     - `Manage Channels` (for auto-tagging Forum channels)
     - `Manage Threads`
     - `View Channels`
     - `Send Messages`
     - `Send Messages in Threads`
     - `Create Public Threads`
     - `Manage Messages`
     - `Embed Links`
     - `Read Message History`
     - `Attach Files`


---

## 🛠️ Step-by-Step Setup (Admins & Leads)

### 1. (Optional) Authorize Team Lead Roles (Server Managers)
Server managers can delegate project creation and management authority to trusted team lead roles without granting full Discord administrator permissions:

- **Via Interactive Menu**: Run `/pm menu`, click the **`Lead Roles`** button, select the role (e.g. `@Engineering Lead`) from the dropdown, and click **`Assign Role`**.
- **Via Slash Command**:
  ```text
  /pm admin lead-role action:add role:@Engineering Lead
  ```

Members with this role can now create projects, bind channels, and map squads across the server.

---

### 2. Create a Project & Map Discord Role
Projects can be created by Server Managers or any member holding an Authorized Team Lead role.

#### Option A: Zero-Command Interactive Creation
1. From any pinned **Control Hub**, click **`📁 Create Project`** (or open `/pm menu` and click **`Create Project`**).
2. Select an existing Forum Channel (or choose to create a new one).
3. Fill out the popup modal with Project Name, Prefix, and primary squad role.

#### Option B: Slash Command
```text
/pm project create name:Mobile App prefix:MOB role:@Mobile Developers channel:#mobile-dev-forum
```

**What the bot does automatically:**
1. **Assigns Squad**: Directly maps `@Mobile Developers` to the project container. Only members holding this role can be assigned to tasks.
2. **Generates Sequential IDs**: Starts task counter with the short prefix (`MOB-1`, `MOB-2`, etc.).
3. **Provisions Forum Tags**: Automatically configures standard PM tags (`⏳ Not Started`, `🟡 In Progress`, `✅ Completed`, `🔴 High`, `🟡 Normal`, `🟢 Low`, `👤 Unassigned`).
4. **Pins Control Hub**: Automatically creates and pins **`📌 📊 Mobile App • Control Hub`** right at the top of the forum channel.

---

### 3. Designate Squad Leads
Assign one or more members holding the project's role as Squad Leads:

```text
/pm project lead project_name:Mobile App user:@Alice action:add
```
> [!NOTE]
> Squad leads can manage task assignments, squad rosters, and mutations for their project. If an admin strips the Discord role from a user in server settings, their lead privileges are revoked immediately and automatically cleaned up via our 3-tier self-healing system.

---

### 4. Optional: Add Additional Roles (Cross-Functional Projects)
If a project needs multiple squads (e.g. adding `@QA` or `@Design`):

```text
/pm project role project_name:Mobile App role:@QA Engineers action:add
```

---

## 💼 Daily Team Workflows

### Option 1: Zero-Command Workflow (Recommended)
You never need to remember or type slash commands for daily work.

1. **Click Pinned Hub**: Navigate to your project forum and click into the pinned `📌 📊 Control Hub`.
2. **Create Task**: Click **`⚡ Tasks Hub`** ➔ **`➕ New Task`** to open the creation modal with project and assignee dropdowns.
3. **Work Inside Task Post**:
   - Each task has its own forum thread post (e.g. `[MOB-1] Implement OAuth login`).
   - Use the **Interactive Action Card** buttons directly in the thread:
     - 🚀 **1-Click Status**: `Start Task`, `In Progress`, `Complete`, `Reopen`.
     - 📝 **Add Note**: Record progress notes and audit events in a popup modal.
     - ✏️ **Edit Details**: Update task title, description, due date, or watchers.
     - 🔗 **Dependencies**: Interactively link/unlink prerequisite and dependent tasks.
     - 🎛️ **Quick Controls**: Access quick dropdowns for assigning members, setting priority (`High`, `Normal`, `Low`), changing due dates, and archiving.

---

### Option 2: Slash Commands (`/pm`)

#### Creating Tasks
```text
/pm task create project_name:Mobile App title:Implement OAuth login assignee:@Bob priority:high due:tomorrow 5pm
```

#### Modifying & Assigning Tasks
- **Assign / Reassign**: `/pm task assign task:MOB-1 assignee:@Bob`
- **Update Status**: `/pm task status task:MOB-1 status:in_progress notes:Started backend endpoint`
- **Audit Trail & History**: `/pm task history task:MOB-1`
- **Archive Task**: `/pm task archive task:MOB-1`

#### Filtered Task Board
```text
/pm task list project_name:Mobile App status:active
```

---

## ⚙️ Personal Settings & Notification Delivery

Team members can customize how and where they receive task assignment and reminder alerts:

```text
/pm settings notify_preference:both
```

- **`dm`**: Receive direct messages from the bot.
- **`channel`**: Receive `@mentions` inside the task thread channel.
- **`both`**: Receive both direct messages and thread channel pings.
- **`silent`**: Silent / no proactive notifications.
