# 🚀 Deployment Guide (Coolify & Docker)

This guide covers production deployment strategies for **DGG-PM**, including Docker Compose and automated deployments via [Coolify](https://coolify.io).

---

## Recommended Branch Strategy

- **`develop`**: Active development and integration branch. All features, PRs, and daily changes land here.
- **`main`**: Production-ready branch tracked by Coolify or CI/CD for automated builds.
- **Promoting to Production**:
  ```bash
  # Fast-forward main to current develop
  git push origin develop:main
  ```
  *(Or open and merge a Pull Request from `develop` into `main` on GitHub).*

---

## Deploying to Coolify

DGG-PM is pre-configured for turnkey hosting on [Coolify](https://coolify.io).

### Option A: Docker Compose Stack (Recommended)

1. In your Coolify project dashboard, click **Add Resource** ➔ **Docker Compose**.
2. Connect your Git repository and set the branch to **`main`**.
3. Configure the required environment variables in Coolify:
   - `DISCORD_BOT_TOKEN`: Your Discord bot token.
   - `DISCORD_CLIENT_ID`: Your Discord application client ID.
   - `DISCORD_GUILD_ID`: *(Optional)* Guild ID for instant slash command registration.
4. Click **Deploy**.
   - Migrations run automatically on startup (`AUTO_RUN_MIGRATIONS=true`).
   - The container healthcheck actively monitors `/healthz`.

### Option B: Standalone Application + Managed Coolify PostgreSQL

1. Provision a PostgreSQL 16+ database resource in Coolify.
2. Create a new **Application** pointing to this repository (Build Pack: **Dockerfile**, Branch: **`main`**).
3. Configure the following environment variables:
   - `DATABASE_URL`: PostgreSQL connection string (standard `postgres://` and `postgresql://` URIs are automatically normalized to asyncpg).
   - `AUTO_RUN_MIGRATIONS`: `true`
   - `DISCORD_BOT_TOKEN`: Your Discord bot token.
   - `DISCORD_CLIENT_ID`: Your Discord application ID.
   - `DISCORD_GUILD_ID`: *(Optional)*
4. In Coolify Application Settings, set:
   - **Port**: `8000`
   - **Health Check Path**: `/healthz`

---

## Deploying with Docker Compose (Self-Hosted)

For running directly on a Linux VPS or self-hosted server using Docker Compose:

1. Clone the repository and configure your environment:
   ```bash
   cp .env.example .env
   # Edit .env with your DISCORD_BOT_TOKEN and credentials
   ```

2. Build and launch containers:
   ```bash
   docker compose up -d --build
   ```

3. View live application logs:
   ```bash
   docker compose logs -f app
   ```

4. Stop services:
   ```bash
   docker compose down
   ```
