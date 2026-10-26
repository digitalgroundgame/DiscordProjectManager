"""Backwards-compatible re-exports for project views."""

from src.adapters.discord_bot.views.project.archive import (
    ProjectArchiveConfirmView,
    ProjectArchiveSelectView,
    ProjectRestoreConfirmView,
    ProjectRestoreSelectView,
    build_archive_select_embed,
    build_restore_select_embed,
)
from src.adapters.discord_bot.views.project.dashboard import (
    ProjectMenuView,
    build_project_menu_embed,
)
from src.adapters.discord_bot.views.project.governance import (
    ProjectAssignSquadView,
    ProjectAssignTimelineModal,
    ProjectLeadSelectView,
    ProjectRoleSelectView,
)
from src.adapters.discord_bot.views.project.rebuild import (
    ProjectRebuildSelectView,
    RebuildConfirmView,
)
from src.adapters.discord_bot.views.project.roster import (
    ProjectActiveListView,
    ProjectSearchModal,
    build_active_projects_embed,
)
from src.adapters.discord_bot.views.project.wizard import (
    ProjectChannelSelectView,
    ProjectCreateDraftView,
    ProjectCreateModal,
    build_project_draft_embed,
)

__all__ = [
    "ProjectActiveListView",
    "ProjectArchiveConfirmView",
    "ProjectArchiveSelectView",
    "ProjectAssignSquadView",
    "ProjectAssignTimelineModal",
    "ProjectChannelSelectView",
    "ProjectCreateDraftView",
    "ProjectCreateModal",
    "ProjectLeadSelectView",
    "ProjectMenuView",
    "ProjectRebuildSelectView",
    "ProjectRestoreConfirmView",
    "ProjectRestoreSelectView",
    "ProjectRoleSelectView",
    "ProjectSearchModal",
    "RebuildConfirmView",
    "build_active_projects_embed",
    "build_archive_select_embed",
    "build_project_draft_embed",
    "build_project_menu_embed",
    "build_restore_select_embed",
]
