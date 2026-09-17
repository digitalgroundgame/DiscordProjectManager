# dgg-pm: Discord-Native Task Management Platform

[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/downloads/)
[![Architecture: Hexagonal](https://img.shields.io/badge/architecture-hexagonal-green.svg)](docs/wiki/Architecture.md)
[![Schema: RFC 5545 & MS Graph](https://img.shields.io/badge/schema-RFC%205545%20%2F%20MS%20Graph-orange.svg)](docs/wiki/Architecture.md#data-standardization-rfc-5545--microsoft-graph)

A self-hosted, zero-signup Discord-native project management platform built to eliminate context-switching by embedding task workflows directly into Discord text channels, threads, and direct messages.

---

## 📚 Documentation & Wiki

All comprehensive documentation, guides, and technical references are organized in the **[DGG-PM Wiki](docs/wiki/Home.md)**:

| Guide | Description |
| :--- | :--- |
| 🚀 **[Workflow & Quickstart Guide](docs/wiki/Workflow-Guide.md)** | End-to-end setup and zero-command daily workflows |
| ⌨️ **[Slash Commands Reference (`/pm`)](docs/wiki/Slash-Commands-Reference.md)** | Complete parameter and permission breakdown |
| 🏛️ **[System Architecture](docs/wiki/Architecture.md)** | Hexagonal ports & adapters, concurrency (CAS), and outbox pattern |
| 👥 **[Squads & Authorization Matrix](docs/wiki/Teams-and-Authorization.md)** | Native role rosters, configurable lead roles, and self-healing |
| 📌 **[Forum Channels & Interactive Hubs](docs/wiki/Forum-Channels-and-Hubs.md)** | Automatic PM tag provisioning and pinned Control Hubs |
| 💻 **[Local Development & Seeding](docs/wiki/Local-Development.md)** | `uv`, NixOS/devenv, Makefile reference, and declarative seeder |
| 🚀 **[Deployment & Coolify](docs/wiki/Deployment.md)** | Coolify hosting, branch strategy, and Docker Compose stack |
| 🗄️ **[Database Migrations Manual](docs/migrations.md)** | Development and production migration runbook (Alembic) |

---

## ✨ Key Features

- **Unified `/pm` Namespace**: Single isolated command tree eliminating server clutter and bot collisions.
- **Zero-Command Workflows**: Pinned Forum **Control Hubs** and thread **Action Cards** allow creating, assigning, and progressing tasks without typing CLI commands.
- **100% Discord-Native Squads**: Live Discord server roles serve as the single source of truth for squad rosters with 3-tier self-healing lead protection.
- **Dedicated Task Threads**: Automatic forum post and thread provisioning with optimistic concurrency control (CAS) preventing lost update races.
- **Human-Friendly Short IDs**: Sequential project-prefixed IDs (e.g. `INF-1`, `PRJ-42`) with channel-scoped autocomplete.
- **Transactional Outbox**: Guaranteed notification delivery with rate-limit-aware backoff and `FOR UPDATE SKIP LOCKED` background workers.

---

## ⚡ Quickstart

### 1. Bot Invite
Install the bot to your server using the official OAuth2 install link:
> 🔗 **[Invite Bot to Discord Server](https://discord.com/oauth2/authorize?client_id=1548482366245175297&permissions=395405814864&integration_type=0&scope=bot)**

*(Ensure **Server Members Intent** is enabled under the Bot tab in the [Discord Developer Portal](https://discord.com/developers/applications)).*

### 2. Run Locally (`uv` & Docker)
```bash
# 1. Configure environment
cp .env.example .env
# Edit .env with your DISCORD_BOT_TOKEN and credentials

# 2. Install dependencies & start PostgreSQL container
make install && make db-up

# 3. Run test suite (uses in-memory SQLite; does not require Postgres)
make test

# 4. Start the application
make run
```

---

## 📋 Common Commands

```bash
make help        # List all available targets and descriptions
make check       # Run full quality gate (lint, format check, and tests)
make test        # Run pytest test suite
make seed        # Declarative sync (non-destructive; preserves channels/threads)
make db-reset    # Destructive reset (wipes DB tables and re-provisions cleanly)
make db-migrate  # Apply pending Alembic migrations
```

For NixOS (`devenv`), offline seeding (`--no-discord`), and complete Coolify deployment runbooks, see the **[Wiki](docs/wiki/Home.md)**.
