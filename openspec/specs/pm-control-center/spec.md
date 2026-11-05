# pm-control-center Specification

## Purpose
Governs the presentation, action layout, and navigation controls of the Project Management Control Center (`/pm menu`).

## Requirements

### Requirement: Control Center Action Layout
The system SHALL present authorized navigation and management actions on the `/pm menu` Control Center without crashing unpaginated overview buttons.

#### Scenario: Contributor views Control Center
- **GIVEN** a project contributor without server management permissions executes `/pm menu`
- **WHEN** the Control Center dashboard renders
- **THEN** the actions include "Projects", "My Settings", and "Guides"
- **AND** the crashing "Server Overview" button is not present

#### Scenario: Server manager views Control Center
- **GIVEN** a member with server manager privileges executes `/pm menu`
- **WHEN** the Control Center dashboard renders
- **THEN** the actions include "Create Project", "Projects", "My Settings", and "Guides" (and "Team Leads" for administrators)
- **AND** the crashing "Server Overview" button is not present

### Requirement: Streamlined Project Navigation
The system SHALL direct all project status and channel exploration through the paginated, permission-scoped Projects roster view rather than raw guild-wide overview text dumps.

#### Scenario: User navigates to active projects from Control Center
- **GIVEN** a user is viewing the `/pm menu` Control Center
- **WHEN** the user clicks "Projects"
- **THEN** the system navigates to the Project Operations menu where "Active Projects" provides a paginated, search-filtered roster scoped by visibility permissions
