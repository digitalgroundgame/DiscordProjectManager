# 📖 DGG-PM Documentation & Wiki

Welcome to the **DGG-PM** GitHub Wiki!

**DGG-PM** is a self-hosted, Discord-native project management platform designed to eliminate context-switching by embedding task tracking directly into Discord text channels, threads, forum posts, and direct messages.

---

## 🧭 Wiki Navigation

1. **[Workflow & Quickstart Guide](Workflow-Guide.md)**
   - Discord Developer Portal configuration & bot permissions.
   - Initial server setup (Teams, Projects, Forums).
   - Zero-command workflows with Pinned Control Hubs.
   - Creating, assigning, and executing tasks.

2. **[Slash Commands Reference (`/pm`)](Slash-Commands-Reference.md)**
   - Complete reference of all `/pm` grouped slash commands.
   - Arguments, permissions, options, and autocomplete.

3. **[Squads & Authorization Matrix](Teams-and-Authorization.md)**
   - Discord-native role membership.
   - Configurable Team Lead roles for delegated project management.
   - Squad Leads & 3-tier self-healing protection.
   - Role-restricted task assignments and mutation guards.

4. **[Forum Channels & Interactive Hubs](Forum-Channels-and-Hubs.md)**
   - Automatic PM tag provisioning (Status, Priority, Unassigned).
   - Automated pinned control center post creation with direct Project & Task creation.
   - Dynamic Task Action Cards & thread workspaces.

5. **[System Architecture](Architecture.md)**
   - Hexagonal architecture layer breakdown (Ports & Adapters).
   - Concurrency guarantees: Optimistic Concurrency Control (CAS) & Transactional Outbox.
   - Data standardization with RFC 5545 (`VTODO`) and Microsoft Graph (`todoTask`).

6. **[Local Development & Seeding](Local-Development.md)**
   - Local setup with `uv`, Docker, and NixOS/devenv.
   - Common `Makefile` target cheat sheet.
   - Declarative seeding engine (`make seed`, `make db-reset`, `--no-discord`).

7. **[Deployment & Coolify](Deployment.md)**
   - Turnkey deployment to Coolify (Docker Compose & Standalone).
   - Recommended Git branch strategy (`develop` ➔ `main`).
   - Self-hosted Docker Compose deployment runbook.

8. **[Database Migrations Manual (Alembic)](../migrations.md)**
   - Migration management with Alembic for development and production.
   - Revision creation, testing, rollback runbooks, and zero-downtime rules.

---

## ⚡ Core Design Principles

- **Single Namespace (`/pm`)**: No top-level slash command clutter or collisions with other server bots.
- **100% Discord-Native**: Discord Server Roles are the real-time source of truth for squad rosters.
- **Flexible Least-Privilege Delegation**: Authorize designated Discord roles as Team Leads for project and squad management without granting server-wide administrator permissions.
- **Zero-Command Workflows**: Pinned Forum Hubs, Modals, Dropdowns, and Action Cards allow daily operations without typing CLI commands.
- **Self-Healing State**: Stripping a Discord role instantly revokes lead privileges and cleans up database records automatically.

