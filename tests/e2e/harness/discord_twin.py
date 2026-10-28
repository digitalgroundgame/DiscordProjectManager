"""Stateful In-Memory Discord Server Twin for End-to-End Testing.

Implements faithful test doubles for discord.Guild, discord.ForumChannel,
discord.TextChannel, discord.Thread, discord.Message, discord.Member,
and discord.Role that satisfy all `isinstance(...)` checks across dgg-pm.
"""

from __future__ import annotations

from collections import namedtuple
from typing import Any

import discord

ThreadWithMessage = namedtuple("ThreadWithMessage", ["thread", "message"])


class SimulatedRole(discord.Role):
    """Stateful role test double."""

    def __init__(self, id: int, name: str, guild: SimulatedGuild | None = None):
        self._id = int(id)
        self._name = str(name)
        self.guild = guild

    @property
    def id(self) -> int:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @property
    def mention(self) -> str:
        return f"<@&{self._id}>"

    def __str__(self) -> str:
        return self._name

    def __repr__(self) -> str:
        return f"<SimulatedRole id={self._id} name={self._name!r}>"


class SimulatedMember(discord.Member):
    """Stateful guild member test double."""

    def __init__(
        self,
        id: int,
        name: str,
        guild: SimulatedGuild | None = None,
        roles: list[SimulatedRole] | None = None,
        is_admin: bool = False,
    ):
        self._id = int(id)
        self._name = str(name)
        self.guild = guild
        self._custom_roles: list[SimulatedRole] = list(roles or [])
        self._is_admin = is_admin
        self.dm_messages: list[SimulatedMessage] = []

    @property
    def id(self) -> int:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._name

    @property
    def mention(self) -> str:
        return f"<@{self._id}>"

    @property
    def roles(self) -> list[SimulatedRole]:
        return list(self._custom_roles)

    @roles.setter
    def roles(self, r: list[SimulatedRole]) -> None:
        self._custom_roles = list(r)

    def add_role(self, role: SimulatedRole) -> None:
        if role not in self._custom_roles:
            self._custom_roles.append(role)

    def remove_role(self, role: SimulatedRole) -> None:
        if role in self._custom_roles:
            self._custom_roles.remove(role)

    @property
    def guild_permissions(self) -> discord.Permissions:
        if self._is_admin:
            return discord.Permissions.all()
        return discord.Permissions(send_messages=True, view_channel=True, use_application_commands=True)

    async def send(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        embeds: list[discord.Embed] | None = None,
        view: discord.ui.View | None = None,
        **kwargs: Any,
    ) -> SimulatedMessage:
        """Captures private DM messages sent to this user."""
        all_embeds = list(embeds or [])
        if embed is not None and embed not in all_embeds:
            all_embeds.insert(0, embed)

        msg = SimulatedMessage(
            id=next_snowflake(),
            channel=None,
            guild=None,
            author=self,
            content=content or "",
            embeds=all_embeds,
            view=view,
        )
        self.dm_messages.append(msg)
        return msg

    def __str__(self) -> str:
        return self._name

    def __repr__(self) -> str:
        return f"<SimulatedMember id={self._id} name={self._name!r}>"


class SimulatedMessage(discord.Message):
    """Stateful message test double."""

    def __init__(
        self,
        id: int,
        channel: discord.abc.GuildChannel | discord.Thread | None,
        guild: SimulatedGuild | None,
        author: SimulatedMember | None = None,
        content: str = "",
        embeds: list[discord.Embed] | None = None,
        view: discord.ui.View | None = None,
        pinned: bool = False,
    ):
        self._id = int(id)
        self.channel = channel
        self.guild = guild
        self.author = author
        self.content = content
        self.embeds = list(embeds or [])
        self.view = view
        self.attachments: list[Any] = []
        self.pinned = pinned
        self.attachments = []

    @property
    def id(self) -> int:
        return self._id

    @property
    def embed(self) -> discord.Embed | None:
        return self.embeds[0] if self.embeds else None

    @property
    def jump_url(self) -> str:
        guild_id = self.guild.id if self.guild else 0
        chan_id = self.channel.id if self.channel else 0
        return f"https://discord.com/channels/{guild_id}/{chan_id}/{self._id}"

    async def edit(
        self,
        *,
        content: str | None = None,
        embed: discord.Embed | None = None,
        embeds: list[discord.Embed] | None = None,
        view: discord.ui.View | None = None,
        attachments: list[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if content is not None:
            self.content = content
        if embeds is not None:
            self.embeds = list(embeds)
        elif embed is not None:
            self.embeds = [embed]
        if view is not None:
            self.view = view
        if attachments is not None:
            self.attachments = list(attachments)

    async def pin(self, *, reason: str | None = None) -> None:
        self.pinned = True

    async def unpin(self, *, reason: str | None = None) -> None:
        self.pinned = False

    async def delete(self, *, delay: float | None = None) -> None:
        if isinstance(self.channel, (SimulatedThread, SimulatedTextChannel)):
            if self in self.channel.messages:
                self.channel.messages.remove(self)

    def __repr__(self) -> str:
        return f"<SimulatedMessage id={self._id} content={self.content[:30]!r}>"


class SimulatedThread(discord.Thread):
    """Stateful Discord Forum Thread test double."""

    def __init__(
        self,
        id: int,
        name: str,
        guild: SimulatedGuild,
        parent: SimulatedForumChannel | SimulatedTextChannel | None = None,
        applied_tags: list[discord.ForumTag] | None = None,
        starter_message: SimulatedMessage | None = None,
        auto_archive_duration: int = 10080,
    ):
        self._state = None
        self.guild = guild
        self.id = int(id)
        self.name = str(name)
        self._custom_parent = parent
        self.parent_id = parent.id if parent else None
        self._applied_tags_list: list[discord.ForumTag] = list(applied_tags or [])
        self.archived = False
        self.locked = False
        self.auto_archive_duration = auto_archive_duration
        self._starter_message = starter_message
        self.messages: list[SimulatedMessage] = []
        self._type = 11

    @property
    def parent(self) -> SimulatedForumChannel | SimulatedTextChannel | None:
        return self._custom_parent

    @parent.setter
    def parent(self, p: SimulatedForumChannel | SimulatedTextChannel | None) -> None:
        self._custom_parent = p
        self.parent_id = p.id if p else None

    @property
    def starter_message(self) -> SimulatedMessage | None:
        return self._starter_message

    @starter_message.setter
    def starter_message(self, msg: SimulatedMessage | None) -> None:
        self._starter_message = msg

    @property
    def applied_tags(self) -> list[discord.ForumTag]:
        return list(self._applied_tags_list)

    @applied_tags.setter
    def applied_tags(self, tags: list[discord.ForumTag]) -> None:
        self._applied_tags_list = list(tags)

    @property
    def jump_url(self) -> str:
        return f"https://discord.com/channels/{self.guild.id}/{self.id}"

    async def edit(
        self,
        *,
        name: str | None = None,
        archived: bool | None = None,
        locked: bool | None = None,
        applied_tags: list[discord.ForumTag] | None = None,
        pinned: bool | None = None,
        **kwargs: Any,
    ) -> None:
        if name is not None:
            self.name = name
        if archived is not None:
            self.archived = archived
        if locked is not None:
            self.locked = locked
        if applied_tags is not None:
            self.applied_tags = list(applied_tags)

    async def pin(self, *, reason: str | None = None) -> None:
        self.pinned = True

    async def send(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        embeds: list[discord.Embed] | None = None,
        view: discord.ui.View | None = None,
        file: discord.File | None = None,
        files: list[discord.File] | None = None,
        **kwargs: Any,
    ) -> SimulatedMessage:
        all_embeds = list(embeds or [])
        if embed is not None and embed not in all_embeds:
            all_embeds.insert(0, embed)

        msg = SimulatedMessage(
            id=next_snowflake(),
            channel=self,
            guild=self.guild,
            content=content or "",
            embeds=all_embeds,
            view=view,
        )
        if file:
            msg.attachments.append(file)
        if files:
            msg.attachments.extend(files)

        self.messages.append(msg)
        self.guild._register_message(msg)
        return msg

    async def fetch_message(self, message_id: int) -> SimulatedMessage:
        if self._starter_message and self._starter_message.id == message_id:
            return self._starter_message
        for m in self.messages:
            if m.id == message_id:
                return m
        msg = self.guild.get_message(message_id)
        if msg:
            return msg
        raise discord.NotFound(None, f"Message {message_id} not found in thread {self.id}")

    def __repr__(self) -> str:
        return f"<SimulatedThread id={self.id} name={self.name!r} archived={self.archived}>"


class SimulatedForumChannel(discord.ForumChannel):
    """Stateful Discord ForumChannel test double."""

    def __init__(self, id: int, name: str, guild: SimulatedGuild):
        self._state = None
        self.id = int(id)
        self.name = str(name)
        self.guild = guild
        self._type = 15
        self._available_tags: list[discord.ForumTag] = []
        self._threads: list[SimulatedThread] = []

    @property
    def available_tags(self) -> list[discord.ForumTag]:
        return list(self._available_tags)

    @available_tags.setter
    def available_tags(self, tags: list[discord.ForumTag]) -> None:
        processed: list[discord.ForumTag] = []
        for tag in tags:
            if getattr(tag, "id", 0) == 0:
                tag.id = next_snowflake()
            processed.append(tag)
        self._available_tags = processed

    @property
    def threads(self) -> list[SimulatedThread]:
        return list(self._threads)

    def active_threads(self) -> list[SimulatedThread]:
        return [t for t in self._threads if not t.archived]

    async def edit(
        self,
        *,
        name: str | None = None,
        available_tags: list[discord.ForumTag] | None = None,
        **kwargs: Any,
    ) -> None:
        if name is not None:
            self.name = name
        if available_tags is not None:
            self.available_tags = list(available_tags)

    async def create_thread(
        self,
        *,
        name: str,
        content: str | None = None,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
        applied_tags: list[discord.ForumTag] | None = None,
        auto_archive_duration: int = 10080,
        **kwargs: Any,
    ) -> ThreadWithMessage:
        thread_id = next_snowflake()
        msg_id = next_snowflake()

        thread = SimulatedThread(
            id=thread_id,
            name=name,
            guild=self.guild,
            parent=self,
            applied_tags=applied_tags,
            auto_archive_duration=auto_archive_duration,
        )

        all_embeds = [embed] if embed is not None else []
        starter_msg = SimulatedMessage(
            id=msg_id,
            channel=thread,
            guild=self.guild,
            content=content or "",
            embeds=all_embeds,
            view=view,
        )
        thread.starter_message = starter_msg
        thread.messages.append(starter_msg)

        self._threads.append(thread)
        self.guild._register_channel(thread)
        self.guild._register_message(starter_msg)

        return ThreadWithMessage(thread=thread, message=starter_msg)

    def get_thread(self, thread_id: int) -> SimulatedThread | None:
        for t in self._threads:
            if t.id == thread_id:
                return t
        return None

    def get_thread_by_name(self, name_substr: str) -> SimulatedThread | None:
        for t in self._threads:
            if name_substr in t.name:
                return t
        return None

    def __repr__(self) -> str:
        return f"<SimulatedForumChannel id={self.id} name={self.name!r}>"


class SimulatedTextChannel(discord.TextChannel):
    """Stateful Discord TextChannel test double."""

    def __init__(self, id: int, name: str, guild: SimulatedGuild):
        self._state = None
        self.id = int(id)
        self.name = str(name)
        self.guild = guild
        self._type = 0
        self.messages: list[SimulatedMessage] = []
        self._threads: list[SimulatedThread] = []

    async def send(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        embeds: list[discord.Embed] | None = None,
        view: discord.ui.View | None = None,
        file: discord.File | None = None,
        files: list[discord.File] | None = None,
        **kwargs: Any,
    ) -> SimulatedMessage:
        all_embeds = list(embeds or [])
        if embed is not None and embed not in all_embeds:
            all_embeds.insert(0, embed)

        msg = SimulatedMessage(
            id=next_snowflake(),
            channel=self,
            guild=self.guild,
            content=content or "",
            embeds=all_embeds,
            view=view,
        )
        if file:
            msg.attachments.append(file)
        if files:
            msg.attachments.extend(files)

        self.messages.append(msg)
        self.guild._register_message(msg)
        return msg


class SimulatedGuild(discord.Guild):
    """Stateful Discord Guild (Server) test double."""

    def __init__(self, id: int = 1543430283250901023, name: str = "Test PM Server"):
        self._id = int(id)
        self._name = str(name)
        self._channels: dict[int, discord.abc.GuildChannel | discord.Thread] = {}
        self._members: dict[int, SimulatedMember] = {}
        self._roles: dict[int, SimulatedRole] = {}
        self._messages: dict[int, SimulatedMessage] = {}

    @property
    def id(self) -> int:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @property
    def channels(self) -> list[discord.abc.GuildChannel | discord.Thread]:
        return list(self._channels.values())

    @property
    def members(self) -> list[SimulatedMember]:
        return list(self._members.values())

    @property
    def roles(self) -> list[SimulatedRole]:
        return list(self._roles.values())

    def _register_channel(self, channel: discord.abc.GuildChannel | discord.Thread) -> None:
        self._channels[channel.id] = channel

    def _register_message(self, message: SimulatedMessage) -> None:
        self._messages[message.id] = message

    def get_channel(self, channel_id: int) -> discord.abc.GuildChannel | discord.Thread | None:
        return self._channels.get(channel_id)

    async def fetch_channel(self, channel_id: int) -> discord.abc.GuildChannel | discord.Thread:
        ch = self.get_channel(channel_id)
        if ch is None:
            raise discord.NotFound(None, f"Channel {channel_id} not found")
        return ch

    def get_member(self, member_id: int) -> SimulatedMember | None:
        return self._members.get(member_id)

    async def fetch_member(self, member_id: int) -> SimulatedMember:
        m = self.get_member(member_id)
        if m is None:
            raise discord.NotFound(None, f"Member {member_id} not found")
        return m

    def get_role(self, role_id: int) -> SimulatedRole | None:
        return self._roles.get(role_id)

    def create_role(self, name: str, role_id: int | None = None) -> SimulatedRole:
        rid = role_id or next_snowflake()
        role = SimulatedRole(id=rid, name=name, guild=self)
        self._roles[rid] = role
        return role

    def create_forum_channel(self, name: str, channel_id: int | None = None) -> SimulatedForumChannel:
        cid = channel_id or next_snowflake()
        forum = SimulatedForumChannel(id=cid, name=name, guild=self)
        self._channels[cid] = forum
        return forum

    def create_text_channel(self, name: str, channel_id: int | None = None) -> SimulatedTextChannel:
        cid = channel_id or next_snowflake()
        text = SimulatedTextChannel(id=cid, name=name, guild=self)
        self._channels[cid] = text
        return text

    def create_member(
        self,
        name: str,
        user_id: int | None = None,
        roles: list[SimulatedRole] | None = None,
        is_admin: bool = False,
    ) -> SimulatedMember:
        uid = user_id or next_snowflake()
        member = SimulatedMember(
            id=uid,
            name=name,
            guild=self,
            roles=roles,
            is_admin=is_admin,
        )
        self._members[uid] = member
        return member

    def get_message(self, message_id: int) -> SimulatedMessage | None:
        return self._messages.get(message_id)

    def __repr__(self) -> str:
        return f"<SimulatedGuild id={self._id} name={self._name!r}>"


_snowflake_counter = 100000000000000000


def next_snowflake() -> int:
    """Generates sequential, unique 64-bit Discord snowflake IDs."""
    global _snowflake_counter
    _snowflake_counter += 1
    return _snowflake_counter
