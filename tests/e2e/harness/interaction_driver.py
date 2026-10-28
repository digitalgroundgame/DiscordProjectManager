"""Synthetic Interaction Driver and User Journey Actor.

Simulates human user interactions (slash commands, button clicks, select menus,
modal forms) against DggPmBot and captures ephemeral and channel responses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from tests.e2e.harness.discord_twin import (
    SimulatedGuild,
    SimulatedMember,
    SimulatedMessage,
    next_snowflake,
)

if TYPE_CHECKING:
    from tests.e2e.harness.environment import E2ETestEnvironment


class CapturedResponse:
    """Tracks state and payload returned from an interaction."""

    def __init__(self, interaction: SyntheticInteraction):
        self.interaction = interaction
        self.deferred: bool = False
        self.thinking: bool = False
        self.ephemeral: bool = False
        self.content: str = ""
        self.embed: discord.Embed | None = None
        self.embeds: list[discord.Embed] = []
        self.view: discord.ui.View | None = None
        self.modal: discord.ui.Modal | None = None
        self.files: list[Any] = []
        self.deleted: bool = False
        self.followups: list[CapturedResponse] = []
        self._underlying_message: SimulatedMessage | None = None

    @property
    def message(self) -> SimulatedMessage | None:
        return self._underlying_message or getattr(self.interaction, "message", None)

    @message.setter
    def message(self, msg: SimulatedMessage | None) -> None:
        self._underlying_message = msg

    def all_text(self) -> str:
        """Helper to search across content and embed titles/descriptions."""
        parts = [self.content]
        for emb in self.embeds:
            if getattr(emb, "title", None):
                parts.append(str(emb.title))
            if getattr(emb, "description", None):
                parts.append(str(emb.description))
            for f in getattr(emb, "fields", []):
                parts.append(f"{f.name}: {f.value}")
        return "\n".join(p for p in parts if p)


class SyntheticInteractionResponse:
    """Simulates discord.InteractionResponse."""

    def __init__(self, interaction: SyntheticInteraction, captured: CapturedResponse):
        self._interaction = interaction
        self._captured = captured
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def defer(self, *, ephemeral: bool = False, thinking: bool = False) -> None:
        self._done = True
        self._captured.deferred = True
        self._captured.ephemeral = ephemeral
        self._captured.thinking = thinking

    async def send_message(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        embeds: list[discord.Embed] | None = None,
        view: discord.ui.View | None = None,
        file: Any = None,
        files: list[Any] | None = None,
        ephemeral: bool = False,
        **kwargs: Any,
    ) -> None:
        self._done = True
        self._captured.content = content or ""
        all_embeds = list(embeds or [])
        if embed is not None and embed not in all_embeds:
            all_embeds.insert(0, embed)
        self._captured.embeds = all_embeds
        self._captured.embed = all_embeds[0] if all_embeds else None
        self._captured.view = view
        self._captured.ephemeral = ephemeral
        if file:
            self._captured.files.append(file)
        if files:
            self._captured.files.extend(files)

        channel = self._interaction.channel
        guild = self._interaction.guild
        msg = SimulatedMessage(
            id=next_snowflake(),
            channel=channel,
            guild=guild,
            author=self._interaction.user,
            content=self._captured.content,
            embeds=all_embeds,
            view=view,
        )
        self._captured.message = msg
        msg.attachments = list(self._captured.files)
        if not ephemeral and channel and hasattr(channel, "messages"):
            channel.messages.append(msg)
        if guild:
            guild._register_message(msg)

    async def send_modal(self, modal: discord.ui.Modal) -> None:
        self._done = True
        self._captured.modal = modal

    async def edit_message(
        self,
        *,
        content: str | None = None,
        embed: discord.Embed | None = None,
        embeds: list[discord.Embed] | None = None,
        view: discord.ui.View | None = None,
        attachments: list[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._done = True
        if content is not None:
            self._captured.content = content
        all_embeds = list(embeds or [])
        if embed is not None and embed not in all_embeds:
            all_embeds.insert(0, embed)
        if all_embeds or embeds is not None:
            self._captured.embeds = all_embeds
            self._captured.embed = all_embeds[0] if all_embeds else None
        if view is not None:
            self._captured.view = view

        # Mutate target message if present
        msg = self._captured.message
        if msg:
            await msg.edit(content=content, embed=self._captured.embed, view=view, attachments=attachments)


class SyntheticFollowup:
    """Simulates discord.Webhook / interaction.followup."""

    def __init__(self, interaction: SyntheticInteraction, captured: CapturedResponse):
        self._interaction = interaction
        self._captured = captured

    async def send(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        embeds: list[discord.Embed] | None = None,
        view: discord.ui.View | None = None,
        file: Any = None,
        files: list[Any] | None = None,
        ephemeral: bool = False,
        wait: bool = False,
        **kwargs: Any,
    ) -> SimulatedMessage:
        sub_captured = CapturedResponse(self._interaction)
        sub_captured.content = content or ""
        all_embeds = list(embeds or [])
        if embed is not None and embed not in all_embeds:
            all_embeds.insert(0, embed)
        sub_captured.embeds = all_embeds
        sub_captured.embed = all_embeds[0] if all_embeds else None
        sub_captured.view = view
        sub_captured.ephemeral = ephemeral
        if file:
            sub_captured.files.append(file)
        if files:
            sub_captured.files.extend(files)

        channel = self._interaction.channel
        guild = self._interaction.guild
        msg = SimulatedMessage(
            id=next_snowflake(),
            channel=channel,
            guild=guild,
            author=self._interaction.user,
            content=sub_captured.content,
            embeds=all_embeds,
            view=view,
        )
        sub_captured.message = msg
        if not ephemeral and channel and hasattr(channel, "messages"):
            channel.messages.append(msg)
        if guild:
            guild._register_message(msg)

        # Update root captured response if empty, and append to followups
        if not self._captured.content and not self._captured.embeds:
            self._captured.content = sub_captured.content
            self._captured.embeds = sub_captured.embeds
            self._captured.embed = sub_captured.embed
            self._captured.view = sub_captured.view
            self._captured.files = list(sub_captured.files)
            self._captured.message = msg
        msg.attachments = list(sub_captured.files)

        self._captured.followups.append(sub_captured)
        return msg


class SyntheticInteraction(discord.Interaction):
    """Faithful test double for discord.Interaction."""

    def __init__(
        self,
        client: discord.Client,
        guild: SimulatedGuild,
        channel: discord.abc.GuildChannel | discord.Thread | None,
        user: SimulatedMember,
        interaction_type: discord.InteractionType = discord.InteractionType.application_command,
        data: dict[str, Any] | None = None,
        message: SimulatedMessage | None = None,
        command: app_commands.Command | None = None,
    ):
        self.id = next_snowflake()
        self._client = client
        self._custom_guild = guild
        self.guild_id = guild.id if guild else None
        self.channel = channel
        self.user = user
        self.type = interaction_type
        self.data = data or {}
        self.message = message
        self._cs_command = command
        self.extras = {}
        self._permissions = user.guild_permissions.value if user and hasattr(user, "guild_permissions") else 0

        self.captured = CapturedResponse(self)
        if message:
            self.captured.message = message
        self._custom_response = SyntheticInteractionResponse(self, self.captured)
        self._custom_followup = SyntheticFollowup(self, self.captured)

    @property
    def guild(self) -> SimulatedGuild:
        return self._custom_guild

    @property
    def command(self) -> app_commands.Command | None:
        return self._cs_command

    @property
    def response(self) -> SyntheticInteractionResponse:
        return self._custom_response

    @property
    def followup(self) -> SyntheticFollowup:
        return self._custom_followup

    async def edit_original_response(
        self,
        *,
        content: str | None = None,
        embed: discord.Embed | None = None,
        embeds: list[discord.Embed] | None = None,
        view: discord.ui.View | None = None,
        attachments: list[Any] | None = None,
        **kwargs: Any,
    ) -> SimulatedMessage:
        if content is not None:
            self.captured.content = content
        all_embeds = list(embeds or [])
        if embed is not None and embed not in all_embeds:
            all_embeds.insert(0, embed)
        if all_embeds or embeds is not None:
            self.captured.embeds = all_embeds
            self.captured.embed = all_embeds[0] if all_embeds else None
        if view is not None:
            self.captured.view = view
        if attachments is not None:
            self.captured.files = list(attachments)

        msg = self.captured.message
        if msg:
            await msg.edit(content=content, embed=self.captured.embed, view=view, attachments=attachments)
            return msg

        msg = SimulatedMessage(
            id=next_snowflake(),
            channel=self.channel,
            guild=self.guild,
            author=self.user,
            content=self.captured.content,
            embeds=self.captured.embeds,
            view=view,
        )
        self.captured.message = msg
        return msg

    async def delete_original_response(self) -> None:
        self.captured.deleted = True
        if self.captured.message:
            await self.captured.message.delete()

    async def original_response(self) -> SimulatedMessage:
        if self.captured.message:
            return self.captured.message
        msg = SimulatedMessage(
            id=next_snowflake(),
            channel=self.channel,
            guild=self.guild,
            author=self.user,
            content=self.captured.content,
            embeds=self.captured.embeds,
            view=self.captured.view,
        )
        self.captured.message = msg
        return msg


class UserJourneyActor:
    """Represents a simulated Discord user driving interactions against DggPmBot."""

    def __init__(
        self,
        user: SimulatedMember,
        guild: SimulatedGuild,
        default_channel: discord.abc.GuildChannel | discord.Thread | None,
        env: E2ETestEnvironment,
    ):
        self.user = user
        self.guild = guild
        self.default_channel = default_channel
        self.env = env

    def _resolve_command(self, command_path: str) -> app_commands.Command:
        """Resolves slash command path (e.g. '/pm project create' or 'pm menu')."""
        tokens = [t.strip().lstrip("/") for t in command_path.strip().split() if t.strip()]
        if not tokens:
            raise ValueError(f"Invalid empty command path: {command_path!r}")

        # Root command in tree
        root_name = tokens[0]
        cmd = self.env.bot.tree.get_command(root_name)
        if not cmd:
            raise ValueError(f"Slash command root '{root_name}' not found in command tree.")

        for token in tokens[1:]:
            if hasattr(cmd, "get_command"):
                next_cmd = cmd.get_command(token)
                if not next_cmd:
                    raise ValueError(f"Subcommand '{token}' not found under '{cmd.name}'.")
                cmd = next_cmd
            else:
                raise ValueError(f"Command '{cmd.name}' has no subcommands; cannot resolve '{token}'.")

        return cmd

    async def slash(
        self,
        command_path: str,
        *,
        invocation_channel: discord.abc.GuildChannel | discord.Thread | None = None,
        **kwargs: Any,
    ) -> CapturedResponse:
        """Executes a slash command as this user."""
        cmd = self._resolve_command(command_path)
        param_names = {p.name for p in cmd.parameters}

        target_channel = invocation_channel or self.default_channel
        if target_channel is None:
            if "channel" in kwargs and "channel" not in param_names:
                target_channel = kwargs.pop("channel")
            elif kwargs.get("channel") is not None:
                # If command takes channel parameter, use that channel also as invocation context if none set
                target_channel = kwargs["channel"]

        interaction = SyntheticInteraction(
            client=self.env.bot,
            guild=self.guild,
            channel=target_channel,
            user=self.user,
            interaction_type=discord.InteractionType.application_command,
            command=cmd,
            data={"name": cmd.name, "options": kwargs},
        )

        try:
            # Evaluate discord.py app command checks (e.g. has_permissions)
            can_run = await cmd._check_can_run(interaction)
            if can_run:
                await cmd._do_call(interaction, kwargs)
        except Exception as exc:
            cog = getattr(cmd, "binding", None)
            if cog and hasattr(cog, "cog_app_command_error"):
                await cog.cog_app_command_error(interaction, exc)
            else:
                await self.env.bot.on_tree_error(interaction, exc)

        return interaction.captured

    async def click_button(
        self,
        target: CapturedResponse | SimulatedMessage,
        *,
        label: str | None = None,
        custom_id: str | None = None,
        channel: discord.abc.GuildChannel | discord.Thread | None = None,
    ) -> CapturedResponse:
        """Finds and clicks a button on a message or captured response view."""
        view = target.view
        if not view:
            raise ValueError(f"Target {target} has no attached View containing buttons.")

        matched_button: discord.ui.Button | None = None
        for item in view.children:
            if isinstance(item, discord.ui.Button):
                if custom_id and item.custom_id == custom_id:
                    matched_button = item
                    break
                if label and (item.label == label or (item.label and label in item.label)):
                    matched_button = item
                    break

        if not matched_button:
            avail = [f"'{b.label}' (id={b.custom_id})" for b in view.children if isinstance(b, discord.ui.Button)]
            raise ValueError(f"Button (label={label!r}, custom_id={custom_id!r}) not found. Available buttons: {avail}")

        if matched_button.disabled:
            raise RuntimeError(f"Cannot click button '{matched_button.label}' because it is disabled.")

        target_msg = target if isinstance(target, SimulatedMessage) else target.message
        target_channel = channel or getattr(target_msg, "channel", None) or self.default_channel

        interaction = SyntheticInteraction(
            client=self.env.bot,
            guild=self.guild,
            channel=target_channel,
            user=self.user,
            interaction_type=discord.InteractionType.component,
            data={"component_type": 2, "custom_id": matched_button.custom_id},
            message=target_msg,
        )

        btn_cid = str(matched_button.custom_id or "")
        # Dynamic persistent task buttons route through bot.on_interaction
        if btn_cid.startswith("task:"):
            await self.env.bot.on_interaction(interaction)
        else:
            await matched_button.callback(interaction)

        return interaction.captured

    async def select_values(
        self,
        target: CapturedResponse | SimulatedMessage,
        *,
        values: list[Any],
        row: int | None = None,
        placeholder: str | None = None,
        channel: discord.abc.GuildChannel | discord.Thread | None = None,
    ) -> CapturedResponse:
        """Selects options on a Select / UserSelect / RoleSelect component."""
        view = target.view
        if not view:
            raise ValueError(f"Target {target} has no attached View containing select menus.")

        matched_select: Any = None
        for item in view.children:
            if isinstance(
                item, (discord.ui.Select, discord.ui.UserSelect, discord.ui.RoleSelect, discord.ui.ChannelSelect)
            ):
                if row is not None and item.row == row:
                    matched_select = item
                    break
                if placeholder and item.placeholder and placeholder in item.placeholder:
                    matched_select = item
                    break
                if row is None and placeholder is None:
                    matched_select = item
                    break

        if not matched_select:
            raise ValueError(f"Select component matching row={row}, placeholder={placeholder!r} not found.")

        target_msg = target if isinstance(target, SimulatedMessage) else target.message
        target_channel = channel or getattr(target_msg, "channel", None) or self.default_channel

        interaction = SyntheticInteraction(
            client=self.env.bot,
            guild=self.guild,
            channel=target_channel,
            user=self.user,
            interaction_type=discord.InteractionType.component,
            data={"component_type": 3, "custom_id": getattr(matched_select, "custom_id", ""), "values": values},
            message=target_msg,
        )

        matched_select._values = list(values)
        await matched_select.callback(interaction)
        return interaction.captured

    async def submit_modal(
        self,
        modal: discord.ui.Modal,
        *,
        channel: discord.abc.GuildChannel | discord.Thread | None = None,
        **field_values: str,
    ) -> CapturedResponse:
        """Populates TextInput fields and submits a Modal dialog."""
        if not modal:
            raise ValueError("No modal provided to submit_modal.")

        text_inputs = [item for item in modal.children if isinstance(item, discord.ui.TextInput)]
        if len(text_inputs) == 1 and len(field_values) == 1:
            only_val = next(iter(field_values.values()))
            text_inputs[0]._value = str(only_val)
        else:
            for item in text_inputs:
                matched_val = None
                for k, v in field_values.items():
                    if hasattr(modal, k) and getattr(modal, k) is item:
                        matched_val = v
                        break
                    clean_k = k.lower().replace("_", "")
                    short_k = clean_k.replace("input", "")
                    clean_id = (item.custom_id or "").lower().replace("_", "")
                    raw_lbl = (
                        getattr(item, "_label", None)
                        or getattr(getattr(item, "_underlying", None), "label", None)
                        or getattr(item, "label", "")
                        or ""
                    )
                    clean_lbl = str(raw_lbl).lower().replace(" ", "").replace("_", "")
                    if (
                        clean_k in clean_id
                        or clean_k in clean_lbl
                        or (short_k and (short_k in clean_id or short_k in clean_lbl))
                    ):
                        matched_val = v
                        break
                if matched_val is not None:
                    item._value = str(matched_val)
                    try:
                        item.default = str(matched_val)
                    except Exception:
                        pass

        target_channel = channel or self.default_channel
        interaction = SyntheticInteraction(
            client=self.env.bot,
            guild=self.guild,
            channel=target_channel,
            user=self.user,
            interaction_type=discord.InteractionType.modal_submit,
            data={"components": []},
        )

        await modal.on_submit(interaction)
        return interaction.captured
