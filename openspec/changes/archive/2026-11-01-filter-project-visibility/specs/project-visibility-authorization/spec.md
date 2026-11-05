# Spec Delta

## Purpose

Enforces role-based visibility scoping for active projects across interactive UI rosters, slash commands, and autocompletion to prevent private projects from leaking to unauthorized members.

## ADDED Requirements

### Requirement: Filter active projects in roster view
The system SHALL filter active projects displayed in the Project Active List roster view so that members only see projects they are authorized to view based on squad roles, project lead assignment, or server manager privileges.

#### Scenario: Squad member opens active projects roster
- **GIVEN** a project is restricted to a squad Discord role
- **WHEN** a member holding that squad role clicks "Active Projects" in the Project Management Hub
- **THEN** the restricted project is visible in the active projects roster

#### Scenario: Outsider opens active projects roster
- **GIVEN** a project is restricted to a squad Discord role
- **WHEN** a member who does not hold the squad role and is not the project lead clicks "Active Projects"
- **THEN** the restricted project is omitted from the active projects roster

### Requirement: Zero accessible projects feedback
When a member has access to zero active projects in the server, the system SHALL display an informative ephemeral notice explaining that they do not have permission to view any active projects.

#### Scenario: Member with no viewable projects clicks Active Projects
- **GIVEN** there are active projects in the server but the member is unauthorized for all of them
- **WHEN** the member clicks "Active Projects" in the Project Management Hub
- **THEN** the system sends an ephemeral message explaining they lack permission to view any active projects

### Requirement: Filter active projects in slash command list
The system SHALL filter the projects returned by the `/pm project list` slash command so that only projects the caller is authorized to view are included in the embed.

#### Scenario: Running project list with mixed visibility
- **GIVEN** multiple active projects exist, with some public and some restricted to other squads
- **WHEN** a member executes `/pm project list`
- **THEN** the response embed only includes public projects and projects matching the caller's squad or lead status

#### Scenario: Running project list with zero accessible projects
- **GIVEN** all active projects in the server are restricted to squads the caller does not belong to
- **WHEN** the caller executes `/pm project list`
- **THEN** the command responds with an informative notice indicating no accessible projects were found

### Requirement: Filter project autocomplete suggestions
The system SHALL filter autocomplete suggestions for project name parameters across all slash commands so that private projects are never suggested to unauthorized members.

#### Scenario: Autocomplete invoked by squad member
- **GIVEN** a private project exists for Squad A
- **WHEN** a member of Squad A types in the `project_name` parameter of `/pm task list`
- **THEN** the private project is included in the suggested autocomplete choices

#### Scenario: Autocomplete invoked by non-member
- **GIVEN** a private project exists for Squad A
- **WHEN** a non-member types in the `project_name` parameter of `/pm task list`
- **THEN** the private project is excluded from the suggested autocomplete choices

### Requirement: Server manager visibility bypass
The system SHALL allow Discord server managers with administrator or manage server permissions to view and autocomplete all active projects regardless of squad assignment.

#### Scenario: Server manager views active projects
- **GIVEN** private projects exist across multiple different squads
- **WHEN** a server manager opens the active projects roster or runs `/pm project list`
- **THEN** all active projects across all squads are displayed
