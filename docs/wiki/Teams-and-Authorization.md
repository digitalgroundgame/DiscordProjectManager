# 👥 Squads & Authorization Matrix

DGG-PM implements a Discord-native permission model. Discord server roles act as the real-time source of truth for squad membership, eliminating manual user synchronization.

---

## 🔐 Permission Roles & Hierarchy

```
┌────────────────────────────────────────────────────────┐
│  Server Managers (Manage Server / Administrator)       │
│  - Full bypass across all projects, tasks, and squads  │
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

| Action | Server Manager | Squad Lead | Squad Member (Mapped Role) | Task Assignee | Task Creator | Other Server Member |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Create Project / Squad** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Designate / Remove Lead** | ✅ | ✅ (Own Squad) | ❌ | ❌ | ❌ | ❌ |
| **Create Project Task** | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| **Mutate / Edit Task** | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Assign Task to Member** | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ (Target must hold squad role) |
| **Self-Service Watchers (CC)** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ (Add/Remove Self) |

---

## 🔄 3-Tier Self-Healing for Orphaned Squad Leads

If an administrator strips a Discord role from a user in server settings (or if the member leaves the server):

1. **Tier 1: Instant Authorization Guard**:
   - `AuthService.can_manage_squad_leads` validates that the user is recorded in the database **and** actively holds the squad's `discord_role_id`.
   - The moment their Discord role is removed, they lose all Squad Lead authority instantly.

2. **Tier 2: Event-Driven Automatic Pruning**:
   - Discord bot event listeners (`on_member_update` and `on_member_remove`) immediately detect when a squad role is stripped or when a member leaves the server and delete their record from PostgreSQL.

3. **Tier 3: Display-Time Reconciliation**:
   - `/pm squad list` (alias: `/pm team list`) and the interactive Squad Roster detail menu cross-reference database records against live Discord `role.members` and automatically clean up any lingering records on-the-fly.
