from __future__ import annotations

import logging

import discord
from discord import app_commands

from src.adapters.discord_bot.error_handler import send_interaction_error

logger = logging.getLogger("dgg_pm.cogs.admin")


class AdminCommandsMixin:
    """Administrative and operational commands for server managers."""

    admin_group = app_commands.Group(
        name="admin",
        description="Administrative commands and maintenance operations",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @admin_group.command(
        name="sync",
        description="Synchronize application slash commands with Discord on demand.",
    )
    @app_commands.describe(
        scope="Sync scope: 'guild' (current server, fast) or 'global' (all servers, slower propagation)",
    )
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="Guild (Current Server)", value="guild"),
            app_commands.Choice(name="Global (All Servers)", value="global"),
        ]
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def admin_sync(
        self,
        interaction: discord.Interaction,
        scope: str = "guild",
    ) -> None:
        """Synchronize slash commands to the current guild or globally."""
        if scope == "guild" and not interaction.guild:
            await interaction.response.send_message(
                "❌ Guild sync must be run inside a Discord server.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            guild_id = interaction.guild.id if (scope == "guild" and interaction.guild) else None
            await self.bot.sync_slash_commands(guild_id=guild_id)
            summary_text = ""

            if hasattr(self.bot, "format_command_tree_summary"):
                summary_text = f"\n\n{self.bot.format_command_tree_summary()}"

            if guild_id:
                guild_name = interaction.guild.name if interaction.guild else str(guild_id)
                await interaction.followup.send(
                    f"✅ **Slash Commands Synchronized**\n"
                    f"Successfully synced slash commands to **{guild_name}** (`{guild_id}`)."
                    f"{summary_text}",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"✅ **Slash Commands Synchronized**\n"
                    f"Successfully synced slash commands globally. "
                    f"*(Note: Discord global commands may take up to an hour to propagate to all clients.)*"
                    f"{summary_text}",
                    ephemeral=True,
                )

        except discord.errors.Forbidden as exc:
            if getattr(exc, "code", None) == 50001:
                await interaction.followup.send(
                    "❌ **Command Sync Failed (403 Missing Access)**\n"
                    "The bot lacks access to sync application commands. "
                    "Ensure the bot was invited with the `applications.commands` OAuth2 scope.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"❌ **Command Sync Failed (Forbidden)**: {exc}",
                    ephemeral=True,
                )
        except Exception as exc:
            logger.exception("Error during /pm admin sync: %s", exc)
            await interaction.followup.send(
                f"❌ **Command Sync Error**: {exc}",
                ephemeral=True,
            )

    @admin_group.command(
        name="retry-outbox",
        description="Reclaim and retry dead-lettered outbox events that failed during an outage.",
    )
    @app_commands.describe(
        hours="Lookback window in hours to reclaim failed events (default: 24.0)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def admin_retry_outbox(
        self,
        interaction: discord.Interaction,
        hours: float = 24.0,
    ) -> None:
        """Reclaim recent FAILED outbox events and reset them to PENDING for redelivery."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            outbox_service = getattr(self, "outbox_service", None) or getattr(self.bot, "outbox_service", None)
            if not outbox_service and self.task_service:
                outbox_service = getattr(self.task_service, "outbox_service", None)

            if not outbox_service:
                await interaction.followup.send(
                    "❌ Outbox service is not available on this bot instance.",
                    ephemeral=True,
                )
                return

            count = await outbox_service.reclaim_failed_events(max_age_hours=hours)
            if count > 0:
                await interaction.followup.send(
                    f"✅ **Outbox Events Reclaimed**\n"
                    f"Successfully reclaimed **{count}** failed outbox event(s) "
                    f"from the past **{hours:.1f}** hour(s).\n"
                    f"They have been reset to `PENDING` with retry count cleared and are now queued for dispatch.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"ℹ️ **No Failed Events Found**\n"
                    f"There were no `FAILED` outbox events within the past **{hours:.1f}** hour(s) to reclaim.",
                    ephemeral=True,
                )
        except Exception as exc:
            logger.exception("Error during /pm admin retry-outbox: %s", exc)
            await interaction.followup.send(
                f"❌ **Outbox Reclaim Failed**: {exc}",
                ephemeral=True,
            )

    @admin_group.command(
        name="lead",
        description="Authorize, revoke, or list Team Lead roles and individual members",
    )
    @app_commands.describe(
        action="Action to perform (add, remove, or list authorized Team Leads)",
        role="Discord role to authorize or revoke",
        user="Discord member to authorize or revoke",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Add Team Lead(s)", value="add"),
            app_commands.Choice(name="Remove Team Lead(s)", value="remove"),
            app_commands.Choice(name="List Team Leads", value="list"),
        ]
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def admin_lead(
        self,
        interaction: discord.Interaction,
        action: str,
        role: discord.Role | None = None,
        user: discord.Member | None = None,
    ) -> None:
        """Authorize or revoke Discord roles and individual members to create/manage projects and squads."""
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            guild_id = interaction.guild.id
            if action in ("add", "remove"):
                if not role and not user:
                    await interaction.followup.send(
                        "❌ Please specify a role, a member, or both to add/remove.",
                        ephemeral=True,
                    )
                    return

                if user and getattr(user, "bot", False):
                    await interaction.followup.send(
                        "❌ Bots cannot be authorized as Team Leads.",
                        ephemeral=True,
                    )
                    return

                messages: list[str] = []
                if action == "add":
                    if role:
                        await self.auth_service.add_guild_lead_role(guild_id, role.id)
                        messages.append(f"⭐ **Added Team Lead Role**: <@&{role.id}>")
                    if user:
                        await self.auth_service.add_guild_lead_user(guild_id, user.id)
                        messages.append(f"⭐ **Added Team Lead Member**: <@{user.id}>")
                    messages.append("They are now authorized to create and manage projects and squads.")
                    await interaction.followup.send("\n".join(messages), ephemeral=True)
                else:  # remove
                    if role:
                        removed_role = await self.auth_service.remove_guild_lead_role(guild_id, role.id)
                        if removed_role:
                            messages.append(f"✅ **Removed Team Lead Role**: <@&{role.id}>")
                        else:
                            messages.append(f"ℹ️ <@&{role.id}> was not registered as an authorized Team Lead role.")
                    if user:
                        removed_user = await self.auth_service.remove_guild_lead_user(guild_id, user.id)
                        if removed_user:
                            messages.append(f"✅ **Removed Team Lead Member**: <@{user.id}>")
                        else:
                            messages.append(f"ℹ️ <@{user.id}> was not registered as an authorized Team Lead member.")
                    await interaction.followup.send("\n".join(messages), ephemeral=True)

            elif action == "list":
                role_ids = await self.auth_service.list_guild_lead_roles(guild_id)
                user_ids = await self.auth_service.list_guild_lead_users(guild_id)

                if not role_ids and not user_ids:
                    await interaction.followup.send(
                        "ℹ️ **No Team Leads Configured**\n"
                        "Only Discord Server Managers (users with `Manage Server` or `Administrator`) "
                        "can create and manage projects.",
                        ephemeral=True,
                    )
                    return

                embed = discord.Embed(
                    title="👥 Authorized Team Leads",
                    description=(
                        "The following Discord roles and individual members are authorized to create "
                        "and manage projects and squads without requiring server-wide `Manage Server` permissions:\n"
                    ),
                    color=discord.Color.blue(),
                )

                if role_ids:
                    role_lines = [f"• <@&{rid}> (`{rid}`)" for rid in sorted(role_ids)]
                    embed.add_field(
                        name=f"Configured Roles ({len(role_ids)})",
                        value="\n".join(role_lines),
                        inline=False,
                    )
                else:
                    embed.add_field(
                        name="Configured Roles (0)",
                        value="*No roles configured*",
                        inline=False,
                    )

                if user_ids:
                    user_lines = [f"• <@{uid}> (`{uid}`)" for uid in sorted(user_ids)]
                    embed.add_field(
                        name=f"Configured Members ({len(user_ids)})",
                        value="\n".join(user_lines),
                        inline=False,
                    )
                else:
                    embed.add_field(
                        name="Configured Members (0)",
                        value="*No individual members configured*",
                        inline=False,
                    )

                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await send_interaction_error(interaction, e, "managing team leads", logger, ephemeral=True)

    @admin_group.command(
        name="lead-role",
        description="Authorize or revoke an existing Discord Team Lead role to create and manage projects",
    )
    @app_commands.describe(
        action="Action to perform (add, remove, or list authorized Team Lead roles)",
        role="Discord role to authorize or revoke (required for add/remove)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Add Team Lead Role", value="add"),
            app_commands.Choice(name="Remove Team Lead Role", value="remove"),
            app_commands.Choice(name="List Team Lead Roles", value="list"),
        ]
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def admin_lead_role(
        self,
        interaction: discord.Interaction,
        action: str,
        role: discord.Role | None = None,
    ) -> None:
        """Authorize or revoke an existing Discord role to create/manage projects and squads."""
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            guild_id = interaction.guild.id
            if action == "add":
                if not role:
                    await interaction.followup.send("❌ Please specify a Discord role to add.", ephemeral=True)
                    return
                await self.auth_service.add_guild_lead_role(guild_id, role.id)
                await interaction.followup.send(
                    f"⭐ **Added Team Lead Role**: <@&{role.id}>\n"
                    f"Members holding this role are now authorized to create and manage projects and squads.",
                    ephemeral=True,
                )
            elif action == "remove":
                if not role:
                    await interaction.followup.send("❌ Please specify a Discord role to remove.", ephemeral=True)
                    return
                removed = await self.auth_service.remove_guild_lead_role(guild_id, role.id)
                if removed:
                    await interaction.followup.send(
                        f"✅ **Removed Team Lead Role**: <@&{role.id}> is no longer authorized to manage projects.",
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        f"ℹ️ <@&{role.id}> was not registered as an authorized Team Lead role.",
                        ephemeral=True,
                    )
            elif action == "list":
                role_ids = await self.auth_service.list_guild_lead_roles(guild_id)
                if not role_ids:
                    await interaction.followup.send(
                        "ℹ️ **No Team Lead Roles Configured**\n"
                        "Only Discord Server Managers (users with `Manage Server` or `Administrator`) "
                        "can create and manage projects.",
                        ephemeral=True,
                    )
                else:
                    mentions = "\n".join(f"• <@&{rid}> (`{rid}`)" for rid in sorted(role_ids))
                    embed = discord.Embed(
                        title="👥 Authorized Team Lead Roles",
                        description=(
                            "Members holding any of the following Discord roles can create and manage "
                            "projects and squads without requiring server-wide `Manage Server` permissions:\n\n"
                            + mentions
                        ),
                        color=discord.Color.blue(),
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await send_interaction_error(interaction, e, "managing team lead roles", logger, ephemeral=True)
