"""Centralized UI Navigation Router and View Factory for Discord views.

Breaks circular import chains across views by isolating view construction
and transitions behind a single factory protocol.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import discord

if TYPE_CHECKING:
    from src.domain.enums import TaskStatus
    from src.domain.models import Project, Squad, Task
    from src.services.auth_service import AuthService
    from src.services.calendar_service import CalendarService
    from src.services.project_service import ProjectService
    from src.services.squad_service import SquadService
    from src.services.task_service import TaskService
    from src.services.user_service import UserService


class DiscordViewRouter:
    """Central view factory and screen transition router for Discord UI views."""

    @staticmethod
    def build_hub_view(
        project_service: ProjectService,
        squad_service: SquadService,
        task_service: TaskService,
        user_service: UserService | None = None,
        auth_service: AuthService | None = None,
        channel_name: str | None = None,
        bound_projects: list[Project] | None = None,
        project_squads_map: dict[UUID, list[Squad]] | None = None,
    ) -> tuple[discord.Embed, discord.ui.View]:
        """Constructs the root Control Hub view and welcome embed."""
        from src.adapters.discord_bot.views.hub_menu import PmHubView, build_hub_welcome_embed

        view = PmHubView(
            project_service=project_service,
            squad_service=squad_service,
            task_service=task_service,
            user_service=user_service,
            auth_service=auth_service,
        )
        embed = build_hub_welcome_embed(
            channel_name=channel_name,
            bound_projects=bound_projects,
            project_squads_map=project_squads_map,
        )
        return embed, view

    @staticmethod
    def build_hub_embed(
        channel_name: str | None = None,
        bound_projects: list[Project] | None = None,
        project_squads_map: dict[UUID, list[Squad]] | None = None,
    ) -> discord.Embed:
        """Constructs the Control Hub welcome embed."""
        from src.adapters.discord_bot.views.hub_menu import build_hub_welcome_embed

        return build_hub_welcome_embed(
            channel_name=channel_name,
            bound_projects=bound_projects,
            project_squads_map=project_squads_map,
        )

    @staticmethod
    def build_admin_dashboard(
        guild: discord.Guild,
        user: discord.User | discord.Member,
        project_service: ProjectService,
        squad_service: SquadService,
        task_service: TaskService,
        user_service: UserService | None = None,
        auth_service: AuthService | None = None,
        initial_interaction: discord.Interaction | None = None,
        active_projects: list[Project] | None = None,
        active_tasks_count: int = 0,
        current_pref: Any = None,
        is_server_manager: bool | None = None,
        is_server_admin: bool | None = None,
    ) -> tuple[discord.Embed, discord.ui.View]:
        """Constructs the Server Manager Admin Dashboard view and embed."""
        from src.adapters.discord_bot.views.admin_menu import PmDashboardView, build_pm_dashboard_embed

        view = PmDashboardView(
            project_service=project_service,
            squad_service=squad_service,
            task_service=task_service,
            user_service=user_service,
            auth_service=auth_service,
            initial_interaction=initial_interaction,
            is_server_manager=is_server_manager,
            is_server_admin=is_server_admin,
        )
        if current_pref is None:
            from src.domain.enums import NotificationPreference

            current_pref = NotificationPreference.DM

        effective_manager = is_server_manager if is_server_manager is not None else view.is_server_manager
        effective_admin = is_server_admin if is_server_admin is not None else getattr(view, "is_server_admin", False)

        embed = build_pm_dashboard_embed(
            guild=guild,
            user=user,
            active_projects=active_projects or [],
            active_tasks_count=active_tasks_count,
            current_pref=current_pref,
            is_server_manager=effective_manager,
            is_server_admin=effective_admin,
        )
        return embed, view

    @staticmethod
    def build_project_menu(
        project_service: ProjectService,
        squad_service: SquadService | None = None,
        task_service: TaskService | None = None,
        user_service: UserService | None = None,
        auth_service: AuthService | None = None,
        initial_interaction: discord.Interaction | None = None,
        user: discord.Member | discord.User | None = None,
        return_to: str = "dashboard",
        is_server_manager: bool | None = None,
    ) -> tuple[discord.Embed, discord.ui.View]:
        """Constructs the Project Operations control center view and embed."""
        from src.adapters.discord_bot.views.project_menu import ProjectMenuView, build_project_menu_embed

        view = ProjectMenuView(
            project_service=project_service,
            squad_service=squad_service,
            task_service=task_service,
            initial_interaction=initial_interaction,
            user=user,
            is_server_manager=is_server_manager,
            user_service=user_service,
            return_to=return_to,
            auth_service=auth_service,
        )
        embed = build_project_menu_embed(view.is_server_manager)
        return embed, view

    @staticmethod
    def build_project_channel_select(
        project_service: ProjectService,
        squad_service: SquadService,
        task_service: TaskService,
        user_service: UserService | None = None,
        initial_interaction: discord.Interaction | None = None,
        return_to: str = "hub",
    ) -> tuple[discord.Embed, discord.ui.View]:
        """Constructs the forum channel selector view for project creation."""
        from src.adapters.discord_bot.views.project_menu import ProjectChannelSelectView

        view = ProjectChannelSelectView(
            project_service,
            squad_service,
            task_service,
            initial_interaction=initial_interaction,
            user_service=user_service,
            return_to=return_to,
        )
        embed = discord.Embed(
            title="Create Project: Select Forum Channel",
            description=(
                "Choose a **Forum Channel** to bind as the project's task board.\n\n"
                "Tasks become organized forum post cards with native Discord tag filtering "
                "and an interactive pinned Control Hub."
            ),
            color=discord.Color.blue(),
        )
        return embed, view

    @staticmethod
    def build_squad_menu(
        squad_service: SquadService,
        project_service: ProjectService | None = None,
        task_service: TaskService | None = None,
        user_service: UserService | None = None,
        initial_interaction: discord.Interaction | None = None,
        user: discord.Member | discord.User | None = None,
        can_create_squads: bool | None = None,
        can_assign_members: bool | None = None,
        return_to: str = "dashboard",
    ) -> tuple[discord.Embed, discord.ui.View]:
        """Constructs the Squad Management control center view and embed."""
        from src.adapters.discord_bot.views.squad_menu import SquadMenuView, build_squad_menu_embed

        view = SquadMenuView(
            squad_service=squad_service,
            project_service=project_service,
            task_service=task_service,
            initial_interaction=initial_interaction,
            user=user,
            can_create_squads=can_create_squads,
            can_assign_members=can_assign_members,
            user_service=user_service,
            return_to=return_to,
        )
        embed = build_squad_menu_embed(
            can_create_squads=view.can_create_squads,
            can_assign_members=view.can_assign_members,
        )
        return embed, view

    @staticmethod
    def build_user_settings_view(
        user: discord.User | discord.Member,
        current_pref: Any,
        user_service: UserService,
        project_service: ProjectService | None = None,
        squad_service: SquadService | None = None,
        task_service: TaskService | None = None,
        calendar_service: CalendarService | None = None,
        initial_interaction: discord.Interaction | None = None,
        return_to: str = "hub",
    ) -> tuple[discord.Embed, discord.ui.View]:
        """Constructs the user notification preferences view and embed."""
        from src.adapters.discord_bot.views.settings_menu import UserSettingsView, build_settings_embed

        view = UserSettingsView(
            user_service=user_service,
            current_pref=current_pref,
            project_service=project_service,
            squad_service=squad_service,
            task_service=task_service,
            calendar_service=calendar_service,
            initial_interaction=initial_interaction,
            return_to=return_to,
        )
        embed = build_settings_embed(user, current_pref)
        return embed, view

    @staticmethod
    def build_tech_tree_viewer(
        task_service: TaskService,
        target_project: Project,
        current_orientation: str = "lr",
        auth_service: AuthService | None = None,
    ) -> discord.ui.View:
        """Constructs the interactive Visual Graph viewer."""
        from src.adapters.discord_bot.views.tree_view import TechTreeViewer

        return TechTreeViewer(
            task_service,
            target_project,
            current_orientation=current_orientation,
            auth_service=auth_service,
        )

    @staticmethod
    def build_tech_tree_project_select(
        task_service: TaskService,
        project_service: ProjectService,
        projects: list[Project],
        orientation: str = "lr",
        auth_service: AuthService | None = None,
    ) -> discord.ui.View:
        """Constructs the project picker for Visual Graph viewing."""
        from src.adapters.discord_bot.views.tree_view import TechTreeProjectSelectView

        return TechTreeProjectSelectView(
            task_service,
            project_service,
            projects,
            orientation=orientation,
            auth_service=auth_service,
        )

    @staticmethod
    def build_task_menu(
        task_service: TaskService,
        project_service: ProjectService,
        squad_service: SquadService | None = None,
        projects: list[Project] | None = None,
        current_channel_id: int | None = None,
        parent_channel_id: int | None = None,
        initial_project_id: UUID | Any | None = None,
        initial_assignee_id: int | None = None,
        search_query: str = "",
        auth_service: AuthService | None = None,
        initial_interaction: discord.Interaction | None = None,
        can_create_standalone: bool | None = None,
        tasks: list[Task] | None = None,
        total_count: int = 0,
        project_label: str = "All Projects (Global Scope)",
        status_label: str = "All Statuses",
        priority_label: str = "All Priorities",
        assignee_label: str = "All Assignees",
        tag_label: str = "All Tags",
        page: int = 1,
        page_size: int = 15,
    ) -> tuple[discord.Embed, discord.ui.View]:
        """Constructs the Task Board menu view and embed."""
        from src.adapters.discord_bot.views.task_menu import TaskMenuView, build_task_board_embed

        view = TaskMenuView(
            task_service=task_service,
            project_service=project_service,
            squad_service=squad_service,
            projects=projects,
            current_channel_id=current_channel_id,
            parent_channel_id=parent_channel_id,
            initial_project_id=initial_project_id,
            initial_assignee_id=initial_assignee_id,
            search_query=search_query,
            auth_service=auth_service,
            initial_interaction=initial_interaction,
            can_create_standalone=can_create_standalone,
        )
        embed = build_task_board_embed(
            tasks=tasks if tasks is not None else [],
            total_count=total_count,
            project_label=project_label,
            status_label=status_label,
            assignee_label=assignee_label,
        )
        return embed, view

    @staticmethod
    async def build_calendar_feed_view(
        calendar_service: CalendarService,
        user: discord.User | discord.Member,
        initial_interaction: discord.Interaction | None = None,
    ) -> tuple[discord.Embed, discord.ui.View]:
        """Constructs the user calendar subscription view and embed."""
        from src.adapters.discord_bot.views.calendar_feed_view import CalendarFeedView, build_calendar_feed_embed

        token = await calendar_service.get_or_create_token(user.id)
        urls = calendar_service.build_feed_urls(token)
        embed = build_calendar_feed_embed(user, urls)
        view = CalendarFeedView(
            calendar_service=calendar_service,
            user_discord_id=user.id,
            token=token,
            initial_interaction=initial_interaction,
        )
        return embed, view

    @staticmethod
    def build_task_quick_edit_title_modal(view: Any) -> discord.ui.Modal:
        """Constructs the quick edit title modal."""
        from src.adapters.discord_bot.views.task_modals import TaskQuickEditTitleModal

        return TaskQuickEditTitleModal(view)

    @staticmethod
    def build_task_custom_due_modal(view: Any) -> discord.ui.Modal:
        """Constructs the custom due date entry modal."""
        from src.adapters.discord_bot.views.task_builder import TaskCustomDueModal

        return TaskCustomDueModal(view)

    @staticmethod
    def build_task_note_modal(
        task_id: UUID,
        short_id: str,
        task_service: TaskService,
        auth_service: AuthService | None = None,
        **kwargs: Any,
    ) -> discord.ui.Modal:
        """Constructs the task note entry modal."""
        from src.adapters.discord_bot.views.task_modals import TaskNoteModal

        return TaskNoteModal(
            task_id=task_id,
            short_id=short_id,
            task_service=task_service,
            auth_service=auth_service,
            **kwargs,
        )

    @staticmethod
    def build_task_blocked_confirm_view(
        task: Task,
        target_status: TaskStatus,
        incomplete_prereqs: list[Task],
        author_id: int,
        task_service: TaskService,
        auth_service: AuthService | None = None,
        workspace: Any = None,
        bot: discord.Client | None = None,
        notes: str | None = None,
        initial_interaction: discord.Interaction | None = None,
    ) -> tuple[discord.Embed, discord.ui.View]:
        """Constructs the task dependency blocker confirmation card and view."""
        from src.adapters.discord_bot.views.task_blocked_view import (
            TaskBlockedConfirmView,
            build_task_blocked_confirm_embed,
        )

        embed = build_task_blocked_confirm_embed(task, target_status, incomplete_prereqs)
        view = TaskBlockedConfirmView(
            task=task,
            target_status=target_status,
            incomplete_prereqs=incomplete_prereqs,
            author_id=author_id,
            task_service=task_service,
            auth_service=auth_service,
            workspace=workspace,
            bot=bot,
            notes=notes,
        )
        return embed, view

    @staticmethod
    def build_task_action_view(
        task_id: UUID,
        current_status: Any,
        task_service: TaskService | None = None,
        current_priority: Any = None,
        current_assignee_id: int | None = None,
        current_watchers: list[int] | None = None,
        **kwargs: Any,
    ) -> discord.ui.View:
        """Constructs the interactive Task Action Card controls."""
        from src.adapters.discord_bot.views.task_buttons import TaskActionView
        from src.domain.enums import PriorityLevel

        prio = current_priority if current_priority is not None else PriorityLevel.NORMAL
        return TaskActionView(
            task_id=task_id,
            current_status=current_status,
            task_service=task_service,
            current_priority=prio,
            current_assignee_id=current_assignee_id,
            current_watchers=current_watchers,
        )

    @staticmethod
    def build_task_create_modal(
        task_service: TaskService,
        project: Project | None = None,
        target_channel: discord.ForumChannel | discord.TextChannel | discord.Thread | None = None,
        auth_service: AuthService | None = None,
        draft_view: Any = None,
        initial_title: str = "",
        initial_desc: str = "",
        parent_interaction: discord.Interaction | None = None,
        parent_view: Any = None,
    ) -> discord.ui.Modal:
        """Constructs the Task creation details modal."""
        from src.adapters.discord_bot.views.task_builder import TaskDetailsModal

        return TaskDetailsModal(
            task_service=task_service,
            project=project,
            target_channel=target_channel,
            auth_service=auth_service,
            draft_view=draft_view,
            initial_title=initial_title,
            initial_desc=initial_desc,
            parent_interaction=parent_interaction,
            parent_view=parent_view,
        )

    @staticmethod
    def build_task_select_project_view(
        projects: list[Project],
        task_service: TaskService,
        project_service: ProjectService,
        squad_service: SquadService | None = None,
        current_channel_id: int | None = None,
        parent_channel_id: int | None = None,
        auth_service: AuthService | None = None,
        current_page: int = 0,
        query: str = "",
        **kwargs: Any,
    ) -> discord.ui.View:
        """Constructs the project picker view for task creation."""
        from src.adapters.discord_bot.views.task_menu import TaskSelectProjectView

        return TaskSelectProjectView(
            projects=projects,
            task_service=task_service,
            project_service=project_service,
            squad_service=squad_service,
            current_channel_id=current_channel_id,
            parent_channel_id=parent_channel_id,
            auth_service=auth_service,
            current_page=current_page,
            query=query,
            **kwargs,
        )


view_router = DiscordViewRouter()
