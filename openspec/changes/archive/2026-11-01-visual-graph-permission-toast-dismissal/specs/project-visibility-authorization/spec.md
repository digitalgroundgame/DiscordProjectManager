# Spec Delta

## ADDED Requirements

### Requirement: Ephemeral toast auto-dismissal for visual graph and hub permission feedback
The system SHALL schedule auto-dismissal after standard toast duration for ephemeral permission-denied notices triggered when attempting to view restricted visual graphs or execute unauthorized project actions.

#### Scenario: Visual graph permission denial auto-dismissal on hub
- **GIVEN** a member is unauthorized to view the visual graph for a project
- **WHEN** the member clicks "Visual Graph" on the Forum Control Hub
- **THEN** the system sends the permission denied message ephemerally
- **AND** the system schedules auto-dismissal of the ephemeral message after standard toast duration

#### Scenario: Visual graph zero accessible projects auto-dismissal
- **GIVEN** a member has access to zero project visual graphs
- **WHEN** the member clicks "Visual Graph" in the Project Management Hub or on the Forum Control Hub
- **THEN** the system sends the notice ephemerally
- **AND** the system schedules auto-dismissal of the ephemeral message after standard toast duration

#### Scenario: Visual graph viewer project switch permission denial
- **GIVEN** a member is viewing the interactive visual graph viewer
- **WHEN** the member selects a restricted project in the project dropdown
- **THEN** the system sends the permission denied notice ephemerally
- **AND** the system schedules auto-dismissal of the notice after standard toast duration
