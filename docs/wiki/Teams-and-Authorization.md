# 👥 Squads & Authorization Matrix

DGG-PM implements a Discord-native permission model. Discord server roles act as the real-time source of truth for squad membership, eliminating manual user synchronization.

---

## 🔐 Permission Roles & Hierarchy

```
┌────────────────────────────────────────────────────────┐
│  Server Managers (Manage Server / Administrator)       │
│  - Full server bypass; configure Team Lead roles       │
└──────────────────────────┬─────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────┐
│  Authorized Team Lead Roles & Active Squad Leads       │
│  - Create and manage projects, channels, & squads      │
│  - Granted via server role without Discord admin perms │
└──────────────────────────┬─────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────┐
│  Squad Leads (Designated in DB + Holds Discord Role)   │
│  - Manage squad roster and task mutations              │
└──────────────────────────┬─────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────┐
│  Squad Members (Hold Mapped Discord Squad Role)        │
│  - Assignable to tasks; mutate tasks in their project  │
└──────────────────────────┬─────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────┐
│  Task Assignees & Creators                             │
│  - Mutate, update status, and manage their own tasks   │
└────────────────────────────────────────────────────────┘
```

---

## 🛡️ Mutation Authorization Matrix

| Action | Server Manager | Authorized Team Lead Role | Squad Lead | Squad Member (Mapped Role) | Task Assignee | Task Creator | Other Server Member |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Configure Lead Roles** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Create Project / Squad** | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Manage Project (Role, Archive)** | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Designate / Remove Squad Lead** | ✅ | ✅ | ✅ (Own Squad) | ❌ | ❌ | ❌ | ❌ |
| **Create Project Task** | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| **Mutate / Edit Task** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Assign Task to Member** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ (Target must hold squad role) |
| **Self-Service Watchers (CC)** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ (Add/Remove Self) |

---

## 🎖️ Configurable Team Lead Roles

To adhere to the principle of least privilege, DGG-PM allows server managers to authorize specific Discord server roles as **Team Lead roles**. Members holding these roles can create and manage project containers, map squads, bind channels, and designate leads **without requiring server-wide `Manage Server` or `Administrator` permissions**.

### 1. Interactive Admin Dashboard (`/pm menu`)
1. Open the project dashboard using `/pm menu`.
2. Members with `Manage Server` or `Administrator` permissions will see a **`Lead Roles`** button in the top action row.
3. Clicking **`Lead Roles`** opens the interactive role management portal:
   - Displays all currently authorized Team Lead roles in an embed list.
   - Includes a native Discord **Role Select** dropdown to select any server role.
   - Provides 1-click **`Assign Role`** and **`Remove Role`** actions.
   - Includes a **`Back to Dashboard`** button to return cleanly.

### 2. Slash Command Management (`/pm admin lead-role`)
Server Managers can also configure authorized roles via slash commands:
- **Add a Lead Role**:
  ```text
  /pm admin lead-role action:add role:@Engineering Lead
  ```
- **Remove a Lead Role**:
  ```text
  /pm admin lead-role action:remove role:@Engineering Lead
  ```
- **List All Lead Roles**:
  ```text
  /pm admin lead-role action:list
  ```

---

## 🔄 3-Tier Self-Healing for Orphaned Squad Leads

If an administrator strips a Discord role from a user in server settings (or if the member leaves the server):

1. **Tier 1: Instant Authorization Guard**:
   - `AuthService.can_manage_squad_leads` validates that the user is recorded in the database **and** actively holds the squad's `discord_role_id`.
   - The moment their Discord role is removed, they lose all Squad Lead authority instantly.

2. **Tier 2: Event-Driven Automatic Pruning**:
   - Discord bot event listeners (`on_member_update` and `on_member_remove`) immediately detect when a squad role is stripped or when a member leaves the server and delete their record from PostgreSQL.

3. **Tier 3: Display-Time Reconciliation**:
   - The interactive Squad Roster detail menu and project views cross-reference database records against live Discord `role.members` and automatically clean up any lingering records on-the-fly.

