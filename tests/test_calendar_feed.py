from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import discord
import httpx
import pytest

from src.adapters.api.app import api_app, get_calendar_service
from src.adapters.discord_bot.cogs.pm_cog import PmCog
from src.adapters.discord_bot.views.calendar_feed_view import CalendarFeedView
from src.domain.enums import PriorityLevel
from src.services.calendar_service import CalendarService


@pytest.mark.asyncio
async def test_calendar_token_repo_lifecycle(repos):
    token_repo = repos["calendar_token"]
    user_id = 123456789012345678

    # 1. Initially non-existent
    record = await token_repo.get_by_user_id(user_id)
    assert record is None

    # 2. Create initial token
    created = await token_repo.create_or_regenerate_token(user_id, "token-alpha-123")
    assert created.user_discord_id == user_id
    assert created.token == "token-alpha-123"

    # 3. Lookup by user_id and token
    fetched_user = await token_repo.get_by_user_id(user_id)
    assert fetched_user is not None
    assert fetched_user.token == "token-alpha-123"

    fetched_token = await token_repo.get_by_token("token-alpha-123")
    assert fetched_token is not None
    assert fetched_token.user_discord_id == user_id

    # 4. Regenerate existing token
    updated = await token_repo.create_or_regenerate_token(user_id, "token-beta-456")
    assert updated.token == "token-beta-456"

    old_lookup = await token_repo.get_by_token("token-alpha-123")
    assert old_lookup is None

    new_lookup = await token_repo.get_by_token("token-beta-456")
    assert new_lookup is not None

    # 5. Revoke token
    revoked = await token_repo.revoke_token(user_id)
    assert revoked is True

    revoked_again = await token_repo.revoke_token(user_id)
    assert revoked_again is False

    assert await token_repo.get_by_user_id(user_id) is None
    assert await token_repo.get_by_token("token-beta-456") is None


@pytest.mark.asyncio
async def test_calendar_service_feed_generation(services, repos):
    calendar_srv = services["calendar"]
    task_srv = services["task"]
    proj_srv = services["project"]
    user_id = 999888777666555444
    guild_id = 111222333444555666

    # 1. Create token
    token = await calendar_srv.get_or_create_token(user_id)
    assert token is not None
    assert len(token) > 20

    # Getting token again returns same token
    assert await calendar_srv.get_or_create_token(user_id) == token

    # 2. Setup project and tasks
    proj = await proj_srv.create_project(
        guild_id=guild_id,
        name="Backend Core",
        prefix="CORE",
    )

    now = datetime.now(UTC)
    due = now + timedelta(days=2)

    # Active task with due date (assigned to user)
    await task_srv.create_task(
        guild_id=guild_id,
        project_id=proj.id,
        title="Implement OAuth2 API",
        body="Set up OAuth2 endpoints and token verification.",
        creator_discord_id=111,
        assignee_discord_id=user_id,
        due_at=due,
        priority=PriorityLevel.HIGH,
    )

    # Active task without due date (assigned to user)
    await task_srv.create_task(
        guild_id=guild_id,
        project_id=proj.id,
        title="Refactor Cache Layer",
        creator_discord_id=111,
        assignee_discord_id=user_id,
        due_at=None,
    )

    # Task assigned to someone else
    await task_srv.create_task(
        guild_id=guild_id,
        project_id=proj.id,
        title="Unrelated DevOps Task",
        creator_discord_id=111,
        assignee_discord_id=555444333,
        due_at=due,
    )

    # 3. Test default event feed
    feed_event = await calendar_srv.get_calendar_feed(token, format_type="event")
    assert feed_event is not None
    assert "BEGIN:VCALENDAR" in feed_event
    assert "END:VCALENDAR" in feed_event
    assert "BEGIN:VEVENT" in feed_event
    assert "SUMMARY:[CORE-1] Implement OAuth2 API" in feed_event
    # Task without due date shouldn't appear as VEVENT
    assert "Refactor Cache Layer" not in feed_event
    # Unrelated task shouldn't appear
    assert "Unrelated DevOps Task" not in feed_event

    # 4. Test todo feed (?format=todo)
    feed_todo = await calendar_srv.get_calendar_feed(token, format_type="todo")
    assert feed_todo is not None
    assert "BEGIN:VTODO" in feed_todo
    assert "SUMMARY:Implement OAuth2 API" in feed_todo
    assert "SUMMARY:Refactor Cache Layer" in feed_todo

    # 5. Invalid token returns None
    assert await calendar_srv.get_calendar_feed("invalid-nonexistent-token") is None


def test_calendar_service_build_feed_urls():
    token = "xyz123_abc456"

    # Default localhost config
    urls = CalendarService.build_feed_urls(token, base_url="http://localhost:8000")
    assert urls["https_url"] == "http://localhost:8000/calendar/xyz123_abc456.ics"
    assert urls["webcal_url"] == "webcal://localhost:8000/calendar/xyz123_abc456.ics"
    assert urls["subscribe_url"] == "http://localhost:8000/calendar/xyz123_abc456/subscribe"
    assert "calendar.google.com" in urls["gcal_url"]
    assert urls["todo_https_url"] == "http://localhost:8000/calendar/xyz123_abc456.ics?format=todo"
    assert urls["todo_webcal_url"] == "webcal://localhost:8000/calendar/xyz123_abc456.ics?format=todo"
    assert urls["todo_subscribe_url"] == "http://localhost:8000/calendar/xyz123_abc456/subscribe?format=todo"
    assert "calendar.google.com" in urls["todo_gcal_url"]

    # Production https URL
    urls_prod = CalendarService.build_feed_urls(token, base_url="https://pm.example.com")
    assert urls_prod["https_url"] == "https://pm.example.com/calendar/xyz123_abc456.ics"
    assert urls_prod["webcal_url"] == "webcal://pm.example.com/calendar/xyz123_abc456.ics"
    assert urls_prod["subscribe_url"] == "https://pm.example.com/calendar/xyz123_abc456/subscribe"
    assert "calendar.google.com" in urls_prod["gcal_url"]


@pytest.mark.asyncio
async def test_fastapi_calendar_endpoint(services, db_session):
    calendar_srv = services["calendar"]
    task_srv = services["task"]
    proj_srv = services["project"]
    user_id = 444333222111
    guild_id = 999888777

    token = await calendar_srv.get_or_create_token(user_id)
    proj = await proj_srv.create_project(guild_id=guild_id, name="Infra", prefix="INF")
    due = datetime.now(UTC) + timedelta(days=1)
    await task_srv.create_task(
        guild_id=guild_id,
        project_id=proj.id,
        title="Provision TLS Certs",
        creator_discord_id=111,
        assignee_discord_id=user_id,
        due_at=due,
    )
    await db_session.commit()

    # Override get_calendar_service to use test calendar service
    api_app.dependency_overrides[get_calendar_service] = lambda: calendar_srv

    try:
        transport = httpx.ASGITransport(app=api_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Valid token request (.ics suffix)
            res = await client.get(f"/calendar/{token}.ics")
            assert res.status_code == 200
            assert "text/calendar" in res.headers["content-type"]
            assert "BEGIN:VCALENDAR" in res.text
            assert "BEGIN:VEVENT" in res.text
            assert "Provision TLS Certs" in res.text

            # 2. Valid token without .ics suffix
            res_no_suffix = await client.get(f"/calendar/{token}")
            assert res_no_suffix.status_code == 200
            assert "text/calendar" in res_no_suffix.headers["content-type"]

            # 3. Valid token with ?format=todo
            res_todo = await client.get(f"/calendar/{token}.ics?format=todo")
            assert res_todo.status_code == 200
            assert "BEGIN:VTODO" in res_todo.text

            # 4. Subscribe redirect endpoint (for Discord 1-click button compatibility)
            res_sub = await client.get(f"/calendar/{token}/subscribe", follow_redirects=False)
            assert res_sub.status_code == 307
            assert res_sub.headers["location"].startswith("webcal://")
            assert f"/calendar/{token}.ics" in res_sub.headers["location"]

            # 5. Subscribe redirect endpoint with ?format=todo
            res_sub_todo = await client.get(f"/calendar/{token}/subscribe?format=todo", follow_redirects=False)
            assert res_sub_todo.status_code == 307
            assert res_sub_todo.headers["location"].startswith("webcal://")
            assert "format=todo" in res_sub_todo.headers["location"]

            # 6. Subscribe endpoint fetched by Google Calendar bot directly serves 200 text/calendar
            res_sub_google = await client.get(
                f"/calendar/{token}/subscribe",
                headers={"user-agent": "Google-Calendar-Importer"},
            )
            assert res_sub_google.status_code == 200
            assert "text/calendar" in res_sub_google.headers["content-type"]
            assert "BEGIN:VCALENDAR" in res_sub_google.text

            # 7. Subscribe endpoint with ?format=gcal redirects to Google Calendar render URL
            res_sub_gcal = await client.get(f"/calendar/{token}/subscribe?format=gcal", follow_redirects=False)
            assert res_sub_gcal.status_code == 307
            assert "calendar.google.com" in res_sub_gcal.headers["location"]

            # 8. Subscribe redirect with invalid token returns 404
            res_sub_invalid = await client.get("/calendar/unknown-nonexistent-token/subscribe")
            assert res_sub_invalid.status_code == 404

            # 9. Invalid token returns 404
            res_invalid = await client.get("/calendar/unknown-nonexistent-token.ics")
            assert res_invalid.status_code == 404
            assert "Invalid or expired" in res_invalid.json()["detail"]
    finally:
        api_app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_discord_pm_calendar_command_and_view(services):
    calendar_srv = services["calendar"]
    bot = MagicMock()
    cog = PmCog(
        bot=bot,
        calendar_service=calendar_srv,
    )

    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(spec=discord.User)
    interaction.user.id = 777666555444
    interaction.user.display_name = "AliceDev"
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()

    # Invoke /pm calendar callback
    await cog.calendar.callback(cog, interaction)

    interaction.response.send_message.assert_called_once()
    _, kwargs = interaction.response.send_message.call_args
    assert kwargs.get("ephemeral") is True
    embed = kwargs.get("embed")
    assert embed is not None
    assert "Personal Calendar Feed" in embed.title
    assert "AliceDev" in embed.title

    view = kwargs.get("view")
    assert isinstance(view, CalendarFeedView)
    assert len(view.children) == 2  # Universal .ics link button + Regenerate button

    # Button URL must point directly to the universal HTTPS .ics feed
    sub_button = view.children[0]
    assert sub_button.url.startswith(("http://", "https://"))
    assert sub_button.url.endswith(".ics")

    # Test clicking regenerate button
    regen_interaction = MagicMock(spec=discord.Interaction)
    regen_interaction.user = interaction.user
    regen_interaction.response = MagicMock()
    regen_interaction.response.edit_message = AsyncMock()

    old_token = view.token
    await view._on_regenerate_clicked(regen_interaction)

    regen_interaction.response.edit_message.assert_called_once()
    assert view.token != old_token
    # Old token no longer resolves
    assert await calendar_srv.get_user_id_by_token(old_token) is None
    # New token resolves
    assert await calendar_srv.get_user_id_by_token(view.token) == interaction.user.id
