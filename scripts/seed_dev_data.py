import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Add project root to sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import discord  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.adapters.db.postgres_repo import (  # noqa: E402
    PostgresOutboxRepo,
    PostgresProjectRepo,
    PostgresTaskRepo,
)
from src.adapters.db.session import async_session_factory, close_db, init_db  # noqa: E402
from src.adapters.db.unit_of_work import SqlAlchemyUnitOfWork  # noqa: E402
from src.adapters.discord_bot.views.forum_helpers import (  # noqa: E402
    STANDARD_PM_TAG_DEFINITIONS,
    resolve_forum_tags,
)
from src.adapters.discord_bot.views.task_buttons import TaskActionView  # noqa: E402
from src.adapters.discord_bot.views.task_embed import (  # noqa: E402
    build_task_embed,
    build_thread_workspace_content,
)
from src.config import settings  # noqa: E402
from src.domain.enums import PriorityLevel, TaskStatus  # noqa: E402
from src.services.outbox_service import OutboxService  # noqa: E402
from src.services.project_service import ProjectService  # noqa: E402
from src.services.task_service import TaskService  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed_dev_data")

ACTIVE_PROJECT_SEEDS = [
    ("Scale Testing Platform", "SCALE", "Large-scale stress testing project with 100 seeded tasks", "QA & Testing"),
    ("Cloud Infrastructure", "CLD", "AWS & Kubernetes migration and cluster management", "DevOps"),
    ("Design System 2.0", "DSG", "Figma to React tokens, dark mode and accessible UI components", "Design"),
    ("Auth & Identity V2", "AUTH", "OAuth2, WebAuthn passkeys, and multi-tenant SSO", "Security"),
    ("Mobile App iOS", "IOS", "SwiftUI native client, offline sync, and widgets", "Mobile"),
    ("Mobile App Android", "AND", "Jetpack Compose client, push notifications, and biometric auth", "Mobile"),
    ("GraphQL Federation", "GQL", "Apollo Router subgraph unification and schema registry", "Backend"),
    ("Analytics & BI Engine", "ANL", "ClickHouse real-time event aggregation and dashboards", "Data"),
    ("Payment Gateway", "PAY", "Stripe subscription billing, tax calculation, and invoicing", "FinTech"),
    ("Search & Discovery", "SRCH", "Elasticsearch full-text search, typo tolerance, and filters", "Backend"),
    ("Notification Hub", "NOTIF", "Multi-channel dispatch (Email, Discord, SMS, WebPush)", "Core"),
    ("Kubernetes Fleet", "K8S", "GitOps ArgoCD multi-cluster rollout and node auto-scaling", "DevOps"),
    ("Data Pipeline", "DTP", "Kafka real-time event streaming and dbt transformations", "Data"),
    ("Security Hardening", "SEC", "Zero-trust network access, secrets rotation, and audit logs", "Security"),
    ("Admin Portal", "ADM", "Internal staff tooling, user impersonation, and feature controls", "Internal"),
    ("Developer Docs", "DOC", "Public OpenAPI documentation, SDK generation, and guides", "Product"),
    ("CI/CD Automation", "CICD", "GitHub Actions test runner caching and Docker build matrix", "DevOps"),
    ("Billing Overhaul", "BIL", "Enterprise custom contracts, credit systems, and usage metering", "FinTech"),
    ("Incident Response", "INC", "PagerDuty integration, automated runbooks, and postmortems", "Operations"),
    ("Customer Support Board", "SUP", "Zendesk sync, ticketing escalation queue, and SLA tracking", "Support"),
    ("Email Dispatcher", "EML", "Transactional template engine with SendGrid / SES failover", "Core"),
    ("Chat & WebSockets Relay", "CHAT", "Real-time websocket mesh with Redis Pub/Sub backend", "Backend"),
    ("Observability & Tracing", "OBS", "OpenTelemetry distributed tracing, Prometheus, and Grafana", "DevOps"),
    ("Feature Flags Engine", "FF", "Targeted percentage rollouts, A/B testing, and kill switches", "Core"),
    ("Redis Caching Tier", "REDIS", "Distributed Redis cluster, read replicas, and cache invalidation", "Infra"),
    ("Asset Optimization Pipeline", "AST", "On-the-fly WebP transcoding, AVIF conversion, and CDN edge rules", "Media"),
    ("Identity Provider Federation", "IDP", "SAML 2.0 / Okta / Azure AD enterprise connector", "Security"),
    ("Audit Compliance Logger", "AUD", "SOC2 and HIPAA immutable tamper-proof audit trail", "Compliance"),
    ("MLOps Inference Platform", "ML", "PyTorch model serving with Triton and vLLM GPU nodes", "AI/ML"),
    ("SEO & Landing Booster", "SEO", "Next.js SSR edge rendering, schema markup, and sitemaps", "Marketing"),
    ("Telemetry & Metrics", "TLM", "Client crash reporting and Core Web Vitals telemetry collector", "Analytics"),
]

ARCHIVED_PROJECT_SEEDS = [
    ("Legacy Monolith", "LEG", "Old monolithic codebase sunset and decommissioned", "Legacy"),
    ("Migration 2025", "M25", "Database migration project completed in Q4 2025", "Infrastructure"),
    ("Beta Early Access", "BETA", "Closed beta customer feedback collection program", "Product"),
    ("Old Billing V1", "OBIL", "Legacy chargebee billing system replaced by Stripe", "FinTech"),
    ("Black Friday 2025 Campaign", "BF25", "Holiday traffic scale-up and promotional event board", "Marketing"),
    ("Redis v5 Standalone", "RD5", "Single node redis instance decommissioned in favor of cluster", "Infra"),
    ("PHP API Gateway", "PHP", "Sunset legacy PHP endpoints in favor of FastAPI & Go", "Legacy"),
    ("Mobile App V1 (Cordova)", "MOB1", "Hybrid web app replaced by native iOS and Android clients", "Mobile"),
    ("Spring 2025 Rebrand", "SR25", "Brand assets and style guide revamp completed", "Design"),
    ("CentOS 7 Sunset", "COS", "Operating system fleet migration to Ubuntu 24.04 LTS", "DevOps"),
    ("Old Landing Page", "LAND1", "Initial static marketing website retired", "Marketing"),
    ("Solr Search Cluster", "SOLR", "Apache Solr replaced by Elasticsearch cluster", "Search"),
]

SCALE_100_TASK_TEMPLATES = [
    # (Title, Priority, Status, Days Offset)
    ("Load test WebSocket connections up to 50,000 CCU", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 1),
    ("Benchmark PostgreSQL pg_stat_statements query indexes", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 2),
    ("Redis distributed lock TTL renewal under network partitions", PriorityLevel.NORMAL, TaskStatus.NOT_STARTED, 5),
    ("Simulate Kafka consumer group rebalance storm under load", PriorityLevel.NORMAL, TaskStatus.NOT_STARTED, 7),
    ("Validate 100-task pagination and search indexing performance", PriorityLevel.LOW, TaskStatus.COMPLETED, None),
    ("Asyncpg connection pool exhaustion edge-case testing", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 2),
    ("GraphQL query depth limiting and complexity cost analysis", PriorityLevel.NORMAL, TaskStatus.NOT_STARTED, 4),
    ("ClickHouse high-frequency batch insert buffer benchmarks", PriorityLevel.NORMAL, TaskStatus.IN_PROGRESS, 6),
    ("Discord rate-limit backoff handler retry loop validation", PriorityLevel.HIGH, TaskStatus.COMPLETED, None),
    ("Multi-tenant database schema isolation & tenant leak test", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 3),
    ("Elasticsearch synonym token filter memory usage benchmark", PriorityLevel.NORMAL, TaskStatus.NOT_STARTED, 8),
    ("OAuth2 token refresh race condition stress test", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 1),
    ("Outbox worker event processing lag monitoring under 100k events", PriorityLevel.HIGH, TaskStatus.NOT_STARTED, 4),
    ("Memory leak profiling in FastAPI background workers", PriorityLevel.NORMAL, TaskStatus.COMPLETED, None),
    ("S3 multipart upload concurrency resilience under packet loss", PriorityLevel.LOW, TaskStatus.NOT_STARTED, 10),
    (
        "SQLAlchemy asyncio session lifecycle and transaction rollback check",
        PriorityLevel.NORMAL,
        TaskStatus.IN_PROGRESS,
        5,
    ),
    ("Docker container startup time optimization (multi-stage build)", PriorityLevel.LOW, TaskStatus.COMPLETED, None),
    (
        "Kubernetes HPA CPU/Memory metrics scraping interval calibration",
        PriorityLevel.NORMAL,
        TaskStatus.NOT_STARTED,
        9,
    ),
    ("Prometheus metrics cardinality explosion mitigation", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 2),
    ("gRPC streaming connection termination and reconnect backoff", PriorityLevel.NORMAL, TaskStatus.NOT_STARTED, 12),
    ("OpenTelemetry trace span sampling rate tuning under 20k RPS", PriorityLevel.LOW, TaskStatus.COMPLETED, None),
    ("Redis cluster failover sentinel heartbeat timeout tuning", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 3),
    ("PostgreSQL vacuum freeze threshold tuning on large tables", PriorityLevel.HIGH, TaskStatus.NOT_STARTED, 6),
    ("Playwright end-to-end parallel test suite execution in CI", PriorityLevel.NORMAL, TaskStatus.IN_PROGRESS, 4),
    ("Chaos Mesh network latency injection on auth service pods", PriorityLevel.HIGH, TaskStatus.NOT_STARTED, 2),
    ("Cross-region database read replica replication lag alerts", PriorityLevel.NORMAL, TaskStatus.COMPLETED, None),
    ("API rate-limiting token bucket implementation using Redis Lua", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 1),
    (
        "JWT signature verification performance benchmark (Ed25519 vs RSA)",
        PriorityLevel.LOW,
        TaskStatus.COMPLETED,
        None,
    ),
    ("Stripe webhook idempotency replay attack stress test", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 2),
    ("Automated database backup restoration test in staging", PriorityLevel.NORMAL, TaskStatus.NOT_STARTED, 8),
    (
        "Terraform state locking contention test with concurrent applies",
        PriorityLevel.NORMAL,
        TaskStatus.COMPLETED,
        None,
    ),
    ("Figma design token synchronization webhook receiver", PriorityLevel.LOW, TaskStatus.NOT_STARTED, 14),
    ("WebAuthn FIDO2 passkey hardware token registration flow", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 3),
    ("SAML 2.0 metadata XML auto-refresh and certificate rollover", PriorityLevel.NORMAL, TaskStatus.NOT_STARTED, 7),
    ("Next.js server-side rendering caching layer with Redis", PriorityLevel.NORMAL, TaskStatus.COMPLETED, None),
    ("Client crash report deduplication pipeline with Sentry API", PriorityLevel.LOW, TaskStatus.IN_PROGRESS, 5),
    ("Zendesk ticket webhook bi-directional status sync", PriorityLevel.NORMAL, TaskStatus.NOT_STARTED, 9),
    ("Email delivery bounce rate monitoring with SendGrid webhooks", PriorityLevel.LOW, TaskStatus.COMPLETED, None),
    ("ArgoCD GitOps deployment rollout sync speed benchmarking", PriorityLevel.NORMAL, TaskStatus.IN_PROGRESS, 4),
    ("SOC2 compliance immutable audit trail export to S3 Glacier", PriorityLevel.HIGH, TaskStatus.NOT_STARTED, 11),
    ("Triton inference server GPU memory allocation under batch load", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 2),
    ("vLLM continuous batching throughput profiling with Llama 3", PriorityLevel.HIGH, TaskStatus.NOT_STARTED, 6),
    ("Core Web Vitals telemetry collector edge worker script", PriorityLevel.LOW, TaskStatus.COMPLETED, None),
    ("Dark mode color contrast ratio compliance audit (WCAG AAA)", PriorityLevel.LOW, TaskStatus.NOT_STARTED, 15),
    (
        "Accessible dropdown keyboard navigation and screen reader support",
        PriorityLevel.NORMAL,
        TaskStatus.COMPLETED,
        None,
    ),
    ("SwiftUI state management performance on 10,000 item list", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 3),
    ("Jetpack Compose recomposition profiling on low-end devices", PriorityLevel.NORMAL, TaskStatus.NOT_STARTED, 7),
    ("SQLite offline sync conflict resolution with CRDTs", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 5),
    ("Push notification delivery latency under 1 million device fanout", PriorityLevel.HIGH, TaskStatus.NOT_STARTED, 4),
    ("FaceID biometric prompt cancellation recovery handler", PriorityLevel.LOW, TaskStatus.COMPLETED, None),
    ("Elasticsearch typo tolerance and fuzzy distance scoring", PriorityLevel.NORMAL, TaskStatus.IN_PROGRESS, 3),
    ("Kafka partition key distribution re-hashing to prevent skew", PriorityLevel.HIGH, TaskStatus.NOT_STARTED, 5),
    ("ClickHouse materialized view aggregation speed optimization", PriorityLevel.NORMAL, TaskStatus.COMPLETED, None),
    ("Grafana executive dashboard alert rule deduplication", PriorityLevel.LOW, TaskStatus.IN_PROGRESS, 6),
    ("GitHub Actions matrix runner cache hit-rate optimization", PriorityLevel.NORMAL, TaskStatus.COMPLETED, None),
    ("Docker layer caching with buildx and remote registry cache", PriorityLevel.LOW, TaskStatus.NOT_STARTED, 12),
    ("Stripe tax automatic calculation error fallback handler", PriorityLevel.NORMAL, TaskStatus.IN_PROGRESS, 4),
    ("TaxJar API failure fallback and offline tax estimates", PriorityLevel.HIGH, TaskStatus.NOT_STARTED, 2),
    ("Enterprise contract invoicing PDF generation pipeline", PriorityLevel.NORMAL, TaskStatus.COMPLETED, None),
    ("Customer support escalation queue priority queueing algorithm", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 1),
    ("PagerDuty on-call shift rotation schedule automated sync", PriorityLevel.NORMAL, TaskStatus.COMPLETED, None),
    ("Zero-trust WireGuard mesh tunnel latency measurement", PriorityLevel.NORMAL, TaskStatus.NOT_STARTED, 8),
    ("HashiCorp Vault dynamic database credentials rotation", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 2),
    ("Staff impersonation session audit logging and IP restriction", PriorityLevel.HIGH, TaskStatus.NOT_STARTED, 4),
    ("Public OpenAPI spec generator from FastAPI route schemas", PriorityLevel.LOW, TaskStatus.COMPLETED, None),
    ("Python SDK code generation using Stainless or OpenAPI tools", PriorityLevel.LOW, TaskStatus.NOT_STARTED, 14),
    ("Feature flag percentage rollout deterministic hashing test", PriorityLevel.NORMAL, TaskStatus.IN_PROGRESS, 3),
    ("Feature flag kill switch response propagation time < 100ms", PriorityLevel.HIGH, TaskStatus.COMPLETED, None),
    ("Redis key eviction policy tuning (allkeys-lru vs volatile-lfu)", PriorityLevel.NORMAL, TaskStatus.NOT_STARTED, 7),
    ("WebP and AVIF image transcoding pipeline throughput test", PriorityLevel.LOW, TaskStatus.IN_PROGRESS, 5),
    ("Cloudflare CDN edge cache purge latency verification", PriorityLevel.LOW, TaskStatus.COMPLETED, None),
    ("Azure Active Directory enterprise SSO tenant mapping", PriorityLevel.NORMAL, TaskStatus.NOT_STARTED, 9),
    ("Okta SCIM 2.0 user provisioning and deprovisioning listener", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 2),
    ("HIPAA BAA compliance data encryption at rest verification", PriorityLevel.HIGH, TaskStatus.COMPLETED, None),
    ("PyTorch model quantisation (INT8/FP8) inference accuracy test", PriorityLevel.HIGH, TaskStatus.NOT_STARTED, 6),
    ("Next.js dynamic sitemap generator with 50,000 URLs", PriorityLevel.LOW, TaskStatus.IN_PROGRESS, 8),
    ("Structured JSON-LD schema markup validation across landing pages", PriorityLevel.LOW, TaskStatus.COMPLETED, None),
    ("Client-side battery consumption profiling during background sync", PriorityLevel.LOW, TaskStatus.NOT_STARTED, 13),
    ("WebSocket disconnection exponential backoff jitter test", PriorityLevel.NORMAL, TaskStatus.IN_PROGRESS, 3),
    ("PostgreSQL BRIN index evaluation on time-series log tables", PriorityLevel.NORMAL, TaskStatus.COMPLETED, None),
    ("PostgreSQL foreign data wrapper query latency benchmark", PriorityLevel.LOW, TaskStatus.NOT_STARTED, 11),
    ("FastAPI dependency injection memory overhead under load", PriorityLevel.NORMAL, TaskStatus.IN_PROGRESS, 4),
    ("Uvicorn worker count tuning for multi-core CPU instances", PriorityLevel.NORMAL, TaskStatus.COMPLETED, None),
    ("Asyncio task cancellation leak audit across Discord views", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 1),
    ("Discord slash command autocomplete cache invalidation test", PriorityLevel.NORMAL, TaskStatus.COMPLETED, None),
    ("Discord modal submission timeout handling and user alerts", PriorityLevel.NORMAL, TaskStatus.IN_PROGRESS, 2),
    ("Discord select menu 25-option pagination UI test", PriorityLevel.HIGH, TaskStatus.COMPLETED, None),
    ("Discord button row limit adherence (max 5 rows) validation", PriorityLevel.NORMAL, TaskStatus.COMPLETED, None),
    ("Task assignment DM notification batching and rate limits", PriorityLevel.NORMAL, TaskStatus.NOT_STARTED, 7),
    ("Task deadline countdown reminder scheduling accuracy test", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 2),
    ("Audit trail JSON diff generator for task field updates", PriorityLevel.NORMAL, TaskStatus.COMPLETED, None),
    ("Project archival cascading task archival transaction integrity", PriorityLevel.HIGH, TaskStatus.COMPLETED, None),
    ("Project unarchival task restoration cascading verification", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 3),
    ("Database connection retry on AWS RDS Aurora failover", PriorityLevel.HIGH, TaskStatus.NOT_STARTED, 1),
    ("Alembic migration downgrade script validation test suite", PriorityLevel.NORMAL, TaskStatus.COMPLETED, None),
    ("PostgreSQL partial index optimization for active tasks query", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 2),
    ("UUIDv4 vs UUIDv7 database index fragmentation comparison", PriorityLevel.LOW, TaskStatus.NOT_STARTED, 10),
    ("End-to-end disaster recovery runbook simulation in sandbox", PriorityLevel.HIGH, TaskStatus.NOT_STARTED, 5),
    ("Load testing TaskMenuView with 100 concurrent server users", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 1),
    ("Final production release readiness sign-off and milestone check", PriorityLevel.HIGH, TaskStatus.NOT_STARTED, 3),
]

CHANNEL_BINDINGS = [
    # (Channel Name, Channel Type, Project Prefix, Topic, [Sample Tasks])
    (
        "🧪-scale-testing",
        "forum",
        "SCALE",
        "Scale Testing Forum with 100 seeded tasks for pagination & search tests",
        SCALE_100_TASK_TEMPLATES[:5],  # Post top 5 tasks as forum cards directly
    ),
    (
        "📱-mobile-ios",
        "forum",
        "IOS",
        "Task Board for iOS Mobile Client",
        [
            ("SwiftUI Navigation Stack & Deep Links", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 2),
            ("Offline SQLite Caching Layer", PriorityLevel.NORMAL, TaskStatus.NOT_STARTED, 5),
            ("FaceID & TouchID Biometric Auth", PriorityLevel.NORMAL, TaskStatus.COMPLETED, None),
        ],
    ),
    (
        "☁️-cloud-infra",
        "forum",
        "CLD",
        "Task Board for AWS & Kubernetes Cloud Infrastructure",
        [
            ("EKS Cluster Auto-Scaler & Spot Instances", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 3),
            ("Terraform State Locking with DynamoDB", PriorityLevel.NORMAL, TaskStatus.NOT_STARTED, 7),
            ("Multi-Region S3 Bucket Replication", PriorityLevel.LOW, TaskStatus.COMPLETED, None),
        ],
    ),
    (
        "🎨-design-system",
        "forum",
        "DSG",
        "Task Board for Design System 2.0 & Component Library",
        [
            ("Dark Mode Contrast Ratio Compliance (WCAG AAA)", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 4),
            ("Figma Token Export GitHub Action", PriorityLevel.NORMAL, TaskStatus.NOT_STARTED, 10),
            ("Accessible Dropdown & Modal Dialog Components", PriorityLevel.NORMAL, TaskStatus.COMPLETED, None),
        ],
    ),
    (
        "🔒-auth-identity",
        "forum",
        "AUTH",
        "Task Board for Authentication, Security & SSO",
        [
            ("WebAuthn Passkeys Registration & Login Flow", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 1),
            ("OAuth2 Refresh Token Rotation & Revocation", PriorityLevel.HIGH, TaskStatus.NOT_STARTED, 6),
            ("Multi-Tenant SSO SAML Connector", PriorityLevel.NORMAL, TaskStatus.COMPLETED, None),
        ],
    ),
    (
        "🔍-search-discovery",
        "forum",
        "SRCH",
        "Task Board for Elasticsearch Search Engine",
        [
            ("Elasticsearch Fuzzy Matching & Typo Tolerance", PriorityLevel.NORMAL, TaskStatus.IN_PROGRESS, 4),
            ("Autocomplete Suggestions & Synonym Filtering", PriorityLevel.NORMAL, TaskStatus.NOT_STARTED, 8),
        ],
    ),
    (
        "🛠️-devops-cicd",
        "text",
        "CICD",
        "Task Channel for CI/CD Pipelines & Test Automation",
        [
            ("GitHub Actions Cache Matrix Optimization", PriorityLevel.NORMAL, TaskStatus.IN_PROGRESS, 3),
            ("Docker Layered Build & Multi-Stage Caching", PriorityLevel.LOW, TaskStatus.NOT_STARTED, 9),
        ],
    ),
    (
        "💳-payment-gateway",
        "text",
        "PAY",
        "Task Channel for Stripe Billing & Subscriptions",
        [
            ("Stripe Webhook Idempotency & Error Handling", PriorityLevel.HIGH, TaskStatus.IN_PROGRESS, 2),
            ("TaxJar Automated Sales Tax Calculation", PriorityLevel.NORMAL, TaskStatus.NOT_STARTED, 5),
        ],
    ),
    (
        "📊-analytics-bi",
        "text",
        "ANL",
        "Task Channel for Real-time Event Streaming & ClickHouse BI",
        [
            ("ClickHouse Real-time Aggregation Materialized Views", PriorityLevel.NORMAL, TaskStatus.IN_PROGRESS, 5),
            ("Grafana Executive Dashboard & Alerting", PriorityLevel.LOW, TaskStatus.NOT_STARTED, 12),
        ],
    ),
]


async def seed_scale_project_100_tasks(guild_id: int, bot_user_id: int, scale_channel_id: int | None = None) -> None:
    """Seeds 100 comprehensive tasks for the Scale Testing Platform project in PostgreSQL with working Discord links."""
    task_repo = PostgresTaskRepo(async_session_factory)
    project_repo = PostgresProjectRepo(async_session_factory)
    outbox_repo = PostgresOutboxRepo(async_session_factory)
    uow = SqlAlchemyUnitOfWork(async_session_factory)
    project_service = ProjectService(project_repo)
    outbox_service = OutboxService(outbox_repo)
    task_service = TaskService(task_repo, project_service, outbox_service, uow=uow)

    scale_project = await project_service.project_repo.get_by_prefix(guild_id, "SCALE")
    if not scale_project:
        logger.warning("Scale Testing Platform project not found. Creating it...")
        scale_project = await project_service.create_project(
            guild_id=guild_id,
            name="Scale Testing Platform",
            prefix="SCALE",
            description="Large-scale stress testing project with 100 seeded tasks",
            category="QA & Testing",
        )

    target_channel_id = scale_channel_id or scale_project.discord_channel_id

    existing_tasks, total_existing = await task_service.list_tasks(guild_id, project_id=scale_project.id, limit=200)
    existing_tasks_by_title = {t.title.lower(): t for t in existing_tasks}
    logger.info(f"Scale Testing Platform [SCALE] currently has {total_existing} tasks in DB.")

    created_count = 0
    for title, priority, status, days_offset in SCALE_100_TASK_TEMPLATES:
        task = existing_tasks_by_title.get(title.lower())
        due_at = (datetime.now(UTC) + timedelta(days=days_offset)) if days_offset is not None else None
        if not task:
            task = await task_service.create_task(
                guild_id=guild_id,
                title=title,
                creator_discord_id=bot_user_id,
                project_id=scale_project.id,
                due_at=due_at,
                priority=priority,
                body=(
                    f"Automated test workload requirements for '{title}'. "
                    "Part of the 100-task scale verification suite."
                ),
            )
            if status != TaskStatus.NOT_STARTED:
                task = await task_service.update_status(
                    task_id=task.id,
                    new_status=status,
                    expected_version=task.version,
                    actor_discord_id=bot_user_id,
                )
            created_count += 1

        # Ensure all tasks have a Discord link set
        if not task.discord_thread_id and target_channel_id:
            await task_service.update_discord_message_ids(task.id, 0, target_channel_id)

    all_tasks, total_final = await task_service.list_tasks(guild_id, project_id=scale_project.id, limit=200)
    not_started_cnt = sum(1 for t in all_tasks if t.status == TaskStatus.NOT_STARTED)
    in_prog_cnt = sum(1 for t in all_tasks if t.status == TaskStatus.IN_PROGRESS)
    completed_cnt = sum(1 for t in all_tasks if t.status == TaskStatus.COMPLETED)

    logger.info("=" * 60)
    logger.info(f"🚀 Scale Project [SCALE] Seeding Complete (Total: {total_final} Tasks):")
    logger.info(f"   • Created New Tasks : {created_count}")
    logger.info(f"   • In Progress (🟡)  : {in_prog_cnt}")
    logger.info(f"   • Not Started (⏳)  : {not_started_cnt}")
    logger.info(f"   • Completed   (✅)  : {completed_cnt}")
    logger.info("=" * 60)


async def seed_discord_channels_and_tasks(guild_id: int) -> None:
    if not settings.DISCORD_BOT_TOKEN:
        logger.warning("DISCORD_BOT_TOKEN not configured. Skipping Discord channel creation.")
        return

    client = discord.Client(intents=discord.Intents.default())
    await client.login(settings.DISCORD_BOT_TOKEN)
    bot_user_id = client.user.id if client.user else int(settings.DISCORD_CLIENT_ID)
    logger.info(f"Authenticated with Discord as Bot ID {bot_user_id}")

    try:
        guild = await client.fetch_guild(guild_id)
        channels = await guild.fetch_channels()
    except Exception as e:
        logger.error(f"Failed to fetch guild/channels for guild {guild_id}: {e}")
        await client.close()
        return

    logger.info(f"Fetched Guild: {guild.name} (Found {len(channels)} existing channels)")

    # 1. Find or create Projects Category
    category_name = "📁 DGG-PM Projects"
    category = next(
        (c for c in channels if isinstance(c, discord.CategoryChannel) and c.name.lower() == category_name.lower()),
        None,
    )
    if not category:
        try:
            category = await guild.create_category(category_name)
            logger.info(f"Created category '{category.name}' (ID: {category.id})")
        except Exception as e:
            logger.warning(f"Could not create category '{category_name}': {e}")
            category = None

    # Wire services for task creation and binding
    task_repo = PostgresTaskRepo(async_session_factory)
    project_repo = PostgresProjectRepo(async_session_factory)
    outbox_repo = PostgresOutboxRepo(async_session_factory)
    uow = SqlAlchemyUnitOfWork(async_session_factory)
    project_service = ProjectService(project_repo)
    outbox_service = OutboxService(outbox_repo)
    task_service = TaskService(task_repo, project_service, outbox_service, uow=uow)

    # 2. Iterate through channel bindings
    for chan_name, chan_type, prefix, topic, sample_tasks in CHANNEL_BINDINGS:
        project = await project_service.project_repo.get_by_prefix(guild_id, prefix)
        if not project:
            logger.warning(f"Project with prefix '{prefix}' not found in database. Skipping channel {chan_name}.")
            continue

        logger.info(f"Processing binding: #{chan_name} -> Project [{prefix}] '{project.name}'")

        # Check if channel already exists
        clean_name = (
            chan_name.replace("🧪-", "")
            .replace("📱-", "")
            .replace("☁️-", "")
            .replace("🎨-", "")
            .replace("🔒-", "")
            .replace("🔍-", "")
            .replace("🛠️-", "")
            .replace("💳-", "")
            .replace("📊-", "")
            .strip("-")
        )
        target_channel = next(
            (
                c
                for c in channels
                if c.name.lower()
                in (
                    chan_name.lower(),
                    clean_name.lower(),
                    f"🧪-{clean_name}".lower(),
                    f"📱-{clean_name}".lower(),
                    f"☁️-{clean_name}".lower(),
                    f"🎨-{clean_name}".lower(),
                    f"🔒-{clean_name}".lower(),
                    f"🔍-{clean_name}".lower(),
                    f"🛠️-{clean_name}".lower(),
                    f"💳-{clean_name}".lower(),
                    f"📊-{clean_name}".lower(),
                )
            ),
            None,
        )

        if not target_channel:
            try:
                if chan_type == "forum":
                    forum_tags = [
                        discord.ForumTag(name=d["name"], emoji=d["emoji"]) for d in STANDARD_PM_TAG_DEFINITIONS
                    ]
                    target_channel = await guild.create_forum(
                        name=chan_name,
                        category=category,
                        topic=topic,
                        available_tags=forum_tags,
                    )
                    logger.info(f"Created Forum Channel: #{target_channel.name} (ID: {target_channel.id})")
                else:
                    target_channel = await guild.create_text_channel(
                        name=chan_name,
                        category=category,
                        topic=topic,
                    )
                    logger.info(f"Created Text Channel: #{target_channel.name} (ID: {target_channel.id})")
                channels.append(target_channel)
            except Exception as e:
                logger.error(f"Failed to create channel '{chan_name}': {e}")
                continue

        # Bind project to channel in DB
        if project.discord_channel_id != target_channel.id:
            await project_service.update_project_channel(project.id, target_channel.id)
            logger.info(f"Bound [{project.prefix}] '{project.name}' to #{target_channel.name} ({target_channel.id})")

        # 3. Check existing tasks for this project
        existing_tasks, _ = await task_service.list_tasks(guild_id, project_id=project.id, limit=100)
        existing_tasks_by_title = {t.title.lower(): t for t in existing_tasks}

        # 4. Create or sync sample tasks and post to Discord
        for title, priority, status, days_offset in sample_tasks:
            due_at = (datetime.now(UTC) + timedelta(days=days_offset)) if days_offset is not None else None
            try:
                task = existing_tasks_by_title.get(title.lower())
                if not task:
                    task = await task_service.create_task(
                        guild_id=guild_id,
                        title=title,
                        creator_discord_id=bot_user_id,
                        project_id=project.id,
                        due_at=due_at,
                        priority=priority,
                    )

                if status != TaskStatus.NOT_STARTED and task.status != status:
                    task = await task_service.update_status(
                        task_id=task.id,
                        new_status=status,
                        expected_version=task.version,
                        actor_discord_id=bot_user_id,
                    )

                # If task already posted to Discord, skip posting
                if task.discord_thread_id or task.discord_message_id:
                    continue

                embed = build_task_embed(task, project_name=project.name)
                thread_view = TaskActionView(
                    task_id=task.id,
                    current_status=task.status,
                    current_priority=task.priority,
                    task_service=task_service,
                )

                thread_content = build_thread_workspace_content(task)

                if isinstance(target_channel, discord.ForumChannel):
                    applied_tags = resolve_forum_tags(target_channel, task)
                    res = await target_channel.create_thread(
                        name=f"[{task.short_id}] {task.title[:90]}",
                        content=thread_content,
                        embed=embed,
                        view=thread_view,
                        applied_tags=applied_tags,
                        auto_archive_duration=10080,
                    )
                    thread = getattr(res, "thread", res)
                    msg = getattr(res, "message", None)
                    thread_id = getattr(thread, "id", None)
                    msg_id = getattr(msg, "id", 0) if msg else 0
                    await task_service.update_discord_message_ids(task.id, msg_id, thread_id)
                elif isinstance(target_channel, discord.TextChannel):
                    msg = await target_channel.send(embed=embed)
                    thread = await msg.create_thread(
                        name=f"[{task.short_id}] {task.title[:90]}",
                        auto_archive_duration=10080,
                    )
                    await thread.send(content=thread_content, view=thread_view)
                    await task_service.update_discord_message_ids(task.id, msg.id, thread.id)

                logger.info(f"   • Posted task [{task.short_id}] '{task.title}' to #{target_channel.name}")
            except Exception as e:
                logger.error(f"Failed to create/post sample task '{title}': {e}")

        if prefix == "SCALE":
            scale_channel_id = target_channel.id

    # Seed 100 comprehensive tasks for Scale Testing Platform
    await seed_scale_project_100_tasks(guild_id, bot_user_id, scale_channel_id=scale_channel_id)

    await client.close()
    await close_db()


def check_production_safety_guard(guild_id: int) -> None:
    """Strictly prevents dev seeding and destructive reset operations in production environments."""
    env = getattr(settings, "ENVIRONMENT", "development").lower().strip()
    if env in ("production", "prod", "live"):
        raise RuntimeError(f"⛔ SAFETY BLOCK: seed_dev_data.py cannot be run in production (ENVIRONMENT='{env}').")

    # Require explicit dev guild lock
    if not settings.DISCORD_GUILD_ID:
        raise RuntimeError("⛔ SAFETY BLOCK: seed_dev_data.py requires DISCORD_GUILD_ID configured in .env.")

    if guild_id != settings.DISCORD_GUILD_ID:
        raise RuntimeError(
            f"⛔ SAFETY BLOCK: Target guild ({guild_id}) does not match "
            f"configured dev guild ({settings.DISCORD_GUILD_ID})."
        )

    # Check DATABASE_URL for dangerous production hostnames/keywords
    db_url = settings.DATABASE_URL.lower()
    dangerous_keywords = ("prod", "production", "rds.amazonaws.com", "azure.com", "supabase.com", "elephantsql.com")
    if any(keyword in db_url for keyword in dangerous_keywords):
        raise RuntimeError("⛔ SAFETY BLOCK: DATABASE_URL appears to point to a production/cloud database.")


async def clean_guild_data(guild_id: int) -> None:
    """Purges all PostgreSQL records and Discord channels/categories for the target guild."""
    check_production_safety_guard(guild_id)

    logger.info("=" * 60)
    logger.info(f"🧹 Purging testing server & database data for Guild {guild_id}...")
    logger.info("=" * 60)

    # 1. Purge PostgreSQL database
    logger.info("1. Purging PostgreSQL database records...")
    await init_db()
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(
                text("DELETE FROM task_watchers WHERE task_id IN (SELECT id FROM tasks WHERE guild_id = :gid)"),
                {"gid": guild_id},
            )
            await session.execute(
                text("DELETE FROM task_history WHERE task_id IN (SELECT id FROM tasks WHERE guild_id = :gid)"),
                {"gid": guild_id},
            )
            await session.execute(
                text("DELETE FROM tasks WHERE guild_id = :gid"),
                {"gid": guild_id},
            )
            await session.execute(
                text("DELETE FROM project_squads WHERE project_id IN (SELECT id FROM projects WHERE guild_id = :gid)"),
                {"gid": guild_id},
            )
            await session.execute(
                text("DELETE FROM projects WHERE guild_id = :gid"),
                {"gid": guild_id},
            )
            await session.execute(
                text("DELETE FROM squad_members WHERE squad_id IN (SELECT id FROM squads WHERE guild_id = :gid)"),
                {"gid": guild_id},
            )
            await session.execute(
                text("DELETE FROM squads WHERE guild_id = :gid"),
                {"gid": guild_id},
            )
            await session.execute(
                text("DELETE FROM user_preferences WHERE guild_id = :gid"),
                {"gid": guild_id},
            )
            await session.execute(
                text("DELETE FROM outbox_events"),
            )
    logger.info("   ✔ Database tables purged successfully.")

    # 2. Purge Discord channels & category
    if not settings.DISCORD_BOT_TOKEN:
        logger.warning("DISCORD_BOT_TOKEN not configured; skipping Discord channels cleanup.")
        return

    logger.info("2. Connecting to Discord to clean up channels and categories...")
    client = discord.Client(intents=discord.Intents.default())
    await client.login(settings.DISCORD_BOT_TOKEN)

    try:
        guild = await client.fetch_guild(guild_id)
        channels = await guild.fetch_channels()

        category = next(
            (c for c in channels if isinstance(c, discord.CategoryChannel) and c.name == "📁 DGG-PM Projects"), None
        )
        seeded_names = {chan_name.lower() for chan_name, *_ in CHANNEL_BINDINGS}

        deleted_count = 0
        for channel in channels:
            is_in_cat = bool(category and getattr(channel, "category_id", None) == category.id)
            is_seeded_name = any(
                channel.name.lower() == s
                or channel.name.lower().endswith(
                    s.replace("🧪-", "")
                    .replace("📱-", "")
                    .replace("☁️-", "")
                    .replace("🎨-", "")
                    .replace("🔒-", "")
                    .replace("🔍-", "")
                    .replace("🛠️-", "")
                    .replace("💳-", "")
                    .replace("📊-", "")
                )
                for s in seeded_names
            )
            if (is_in_cat or is_seeded_name) and not isinstance(channel, discord.CategoryChannel):
                try:
                    logger.info(f"   • Deleting channel: #{channel.name} ({channel.id})")
                    await channel.delete(reason="dgg-pm development environment reset")
                    deleted_count += 1
                except Exception as e:
                    logger.error(f"   ❌ Failed to delete channel #{channel.name}: {e}")

        if category:
            try:
                logger.info(f"   • Deleting Category: {category.name} ({category.id})")
                await category.delete(reason="dgg-pm development environment reset")
            except Exception as e:
                logger.error(f"   ❌ Failed to delete category {category.name}: {e}")

        logger.info(f"   ✔ Deleted {deleted_count} Discord channels and the project category.")
    finally:
        await client.close()
        await close_db()


async def seed_data(guild_id: int, reset: bool = False) -> None:
    check_production_safety_guard(guild_id)

    if reset:
        await clean_guild_data(guild_id)

    logger.info("Initializing database schema if not present...")
    await init_db()

    project_repo = PostgresProjectRepo(async_session_factory)
    project_service = ProjectService(project_repo)

    existing_projects = await project_service.list_projects(guild_id, include_archived=True)
    existing_names = {p.name.lower() for p in existing_projects}
    existing_prefixes = {p.prefix.upper() for p in existing_projects}

    logger.info(f"Target Discord Guild ID: {guild_id}")
    logger.info(f"Found {len(existing_projects)} existing projects in guild.")

    active_created = 0
    for name, prefix, desc, cat in ACTIVE_PROJECT_SEEDS:
        if name.lower() in existing_names or prefix.upper() in existing_prefixes:
            continue
        await project_service.create_project(
            guild_id=guild_id,
            name=name,
            prefix=prefix,
            description=desc,
            category=cat,
        )
        existing_names.add(name.lower())
        existing_prefixes.add(prefix.upper())
        active_created += 1

    archived_created = 0
    for name, prefix, desc, cat in ARCHIVED_PROJECT_SEEDS:
        if name.lower() in existing_names or prefix.upper() in existing_prefixes:
            continue
        p = await project_service.create_project(
            guild_id=guild_id,
            name=name,
            prefix=prefix,
            description=desc,
            category=cat,
        )
        await project_service.archive_project(p.id)
        existing_names.add(name.lower())
        existing_prefixes.add(prefix.upper())
        archived_created += 1

    all_current = await project_service.list_projects(guild_id, include_archived=True)
    active_total = sum(1 for p in all_current if not p.is_archived)
    archived_total = sum(1 for p in all_current if p.is_archived)

    logger.info("=" * 60)
    logger.info(f"✅ Seeding Complete for Guild {guild_id}:")
    logger.info(f"   • Active Projects   : {active_total} (created {active_created} new)")
    logger.info(f"   • Archived Projects : {archived_total} (created {archived_created} new)")
    logger.info(f"   • Total in DB       : {len(all_current)}")
    logger.info("=" * 60)

    # Now seed Discord Channels, Forum Cards, and Bound Tasks
    await seed_discord_channels_and_tasks(guild_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed realistic dev projects, Discord channels, and tasks for dgg-pm.")
    default_guild = settings.DISCORD_GUILD_ID or 1543430283250901023
    parser.add_argument(
        "--guild-id",
        type=int,
        default=default_guild,
        help=f"Target Discord Guild ID (default: {default_guild})",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Purge all existing database records and Discord project channels before reseeding from scratch.",
    )
    parser.add_argument(
        "--clean-only",
        action="store_true",
        help="Purge all existing database records and Discord project channels without reseeding.",
    )
    args = parser.parse_args()

    if args.clean_only:
        asyncio.run(clean_guild_data(args.guild_id))
    else:
        asyncio.run(seed_data(args.guild_id, reset=args.reset))


if __name__ == "__main__":
    main()
