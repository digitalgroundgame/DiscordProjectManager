"""Protocols and value types for Discord Workspaces and Thread Lifecycle management.

This module re-exports workspace ports from src.ports.workspace for backwards compatibility
under the Strangler Fig migration pattern.
"""

from __future__ import annotations

from src.ports.workspace import (
    _UNSET,
    ProjectProvisionSpec,
    ProjectWorkspacePort,
    ProjectWorkspaceRef,
    RebuildProgress,
    RebuildWorkspaceResult,
    SyncWorkspaceResult,
    TaskControlPanel,
    TaskWorkspacePort,
    TaskWorkspaceRef,
)

# Backwards compatibility aliases
ITaskDiscordWorkspace = TaskWorkspacePort
IProjectDiscordWorkspace = ProjectWorkspacePort

__all__ = [
    "_UNSET",
    "IProjectDiscordWorkspace",
    "ITaskDiscordWorkspace",
    "ProjectProvisionSpec",
    "ProjectWorkspacePort",
    "ProjectWorkspaceRef",
    "RebuildProgress",
    "RebuildWorkspaceResult",
    "SyncWorkspaceResult",
    "TaskControlPanel",
    "TaskWorkspacePort",
    "TaskWorkspaceRef",
]
