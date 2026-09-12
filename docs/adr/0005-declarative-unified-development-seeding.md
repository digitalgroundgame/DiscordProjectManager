# Declarative Unified Development Seeding

## Context and Decision
Development and integration testing previously relied on multiple disparate procedural scripts (`seed_dev_data.py`, `seed_forums.py`, `seed_tech_tree_forum.py`) containing hardcoded Python tuples and dictionaries. These scripts duplicated project and squad definitions, interwove direct database manipulation with Discord REST calls arbitrarily, and lacked schema validation or scenario composability. We decided to unify all development environment setup into a declarative configuration architecture powered by typed YAML Seed Manifests validated with Pydantic schemas, structured with layered profile overlays (`base/` + `profiles/`), and executed via a single unified runner (`scripts/seed.py` / `make db-reset [PROFILE=...]`). Every seed execution is destructive-by-default to return the development server to a pristine baseline state, safely isolated to a dedicated Discord Category (`📁 DGG-PM Projects`) and the target development guild database tables.

## Considered Options
- **Hardcoded procedural scripts (Rejected)**: Brittle to schema changes, scattered across three separate entrypoints, and impossible to customize or compose for different testing scenarios without modifying Python script internals.
- **Decoupled database-only seeding with post-startup bot sync (Rejected)**: Allowed database state and Discord server fixtures (channels, forum tags, Control Hubs, thread action cards) to drift out of sync during development.
- **Declarative YAML with Pydantic validation & category-isolated destructive reset (Chosen)**: Guarantees strict schema validation prior to execution, models both database entities and Discord workspace structures in a unified manifest, and ensures a repeatable, clean baseline without risking unrelated channels on developer Discord servers.

## Consequences
- All development and testing seed configurations reside under a version-controlled `seeds/` directory as typed YAML manifests.
- Specialized workloads (e.g., Tech Tree DAGs, 100-task scale testing) are maintained as composable Seed Profiles overlaid atop the base topology.
- Discord roles and squads are declared by logical names with automatic guild role provisioning and `@bot`/`DEV_USER_ID` identity resolution.
- Legacy scripts (`scripts/seed_dev_data.py`, `scripts/seed_forums.py`, `scripts/seed_tech_tree_forum.py`) are retired in favor of the unified runner and `make db-reset [PROFILE=...]`.
