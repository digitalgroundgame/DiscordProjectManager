"""Unit and integration tests for the Web Dashboard & Portal Overlay API."""

from __future__ import annotations

import pytest

from src.adapters.api.session import create_session_token, verify_session_token
from src.config import Settings
from src.domain.actor import ActorPrincipal
from src.ports.member_directory import MemberDirectoryPort


class FakeMemberDirectory(MemberDirectoryPort):
    """In-memory test double for MemberDirectoryPort."""

    def __init__(self) -> None:
        self.principals: dict[tuple[int, int], ActorPrincipal] = {}
        self.display_names: dict[tuple[int, int], str] = {}

    def register_member(
        self,
        guild_id: int,
        user_id: int,
        *,
        role_ids: set[int] | frozenset[int] = frozenset(),
        is_guild_admin: bool = False,
        display_name: str | None = None,
    ) -> ActorPrincipal:
        principal = ActorPrincipal(
            user_id=user_id,
            guild_id=guild_id,
            role_ids=frozenset(role_ids),
            is_guild_admin=is_guild_admin,
        )
        self.principals[(guild_id, user_id)] = principal
        self.display_names[(guild_id, user_id)] = display_name or f"Member-{user_id}"
        return principal

    async def get_member_principal(self, guild_id: int, user_id: int) -> ActorPrincipal | None:
        return self.principals.get((guild_id, user_id))

    async def get_member_display_name(self, guild_id: int, user_id: int) -> str | None:
        return self.display_names.get((guild_id, user_id))


def test_session_token_roundtrip_and_tamper_rejection() -> None:
    secret = "test-secret-key-123"
    payload = {"user_id": 42001, "username": "alice", "guild_id": 9001}
    token = create_session_token(payload, secret_key=secret, max_age=3600)

    decoded = verify_session_token(token, secret_key=secret)
    assert decoded is not None
    assert decoded["user_id"] == 42001
    assert decoded["username"] == "alice"

    # Tampered signature or wrong secret is rejected
    assert verify_session_token(token + "tamper", secret_key=secret) is None
    assert verify_session_token(token, secret_key="wrong-secret") is None

    # Expired token is rejected
    expired_token = create_session_token(payload, secret_key=secret, max_age=-10)
    assert verify_session_token(expired_token, secret_key=secret) is None


@pytest.mark.asyncio
async def test_get_current_principal_resolves_live_guild_member() -> None:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from src.adapters.api.dependencies import get_current_principal
    from src.adapters.api.session import SESSION_COOKIE_NAME

    app = FastAPI()
    guild_id = 777123
    secret = "unit-test-secret"
    app.state.settings = Settings(
        ENVIRONMENT="production",
        DISCORD_GUILD_ID=guild_id,
        SESSION_SECRET_KEY=secret,
    )
    directory = FakeMemberDirectory()
    directory.register_member(guild_id, 5001, role_ids={10, 20}, is_guild_admin=False)
    app.state.member_directory = directory

    @app.get("/test-principal")
    async def _route(principal: ActorPrincipal = pytest.importorskip("fastapi").Depends(get_current_principal)):
        return {
            "user_id": principal.user_id,
            "guild_id": principal.guild_id,
            "role_ids": sorted(principal.role_ids),
            "is_guild_admin": principal.is_guild_admin,
        }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Unauthenticated -> 401
        r_unauth = await client.get("/test-principal")
        assert r_unauth.status_code == 401

        # 2. Authenticated guild member -> 200 with live roles
        token = create_session_token({"user_id": 5001, "username": "bob"}, secret_key=secret)
        client.cookies.set(SESSION_COOKIE_NAME, token)
        r_ok = await client.get("/test-principal")
        assert r_ok.status_code == 200
        assert r_ok.json() == {
            "user_id": 5001,
            "guild_id": guild_id,
            "role_ids": [10, 20],
            "is_guild_admin": False,
        }

        # 3. Authenticated non-member -> 403
        outsider_token = create_session_token({"user_id": 9999, "username": "outsider"}, secret_key=secret)
        client.cookies.set(SESSION_COOKIE_NAME, outsider_token)
        r_forbidden = await client.get("/test-principal")
        assert r_forbidden.status_code == 403


class FakeDiscordOAuthClient:
    """In-memory test double for DiscordOAuthClientPort."""

    def __init__(self, user_payload: dict | None = None) -> None:
        self.user_payload = user_payload or {
            "id": "6001",
            "username": "charlie",
            "global_name": "Charlie Dev",
            "avatar": "abc123hash",
        }
        self.calls: list[dict] = []

    async def exchange_code_for_user(
        self,
        *,
        code: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> dict:
        self.calls.append(
            {
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            }
        )
        return dict(self.user_payload)


@pytest.mark.asyncio
async def test_auth_login_oauth_callback_me_and_logout(services) -> None:
    from httpx import ASGITransport, AsyncClient

    from src.adapters.api.app import api_app
    from src.adapters.api.session import SESSION_COOKIE_NAME
    from src.services.auth_service import AuthService

    guild_id = 888111
    secret = "oauth-test-secret"
    api_app.state.settings = Settings(
        ENVIRONMENT="production",
        DISCORD_CLIENT_ID="client-123",
        DISCORD_CLIENT_SECRET="secret-456",
        DISCORD_GUILD_ID=guild_id,
        PUBLIC_BASE_URL="https://pm.example.org",
        SESSION_SECRET_KEY=secret,
    )
    directory = FakeMemberDirectory()
    directory.register_member(
        guild_id,
        6001,
        role_ids={701, 702},
        is_guild_admin=True,
        display_name="Charlie (Guild Lead)",
    )
    api_app.state.member_directory = directory
    api_app.state.auth_service = AuthService(
        project_service=services["project"],
        squad_service=services["squad"],
        member_directory=directory,
        uow=services["uow"],
    )
    fake_oauth = FakeDiscordOAuthClient()
    api_app.state.oauth_client = fake_oauth

    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="https://pm.example.org") as client:
        # 1. /auth/login redirects to Discord OAuth2 authorize URL
        login_resp = await client.get("/auth/login", follow_redirects=False)
        assert login_resp.status_code == 302
        loc = login_resp.headers["location"]
        assert loc.startswith("https://discord.com/oauth2/authorize?")
        assert "client_id=client-123" in loc
        assert "scope=identify" in loc

        # 2. /auth/callback exchanges code, sets cookie, and redirects to /dashboard
        cb_resp = await client.get("/auth/callback?code=oauth-code-xyz", follow_redirects=False)
        assert cb_resp.status_code == 302
        assert cb_resp.headers["location"] == "/dashboard"
        assert SESSION_COOKIE_NAME in cb_resp.cookies
        assert len(fake_oauth.calls) == 1
        assert fake_oauth.calls[0]["code"] == "oauth-code-xyz"

        # 3. /api/me returns resolved guild profile and permissions
        client.cookies.set(SESSION_COOKIE_NAME, cb_resp.cookies[SESSION_COOKIE_NAME])
        me_resp = await client.get("/api/me")
        assert me_resp.status_code == 200
        me_data = me_resp.json()
        assert me_data["user_id"] == 6001
        assert me_data["display_name"] == "Charlie (Guild Lead)"
        assert me_data["role_ids"] == [701, 702]
        assert me_data["is_guild_admin"] is True

        # 4. Non-member attempting /api/me is denied with 403
        outsider_token = create_session_token({"user_id": 99999, "username": "outsider"}, secret_key=secret)
        client.cookies.set(SESSION_COOKIE_NAME, outsider_token)
        denied_resp = await client.get("/api/me")
        assert denied_resp.status_code == 403

        # 5. /auth/logout clears session cookie
        logout_resp = await client.get("/auth/logout", follow_redirects=False)
        assert logout_resp.status_code == 302
        assert logout_resp.headers["location"] == "/dashboard"


@pytest.mark.asyncio
async def test_auth_login_development_fallback(services) -> None:
    from httpx import ASGITransport, AsyncClient

    from src.adapters.api.app import api_app
    from src.adapters.api.session import SESSION_COOKIE_NAME

    guild_id = 888222
    api_app.state.settings = Settings(
        ENVIRONMENT="development",
        DISCORD_CLIENT_ID="",
        DISCORD_CLIENT_SECRET="",
        DISCORD_GUILD_ID=guild_id,
        SESSION_SECRET_KEY="dev-secret",
    )
    api_app.state.member_directory = FakeMemberDirectory()

    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        resp = await client.get("/auth/login?user_id=777&username=local-dev", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/dashboard"
        assert SESSION_COOKIE_NAME in resp.cookies

        client.cookies.set(SESSION_COOKIE_NAME, resp.cookies[SESSION_COOKIE_NAME])
        me_resp = await client.get("/api/me")
        assert me_resp.status_code == 200
        me_data = me_resp.json()
        assert me_data["user_id"] == 777
        assert me_data["username"] == "local-dev"
        assert me_data["is_dev_fallback"] is True
        assert me_data["is_guild_admin"] is True


@pytest.mark.asyncio
async def test_dashboard_projects_workspace_and_tech_tree_endpoints(services, repos) -> None:
    from datetime import UTC, datetime, timedelta

    from httpx import ASGITransport, AsyncClient

    from src.adapters.api.app import api_app
    from src.adapters.api.session import SESSION_COOKIE_NAME
    from src.domain.enums import PriorityLevel, TaskStatus
    from src.services.auth_service import AuthService

    guild_id = 909100
    secret = "dashboard-test-secret"
    squad_a_role_id = 3001
    squad_b_role_id = 3002
    member_a_id = 4001
    lead_user_id = 4009

    proj_srv = services["project"]
    task_srv = services["task"]
    squad_srv = services["squad"]
    ws_srv = services["workspace"]
    uow = services["uow"]

    directory = FakeMemberDirectory()
    directory.register_member(
        guild_id,
        member_a_id,
        role_ids={squad_a_role_id},
        is_guild_admin=False,
        display_name="Alice (Squad A)",
    )
    directory.register_member(
        guild_id,
        lead_user_id,
        role_ids=set(),
        is_guild_admin=False,
        display_name="Dana (Guild Lead)",
    )

    auth_srv = AuthService(
        project_service=proj_srv,
        squad_service=squad_srv,
        guild_lead_role_repo=repos["guild_lead_role"],
        guild_lead_user_repo=repos["guild_lead_user"],
        member_directory=directory,
        uow=uow,
    )
    await auth_srv.add_guild_lead_user(guild_id, lead_user_id)

    api_app.state.settings = Settings(
        ENVIRONMENT="production",
        DISCORD_GUILD_ID=guild_id,
        SESSION_SECRET_KEY=secret,
    )
    api_app.state.project_service = proj_srv
    api_app.state.task_service = task_srv
    api_app.state.squad_service = squad_srv
    api_app.state.workspace_service = ws_srv
    api_app.state.auth_service = auth_srv
    api_app.state.member_directory = directory

    # Create a Squad-A project and a Squad-B restricted project
    proj_a = await proj_srv.create_project(
        guild_id=guild_id,
        name="Alpha Platform",
        prefix="ALP",
        discord_channel_id=555001,
        discord_role_id=squad_a_role_id,
    )
    proj_b = await proj_srv.create_project(
        guild_id=guild_id,
        name="Beta Secret",
        prefix="BET",
        discord_channel_id=555002,
        discord_role_id=squad_b_role_id,
    )

    # Create tasks in Alpha Platform with dependencies and Discord jump IDs
    t1 = await task_srv.create_task(
        guild_id=guild_id,
        title="Setup Database",
        creator_discord_id=member_a_id,
        project_id=proj_a.id,
        assignee_discord_id=member_a_id,
        priority=PriorityLevel.HIGH,
    )
    await task_srv.update_status(t1.id, TaskStatus.COMPLETED, expected_version=t1.version, actor_discord_id=member_a_id)
    await task_srv.update_discord_message_ids(t1.id, discord_message_id=99001, discord_thread_id=88001)

    past_due = datetime.now(UTC) - timedelta(days=2)
    t2 = await task_srv.create_task(
        guild_id=guild_id,
        title="Build API Layer",
        creator_discord_id=member_a_id,
        project_id=proj_a.id,
        assignee_discord_id=member_a_id,
        due_at=past_due,
        metadata_json={"tags": ["backend", "api"]},
        prerequisite_short_ids=[t1.short_id],
    )
    await task_srv.create_task(
        guild_id=guild_id,
        title="Deploy Portal",
        creator_discord_id=member_a_id,
        project_id=proj_a.id,
        prerequisite_short_ids=[t2.short_id],
    )

    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Squad A member sees only Alpha Platform in /api/projects
        token_a = create_session_token({"user_id": member_a_id, "username": "alice"}, secret_key=secret)
        client.cookies.set(SESSION_COOKIE_NAME, token_a)

        projs_resp = await client.get("/api/projects")
        assert projs_resp.status_code == 200
        projs_list = projs_resp.json()["projects"]
        assert len(projs_list) == 1
        assert projs_list[0]["prefix"] == "ALP"
        assert projs_list[0]["discord_jump_url"] == f"https://discord.com/channels/{guild_id}/555001"
        assert projs_list[0]["discord_app_url"] == f"discord://-/channels/{guild_id}/555001"
        assert projs_list[0]["metrics"]["total_tasks"] == 3
        assert projs_list[0]["metrics"]["completed_tasks"] == 1
        assert projs_list[0]["metrics"]["overdue_tasks"] == 1

        # 2. Squad A member is denied (403) when requesting Beta Secret workspace or tech-tree
        forbidden_ws = await client.get(f"/api/projects/{proj_b.id}/workspace")
        assert forbidden_ws.status_code == 403
        forbidden_tt = await client.get(f"/api/projects/{proj_b.id}/tech-tree")
        assert forbidden_tt.status_code == 403

        # 3. Squad A member fetches Alpha Platform workspace
        ws_resp = await client.get(f"/api/projects/{proj_a.id}/workspace")
        assert ws_resp.status_code == 200
        ws_data = ws_resp.json()
        assert ws_data["project"]["name"] == "Alpha Platform"
        assert ws_data["filters"]["tags"] == ["api", "backend"]
        tasks_by_short = {t["short_id"]: t for t in ws_data["tasks"]}
        assert tasks_by_short["ALP-1"]["discord_jump_url"] == f"https://discord.com/channels/{guild_id}/88001/99001"
        assert tasks_by_short["ALP-1"]["discord_app_url"] == f"discord://-/channels/{guild_id}/88001/99001"
        assert tasks_by_short["ALP-1"]["has_dedicated_thread"] is True
        # ALP-2 has no thread yet, so it falls back to the Project Forum Channel URL
        assert tasks_by_short["ALP-2"]["discord_jump_url"] == f"https://discord.com/channels/{guild_id}/555001"
        assert tasks_by_short["ALP-2"]["is_overdue"] is True
        assert tasks_by_short["ALP-2"]["version"] >= 1

        # 4. Squad A member fetches Alpha Platform Tech Tree DAG
        tt_resp = await client.get(f"/api/projects/{proj_a.id}/tech-tree")
        assert tt_resp.status_code == 200
        tt_data = tt_resp.json()
        assert tt_data["metrics"]["total_tasks"] == 3
        assert tt_data["metrics"]["completed_tasks"] == 1
        assert tt_data["metrics"]["available_tasks"] == 1
        assert tt_data["metrics"]["locked_tasks"] == 1
        nodes_by_short = {n["short_id"]: n for n in tt_data["nodes"]}
        assert nodes_by_short["ALP-1"]["state"] == "complete"
        assert nodes_by_short["ALP-2"]["state"] == "available"
        assert nodes_by_short["ALP-3"]["state"] == "locked"
        assert len(tt_data["edges"]) == 2
        assert len(tt_data["bottlenecks"]) >= 1
        assert tt_data["bottlenecks"][0]["short_id"] == "ALP-2"

        # 5. Guild Lead user can view BOTH Alpha Platform and Beta Secret
        token_lead = create_session_token({"user_id": lead_user_id, "username": "dana"}, secret_key=secret)
        client.cookies.set(SESSION_COOKIE_NAME, token_lead)
        lead_projs = await client.get("/api/projects")
        assert lead_projs.status_code == 200
        assert {p["prefix"] for p in lead_projs.json()["projects"]} == {"ALP", "BET"}
        lead_beta_ws = await client.get(f"/api/projects/{proj_b.id}/workspace")
        assert lead_beta_ws.status_code == 200


@pytest.mark.asyncio
async def test_portal_dashboard_and_static_assets_routes() -> None:
    from httpx import ASGITransport, AsyncClient

    from src.adapters.api.app import api_app

    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Root redirects to /dashboard
        r_root = await client.get("/", follow_redirects=False)
        assert r_root.status_code == 307
        assert r_root.headers["location"] == "/dashboard"

        # 2. /dashboard serves the HTML portal with Tech Tree, Kanban, Matrix, and Inspector
        r_dash = await client.get("/dashboard")
        assert r_dash.status_code == 200
        assert "text/html" in r_dash.headers["content-type"]
        html = r_dash.text
        assert "Tech Tree DAG" in html
        assert "Kanban Board" in html
        assert "Table Matrix" in html
        assert "Open Task in Discord" in html

        # 3. Static CSS and JS assets are served with proper media types
        r_css = await client.get("/static/styles.css")
        assert r_css.status_code == 200
        assert "text/css" in r_css.headers["content-type"]

        r_js = await client.get("/static/app.js")
        assert r_js.status_code == 200
        assert "javascript" in r_js.headers["content-type"]
        assert "renderTechTree" in r_js.text
        assert "renderKanban" in r_js.text
        assert "renderMatrix" in r_js.text

        # 4. Path traversal or unknown asset returns 404
        r_missing = await client.get("/static/nonexistent.js")
        assert r_missing.status_code == 404
