# Spec Delta

## MODIFIED Requirements

### Requirement: Ephemeral toast auto-dismissal for visual graph and hub permission feedback
The system SHALL format permission-denied notices triggered when attempting to view restricted visual graphs or execute unauthorized project actions as standardized ephemeral error toast embeds (`ToastType.ERROR`) with blockquote formatting for secondary requirement explanations, stripped leading status emojis, and dynamic relative auto-dismiss countdown timers (`*Auto-dismisses <t:{timestamp}:R>*`).

#### Scenario: Visual graph permission denial auto-dismissal on hub
- **GIVEN** a member is unauthorized to view the visual graph for a project
- **WHEN** the member clicks "Visual Graph" on the Forum Control Hub
- **THEN** the system responds with an ephemeral error toast embed (`ToastType.ERROR`)
- **AND** the embed contains the target project name with secondary role requirements formatted as a blockquote
- **AND** the embed description includes an auto-dismiss relative countdown timer scheduled for standard toast duration

#### Scenario: Visual graph zero accessible projects auto-dismissal
- **GIVEN** a member has access to zero project visual graphs
- **WHEN** the member clicks "Visual Graph" in the Project Management Hub or on the Forum Control Hub
- **THEN** the system responds with an ephemeral error toast embed (`ToastType.ERROR`)
- **AND** the notice formats secondary requirement guidance as a blockquote
- **AND** the embed description includes an auto-dismiss relative countdown timer scheduled for standard toast duration

#### Scenario: Visual graph viewer project switch permission denial
- **GIVEN** a member is viewing the interactive visual graph viewer
- **WHEN** the member selects a restricted project in the project dropdown
- **THEN** the system responds with an ephemeral error toast embed (`ToastType.ERROR`)
- **AND** the embed description includes an auto-dismiss relative countdown timer scheduled for standard toast duration
