# Spec Delta

## ADDED Requirements

### Requirement: CalendarFeedView Dismissal Control
The system SHALL provide an explicit Close action button on `CalendarFeedView` allowing users to immediately dismiss and delete the ephemeral calendar feed menu.

#### Scenario: User closes Calendar Feed menu
- **WHEN** user clicks the "Close" button on an active `CalendarFeedView`
- **THEN** the system deletes the ephemeral message from the client view, stops the view session, and unregisters the session from `MenuSessionManager`

### Requirement: Calendar Feed Single-Menu Session Registration
The system SHALL register the ephemeral interaction originating from `/pm calendar` in `MenuSessionManager` to ensure active user sessions remain clean and single-menu.

#### Scenario: Opening calendar feed dismisses previous ephemeral menu
- **WHEN** a user opens `/pm calendar` while another ephemeral menu session is active
- **THEN** the system registers the calendar feed interaction and automatically dismisses the previously opened menu

#### Scenario: Regenerating token refreshes session interaction context
- **WHEN** a user clicks "Regenerate Link" on an active `CalendarFeedView`
- **THEN** the view updates its interaction reference to the fresh interaction context so subsequent timeout dismissals succeed
