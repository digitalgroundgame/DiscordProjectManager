from __future__ import annotations

import logging

import discord
from discord import app_commands

from src.adapters.discord_bot.cogs.autocomplete import AutocompleteMixin
from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.member_resolver import resolve_discord_member_name
from src.adapters.discord_bot.router import DiscordViewRouter
from src.adapters.discord_bot.views.forum_helpers import ensure_pinned_hub_post, setup_forum_tags
from src.adapters.discord_bot.views.project_menu import RebuildConfirmView
from src.adapters.discord_bot.workspace_protocol import (
    ProjectProvisionSpec,
)

logger = logging.getLogger("dgg_pm.cogs.project")

project_autocomplete = AutocompleteMixin.project_autocomplete
squad_autocomplete = AutocompleteMixin.squad_autocomplete


class ProjectCommandsMixin(AutocompleteMixin):
    """Project container, channel binding, and squad role mapping commands."""

    project_group = app_commands.Group(name="project", description="Project container and channel operations")

    @project_group.command(name="create", description="Create a project container, bind channel, and map squad role.")
    @app_commands.describe(
        name="Project Name (e.g. Mobile App)",
        prefix="Short uppercase task prefix (e.g. MOB)",
        role="Discord server role representing the squad working on this project",
        role_2="Optional second Discord server role representing another squad",
        role_3="Optional third Discord server role representing another squad",
        channel="Discord Forum Channel to bind for tasks",
        description="Optional markdown project overview",
        category="Optional organizational category",
    )
    async def project_create(
        self,
        interaction: discord.Interaction,
        name: str,
        prefix: str,
        role: discord.Role,
        role_2: discord.Role | None = None,
        role_3: discord.Role | None = None,
        channel: discord.ForumChannel | None = None,
        description: str | None = None,
        category: str | None = None,
    ) -> None:
        if not interaction.guild:
            return
        if channel is not None and not isinstance(channel, discord.ForumChannel):
            await interaction.response.send_message(
                "❌ Projects must be bound to a Discord Forum Channel.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            if self.auth_service:
                await self.auth_service.require_project_management(interaction.user, interaction.guild.id)
            roles = [r for r in [role, role_2, role_3] if r is not None]
            ref = await self.project_workspace.provision_project(
                ProjectProvisionSpec(
                    guild_id=interaction.guild.id,
                    name=name,
                    prefix=prefix,
                    role=role,
                    roles=roles,
                    channel=channel,
                    description=description,
                    category=category,
                )
            )
            project = ref.project

            hub_note = ""
            if ref.channel_id:
                hub_note = " • 📌 Pinned Control Hub created"

            role_mentions = ", ".join(f"<@&{r.id}>" for r in roles)
            embed = discord.Embed(
                title=f"📁 Project Created: {project.name} (`{project.prefix}`)",
                description=project.description or "*No description provided.*",
                color=discord.Color.blue(),
            )
            embed.add_field(name="Task ID Prefix", value=f"`{project.prefix}-#`", inline=True)
            embed.add_field(name="Assigned Squad(s)", value=role_mentions, inline=True)
            if project.discord_channel_id:
                embed.add_field(name="Bound Channel", value=f"<#{project.discord_channel_id}>{hub_note}", inline=True)

            await interaction.followup.send(embed=embed)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(interaction, e, f"creating project '{name}'", logger, ephemeral=True)

    @project_group.command(name="list", description="List all active projects in this server.")
    async def project_list(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            projects = await self.project_service.list_projects(interaction.guild.id, include_archived=False)
            if not projects:
                await interaction.followup.send("📁 No active projects found. Use `/pm project create` to set one up.")
                return

            embed = discord.Embed(title="📁 Server Projects", color=discord.Color.blue())
            for p in projects:
                chan_str = f"<#{p.discord_channel_id}>" if p.discord_channel_id else "*No channel bound*"
                role_ids = p.discord_role_ids or ([p.discord_role_id] if p.discord_role_id else [])
                role_str = ", ".join(f"<@&{rid}>" for rid in role_ids) if role_ids else "*No squad role*"
                embed.add_field(
                    name=f"**{p.name}** (`{p.prefix}`)",
                    value=f"Channel: {chan_str}\nSquads: {role_str}\nStatus: Active",
                    inline=False,
                )
            await interaction.followup.send(embed=embed)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(interaction, e, "listing projects", logger, ephemeral=True)

    @project_group.command(name="tree", description="Render tech-tree dependency graph for a project.")
    @app_commands.describe(
        project_name="Name of the project to visualize",
        orientation="Layout orientation: horizontal (lr) or vertical (tb)",
    )
    @app_commands.choices(
        orientation=[
            app_commands.Choice(name="Horizontal (Left to Right)", value="lr"),
            app_commands.Choice(name="Vertical (Top to Bottom)", value="tb"),
        ]
    )
    @app_commands.autocomplete(project_name=project_autocomplete)
    async def project_tree(
        self,
        interaction: discord.Interaction,
        project_name: str,
        orientation: app_commands.Choice[str] | None = None,
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            project = await self.project_service.get_by_name(interaction.guild.id, project_name)
            if not project:
                await interaction.followup.send(f"❌ Project '{project_name}' not found.", ephemeral=True)
                return

            await self.auth_service.require_project_view(interaction.user, project.id)

            orient_val = orientation.value if orientation else "lr"
            buf = await self.task_service.render_project_tree(
                guild_id=interaction.guild.id,
                project_id=project.id,
                orientation=orient_val,
                member_resolver=lambda uid: resolve_discord_member_name(uid, interaction.guild),
            )
            file = discord.File(fp=buf, filename="tech_tree.png")
            orient_label = "Horizontal (Left to Right)" if orient_val == "lr" else "Vertical (Top to Bottom)"
            embed = discord.Embed(
                title=f"🌲 Tech Tree: [{project.prefix}] {project.name}",
                description=f"Showing dependency graph in **{orient_label}** layout.",
                color=discord.Color.from_rgb(16, 152, 247),
            )
            embed.set_image(url="attachment://tech_tree.png")
            view = DiscordViewRouter.build_tech_tree_viewer(
                self.task_service, project, current_orientation=orient_val, auth_service=self.auth_service
            )
            await interaction.followup.send(embed=embed, file=file, view=view, ephemeral=True)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"rendering tech tree for '{project_name}'", logger, ephemeral=True
            )

    @project_group.command(name="archive", description="Archive a project container.")
    @app_commands.describe(project_name="Name of the project to archive")
    @app_commands.autocomplete(project_name=project_autocomplete)
    async def project_archive(self, interaction: discord.Interaction, project_name: str) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            if self.auth_service:
                await self.auth_service.require_project_management(interaction.user, interaction.guild.id)
            project = await self.project_service.get_by_name(interaction.guild.id, project_name)
            if not project:
                await interaction.followup.send(f"❌ Project '{project_name}' not found.", ephemeral=True)
                return

            await self.project_service.archive_project(project.id)

            if hasattr(self.bot, "sync_task_thread") and self.task_service:
                tasks, _ = await self.task_service.list_tasks(
                    guild_id=interaction.guild.id, project_id=project.id, status=None, include_archived=True
                )
                for t in tasks:
                    if t.discord_thread_id:
                        try:
                            await self.bot.sync_task_thread(t, action="archive")
                        except Exception as te:
                            logger.warning("Could not sync task thread on project archive for task %s: %s", t.id, te)

            if interaction.guild and project.discord_channel_id:
                chan = interaction.guild.get_channel(project.discord_channel_id)
                if chan and isinstance(chan, (discord.ForumChannel, discord.TextChannel)):
                    try:
                        await ensure_pinned_hub_post(
                            channel=chan,
                            project_service=self.project_service,
                            squad_service=self.squad_service,
                            task_service=self.task_service,
                            user_service=self.user_service,
                        )
                    except Exception as he:
                        logger.warning("Could not refresh pinned hub on project archive: %s", he)

            await interaction.followup.send(f"📦 Archived project **{project.name}** (`{project.prefix}`).")
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(interaction, e, f"archiving project '{project_name}'", logger, ephemeral=True)

    @project_group.command(name="unarchive", description="Restore an archived project container.")
    @app_commands.describe(project_name="Name of the project to restore")
    @app_commands.autocomplete(project_name=project_autocomplete)
    async def project_unarchive(self, interaction: discord.Interaction, project_name: str) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            if self.auth_service:
                await self.auth_service.require_project_management(interaction.user, interaction.guild.id)
            project = await self.project_service.get_by_name(interaction.guild.id, project_name)
            if not project:
                await interaction.followup.send(f"❌ Project '{project_name}' not found.", ephemeral=True)
                return

            await self.project_service.unarchive_project(project.id)

            if hasattr(self.bot, "sync_task_thread") and self.task_service:
                tasks, _ = await self.task_service.list_tasks(
                    guild_id=interaction.guild.id, project_id=project.id, status=None, include_archived=True
                )
                for t in tasks:
                    if t.discord_thread_id:
                        try:
                            await self.bot.sync_task_thread(t, action="unarchive")
                        except Exception as te:
                            logger.warning("Could not sync task thread on project unarchive for task %s: %s", t.id, te)

            if interaction.guild and project.discord_channel_id:
                chan = interaction.guild.get_channel(project.discord_channel_id)
                if chan and isinstance(chan, (discord.ForumChannel, discord.TextChannel)):
                    try:
                        await ensure_pinned_hub_post(
                            channel=chan,
                            project_service=self.project_service,
                            squad_service=self.squad_service,
                            task_service=self.task_service,
                            user_service=self.user_service,
                        )
                    except Exception as he:
                        logger.warning("Could not refresh pinned hub on project unarchive: %s", he)

            await interaction.followup.send(f"📂 Restored project **{project.name}** (`{project.prefix}`).")
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(interaction, e, f"restoring project '{project_name}'", logger, ephemeral=True)

    @project_group.command(name="role", description="Map or unmap a Discord squad role on a project container.")
    @app_commands.describe(
        project_name="Target project name",
        role="Discord role representing the functional squad",
        action="Map (add) or Unmap (remove) role from project",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Map Role to Project", value="add"),
            app_commands.Choice(name="Unmap Role from Project", value="remove"),
        ]
    )
    @app_commands.autocomplete(project_name=project_autocomplete)
    async def project_role(
        self,
        interaction: discord.Interaction,
        project_name: str,
        role: discord.Role,
        action: str = "add",
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            if self.auth_service:
                await self.auth_service.require_project_management(interaction.user, interaction.guild.id)
            project = await self.project_service.get_by_name(interaction.guild.id, project_name)
            if not project:
                await interaction.followup.send(f"❌ Project '{project_name}' not found.", ephemeral=True)
                return

            squad = await self.squad_service.get_or_create_squad_for_role(interaction.guild.id, role.id, role.name)

            if action == "add":
                await self.project_service.assign_squad_to_project(project_id=project.id, squad_id=squad.id)
                msg = f"🔗 Mapped role **{role.name}** (<@&{role.id}>) to project **{project.name}**."
            else:
                await self.project_service.remove_squad_from_project(project_id=project.id, squad_id=squad.id)
                msg = f"✂️ Unmapped role **{role.name}** from project **{project.name}**."

            await interaction.followup.send(msg)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"managing project role for '{project_name}'", logger, ephemeral=True
            )

    @project_group.command(name="lead", description="Designate or remove a Squad Lead for a project's squads.")
    @app_commands.describe(
        project_name="Target project name",
        user="Discord member to designate or remove as Squad Lead",
        action="Action to perform (Add or Remove Lead)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Add Squad Lead", value="add"),
            app_commands.Choice(name="Remove Squad Lead", value="remove"),
        ]
    )
    @app_commands.autocomplete(project_name=project_autocomplete)
    async def project_lead(
        self,
        interaction: discord.Interaction,
        project_name: str,
        user: discord.Member,
        action: str = "add",
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            project = await self.project_service.get_by_name(interaction.guild.id, project_name)
            if not project:
                await interaction.followup.send(f"❌ Project '{project_name}' not found.", ephemeral=True)
                return

            squads = await self.project_service.list_squads_for_project(project.id)
            if not squads:
                await interaction.followup.send(f"❌ Project '{project.name}' has no assigned squads.", ephemeral=True)
                return

            # Check auth: server manager or active lead
            is_manager = self.auth_service.is_server_manager(interaction.user)
            if not is_manager:
                is_any_lead = False
                for s in squads:
                    if await self.auth_service.can_manage_squad_leads(interaction.user, s.id):
                        is_any_lead = True
                        break
                if not is_any_lead:
                    await interaction.followup.send(
                        "❌ You do not have permission to manage squad leads for this project.",
                        ephemeral=True,
                    )
                    return

            user_roles = {r.id for r in getattr(user, "roles", []) if hasattr(r, "id")}
            matching_squads = [s for s in squads if s.discord_role_id in user_roles] if action == "add" else squads

            if action == "add":
                if not matching_squads:
                    role_mentions = ", ".join(f"<@&{s.discord_role_id}>" for s in squads)
                    await interaction.followup.send(
                        f"❌ <@{user.id}> does not hold any of project **{project.name}**'s assigned roles "
                        f"({role_mentions}).\nPlease assign them the Discord role first.",
                        ephemeral=True,
                    )
                    return
                for s in matching_squads:
                    await self.squad_service.add_squad_lead(s.id, user.id)
                msg = f"⭐ Designated <@{user.id}> as **Squad Lead** for project **{project.name}**."
            else:
                for s in squads:
                    await self.squad_service.remove_squad_lead(s.id, user.id)
                msg = f"✅ Removed Squad Lead status from <@{user.id}> for project **{project.name}**."

            await interaction.followup.send(msg)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"managing lead for project '{project_name}'", logger, ephemeral=True
            )

    @project_group.command(name="squad", description="Map or unmap a functional squad to a project container.")
    @app_commands.describe(
        project_name="Target project name",
        squad_name="Name of the functional squad",
        action="Map (assign) or Unmap (remove) squad",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Map Squad to Project", value="map"),
            app_commands.Choice(name="Unmap Squad from Project", value="unmap"),
        ]
    )
    @app_commands.autocomplete(project_name=project_autocomplete, squad_name=squad_autocomplete)
    async def project_squad(
        self,
        interaction: discord.Interaction,
        project_name: str,
        squad_name: str,
        action: str = "map",
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            if self.auth_service:
                await self.auth_service.require_project_management(interaction.user, interaction.guild.id)
            project = await self.project_service.get_by_name(interaction.guild.id, project_name)
            if not project:
                await interaction.followup.send(f"❌ Project '{project_name}' not found.", ephemeral=True)
                return

            squad = await self.squad_service.get_by_name(interaction.guild.id, squad_name)
            if not squad:
                await interaction.followup.send(f"❌ Squad '{squad_name}' not found.", ephemeral=True)
                return

            if action == "map":
                await self.project_service.assign_squad_to_project(project_id=project.id, squad_id=squad.id)
                msg = f"🔗 Mapped squad **{squad.name}** (<@&{squad.discord_role_id}>) to project **{project.name}**."
            else:
                await self.project_service.remove_squad_from_project(project_id=project.id, squad_id=squad.id)
                msg = f"✂️ Unmapped squad **{squad.name}** from project **{project.name}**."

            await interaction.followup.send(msg)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"mapping squad to project '{project_name}'", logger, ephemeral=True
            )

    @project_group.command(
        name="setup-forum",
        description="Automatically configure standard project management tags on a Forum Channel.",
    )
    @app_commands.describe(forum="The Discord Forum Channel to configure with PM tags")
    async def project_setup_forum(
        self,
        interaction: discord.Interaction,
        forum: discord.ForumChannel,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ Must be used inside a Discord server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            if self.auth_service:
                await self.auth_service.require_project_management(interaction.user, interaction.guild.id)
            tags_added, total_tags, err = await setup_forum_tags(forum)
            if err:
                await interaction.followup.send(f"❌ Failed to configure tags in <#{forum.id}>: {err}", ephemeral=True)
                return

            embed = discord.Embed(
                title=f"🏷️ Forum Tags Configured: #{forum.name}",
                description=(
                    f"Successfully verified and updated tags in <#{forum.id}>!\n\n"
                    f"• **New Tags Added:** `{tags_added}`\n"
                    f"• **Total Available Tags:** `{total_tags}` / 20\n\n"
                    "**Standard Tags Managed:**\n"
                    "• **Status:** `⏳ Not Started`, `🟡 In Progress`, `✅ Completed`\n"
                    "• **Priority:** `🔴 High Priority`, `🟡 Normal Priority`, `🟢 Low Priority`\n"
                    "• **Project:** One `📁 <Project Name>` tag per project bound to this forum"
                ),
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"configuring forum tags for <#{forum.id}>", logger, ephemeral=True
            )

    @project_group.command(
        name="rebuild",
        description="Rebuild and reconcile a project's Discord forum, tags, hub, and task threads.",
    )
    @app_commands.describe(
        project_name="Name of the project to rebuild",
        forum="Optional target Forum Channel to rebind to (auto-creates if omitted and missing)",
    )
    @app_commands.autocomplete(project_name=project_autocomplete)
    async def project_rebuild(
        self,
        interaction: discord.Interaction,
        project_name: str,
        forum: discord.ForumChannel | None = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ Must be used inside a Discord server.", ephemeral=True)
            return

        try:
            if self.auth_service:
                await self.auth_service.require_project_management(interaction.user, interaction.guild.id)
            project = await self.project_service.get_by_name(interaction.guild.id, project_name)
            if not project:
                # Check if archived
                projects = await self.project_service.list_projects(interaction.guild.id, include_archived=True)
                project = next((p for p in projects if p.name.lower() == project_name.lower()), None)
                if not project:
                    await interaction.response.send_message(f"❌ Project '{project_name}' not found.", ephemeral=True)
                    return

            # Fetch task count for preview
            task_count = 0
            if self.task_service:
                _, task_count = await self.task_service.list_tasks(
                    guild_id=interaction.guild.id, project_id=project.id, include_archived=True
                )

            channel_status = (
                f"Rebind to <#{forum.id}>"
                if forum
                else (
                    f"Existing: <#{project.discord_channel_id}>"
                    if project.discord_channel_id and interaction.guild.get_channel(project.discord_channel_id)
                    else "Auto-create new ForumChannel"
                )
            )

            embed = discord.Embed(
                title=f"⚠️ Confirm Workspace Rebuild: [{project.prefix}] {project.name}",
                description=(
                    f"Are you sure you want to rebuild the Discord workspace for **{project.name}**?\n\n"
                    f"**Planned Actions:**\n"
                    f"• **Channel Target:** {channel_status}\n"
                    f"• **Forum Tags:** Configure standard status, priority, and project tags\n"
                    f"• **Control Hub:** Mount and pin interactive Control Hub post\n"
                    f"• **Task Threads:** Reconcile **{task_count}** tasks "
                    "(recreate missing threads and enforce Archive Invariant)\n"
                    f"• **Project Status:** {'Unarchive and restore' if project.is_archived else 'Active'}\n\n"
                    "⚠️ *This will pace Discord API calls to respect rate limits.*"
                ),
                color=discord.Color.orange(),
            )
            view = RebuildConfirmView(
                project_workspace=self.project_workspace,
                project=project,
                target_channel=forum,
                interaction=interaction,
                task_count=task_count,
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except Exception as e:
            await send_interaction_error(interaction, e, "initiating project rebuild", logger, ephemeral=True)
