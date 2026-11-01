# Spec Delta

## ADDED Requirements

### Requirement: Filter visible projects and enforce project access on web portal endpoints
The system SHALL enforce role-based project visibility across all web portal API endpoints (`/api/projects`, `/api/projects/{project_id}/workspace`, and `/api/projects/{project_id}/tech-tree`) and SHALL authorize designated Guild Lead users and Guild Lead roles to view all active projects alongside Server Managers.

#### Scenario: Squad member requests restricted project on web portal
- **GIVEN** a project is restricted to a Squad Discord role
- **WHEN** a web portal user holding that Squad role requests `/api/projects/{project_id}/workspace` or `/api/projects/{project_id}/tech-tree`
- **THEN** the system returns the project workspace and Tech Tree data (`200 OK`)

#### Scenario: Unauthorized member requests restricted project on web portal
- **GIVEN** a project is restricted to a Squad Discord role
- **WHEN** a web portal user who does not hold the Squad role, is not the Project Lead, and is not a Server Manager or Guild Lead requests `/api/projects/{project_id}/workspace` or `/api/projects/{project_id}/tech-tree`
- **THEN** the system denies access with `403 Forbidden`

#### Scenario: Guild Lead views restricted projects
- **GIVEN** private projects exist across multiple squads
- **WHEN** a user designated as a Guild Lead user or holding a configured Guild Lead role views the project roster or requests a project workspace
- **THEN** the system authorizes access to view all projects in the server
