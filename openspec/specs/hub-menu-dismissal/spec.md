# hub-menu-dismissal Specification

## Purpose

Provides safe, 1-click dismissal controls for ephemeral menus spawned from the Forum Control Hub while guaranteeing the public Control Hub message is never modified or deleted.

## Requirements

### Requirement: TaskMenuView Dismissal Control
The system SHALL provide an explicit Close action button on `TaskMenuView` allowing users to immediately dismiss and delete the ephemeral task management menu.

#### Scenario: User closes My Tasks menu
- **WHEN** user clicks the "Close" button on an active `TaskMenuView`
- **THEN** the system deletes the ephemeral message from the client view and stops the view session

### Requirement: ProjectMenuView Dismissal Control
The system SHALL provide an explicit Close action button on `ProjectMenuView` allowing users to immediately dismiss and delete the ephemeral projects hub menu.

#### Scenario: User closes Projects Hub menu
- **WHEN** user clicks the "Close" button on an active `ProjectMenuView`
- **THEN** the system deletes the ephemeral message from the client view and stops the view session

### Requirement: TechTreeViewer Dismissal Control
The system SHALL provide an explicit Close action button on `TechTreeViewer` allowing users to immediately dismiss and delete the ephemeral visual graph visualization.

#### Scenario: User closes Visual Graph menu
- **WHEN** user clicks the "Close" button on an active `TechTreeViewer`
- **THEN** the system deletes the ephemeral graph message from the client view and stops the view session

### Requirement: Public Message Isolation Invariant
The system SHALL guarantee that closing or timing out an ephemeral view spawned from the Forum Control Hub never triggers deletion or modification of the public Control Hub message.

#### Scenario: Closing ephemeral view preserves public Control Hub
- **WHEN** a user closes an ephemeral menu spawned from `PmHubView`
- **THEN** only the private ephemeral message is deleted and the public pinned Control Hub post remains untouched

### Requirement: Automated Public Interaction Registration Rejection
The system SHALL detect and reject any attempt to register an interaction originating from a public message into `MenuSessionManager.register_menu()`.

#### Scenario: Attempted registration of public interaction is rejected
- **WHEN** code invokes `MenuSessionManager.register_menu()` with an interaction whose `message` is not ephemeral
- **THEN** the system logs an error, refuses registration, and leaves the session manager state unmodified

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

