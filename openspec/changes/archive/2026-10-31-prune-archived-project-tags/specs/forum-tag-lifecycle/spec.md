# Spec Delta: forum-tag-lifecycle

## Purpose

Governs the allocation, pruning, and restoration of Discord ForumChannel tags for projects sharing a forum channel, adhering to Discord's 20-tag limit.

## ADDED Requirements

### Requirement: Prune project tag on project archival
The system SHALL remove the corresponding per-project forum tag from the forum channel's available tags whenever a project is archived.

#### Scenario: Project archived in shared forum channel
- **WHEN** a project is archived and is bound to a Discord ForumChannel
- **THEN** the bot deletes the project's forum tag (`📁 <Project Name>`) from the forum channel's available tags list
- **AND** the forum channel tag count is decreased by 1

#### Scenario: Bot lacks manage channels permission during archival
- **WHEN** a project is archived but the bot lacks permission to edit the forum channel tags
- **THEN** the project archival in the database proceeds without raising an unhandled exception
- **AND** a warning is logged or reported

### Requirement: Prune legacy archived project tags during hub reconciliation
The system SHALL remove forum tags belonging to known archived projects when refreshing or mounting the pinned Control Hub post.

#### Scenario: Forum channel contains tags of previously archived projects
- **WHEN** the pinned control hub is refreshed or verified on a forum channel
- **THEN** any forum tag matching the name of a known archived project in that channel is removed from the forum's available tags list
- **AND** the reclaimed slots become available for active projects

### Requirement: Restore project tag on project unarchive
The system SHALL attempt to re-provision the per-project forum tag when an archived project is restored, subject to available tag capacity.

#### Scenario: Project restored with available tag capacity
- **WHEN** an archived project is restored and the forum channel has fewer than 20 tags
- **THEN** the project forum tag is re-added to the forum channel's available tags list
- **AND** unarchived active task threads are re-tagged with the project tag

#### Scenario: Project restored when forum tag limit is reached
- **WHEN** an archived project is restored and the forum channel already has 20 tags
- **THEN** the project restoration in the database succeeds
- **AND** a non-blocking warning is returned indicating the tag could not be added due to the 20-tag limit
