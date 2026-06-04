"""Phase N.2.1 — GithubClient unit tests via httpx.MockTransport.

The client never reaches the public network in tests. Each test wires
a route handler returning the documented GitHub responses so we lock
the parser to real API shapes (not what we wish they looked like)."""

from __future__ import annotations

import httpx
import pytest

from gapt_server.domains.scaffolds.errors import ScaffoldErrorCode, ScaffoldError
from gapt_server.domains.scaffolds.github_client import GithubClient


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


@pytest.mark.asyncio
async def test_get_user_returns_login_on_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/user"
        assert request.headers.get("Authorization") == "Bearer dummy"
        return httpx.Response(200, json={"login": "octocat", "id": 1})

    async with GithubClient("dummy", client=_mock_client(handler)) as gh:
        data = await gh.get_user()
    assert data["login"] == "octocat"


@pytest.mark.asyncio
async def test_get_user_401_raises_token_invalid() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    async with GithubClient("expired", client=_mock_client(handler)) as gh:
        with pytest.raises(ScaffoldError) as exc:
            await gh.get_user()
    assert exc.value.code is ScaffoldErrorCode.TOKEN_INVALID


@pytest.mark.asyncio
async def test_get_scopes_parses_oauth_scopes_header() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"login": "octocat"},
            headers={"X-OAuth-Scopes": "repo, workflow, read:org"},
        )

    async with GithubClient("classic_pat", client=_mock_client(handler)) as gh:
        scopes = await gh.get_scopes()
    assert scopes == {"repo", "workflow", "read:org"}


@pytest.mark.asyncio
async def test_get_scopes_returns_empty_set_for_fine_grained_pat() -> None:
    """Fine-grained PATs don't echo the X-OAuth-Scopes header — the
    permissions live in a different matrix the REST API doesn't expose
    here. We treat absence as empty so the caller can reject + nudge
    the operator to switch to a classic PAT."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"login": "octocat"})

    async with GithubClient("fine_grained", client=_mock_client(handler)) as gh:
        scopes = await gh.get_scopes()
    assert scopes == set()


@pytest.mark.asyncio
async def test_repo_exists_true_on_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/octocat/hello"
        return httpx.Response(200, json={"name": "hello"})

    async with GithubClient("dummy", client=_mock_client(handler)) as gh:
        assert await gh.repo_exists("octocat", "hello") is True


@pytest.mark.asyncio
async def test_repo_exists_false_on_404() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    async with GithubClient("dummy", client=_mock_client(handler)) as gh:
        assert await gh.repo_exists("octocat", "nope") is False


@pytest.mark.asyncio
async def test_create_repo_returns_typed_info_on_201() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/user/repos"
        # We must send the documented payload shape.
        assert request.method == "POST"
        return httpx.Response(
            201,
            json={
                "name": "my-app",
                "full_name": "octocat/my-app",
                "html_url": "https://github.com/octocat/my-app",
                "clone_url": "https://github.com/octocat/my-app.git",
                "default_branch": "main",
                "private": True,
                "owner": {"login": "octocat"},
            },
        )

    async with GithubClient("dummy", client=_mock_client(handler)) as gh:
        info = await gh.create_repo(
            name="my-app", private=True, description="hello"
        )
    assert info.name == "my-app"
    assert info.full_name == "octocat/my-app"
    assert info.clone_url == "https://github.com/octocat/my-app.git"
    assert info.default_branch == "main"
    assert info.private is True


@pytest.mark.asyncio
async def test_create_repo_422_name_collision_maps_to_repo_exists() -> None:
    """GitHub's "name already exists on this account" comes as 422
    with an `errors` array. The client surfaces it as REPO_EXISTS so
    the front-end can highlight the repo_name field inline."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "message": "Validation Failed",
                "errors": [
                    {
                        "resource": "Repository",
                        "code": "custom",
                        "field": "name",
                        "message": "name already exists on this account",
                    }
                ],
            },
        )

    async with GithubClient("dummy", client=_mock_client(handler)) as gh:
        with pytest.raises(ScaffoldError) as exc:
            await gh.create_repo(name="dup", private=True, description="")
    assert exc.value.code is ScaffoldErrorCode.REPO_EXISTS


@pytest.mark.asyncio
async def test_delete_repo_204_is_silent_success() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(204)

    async with GithubClient("dummy", client=_mock_client(handler)) as gh:
        await gh.delete_repo("octocat", "my-app")
    assert calls == ["/repos/octocat/my-app"]


@pytest.mark.asyncio
async def test_delete_repo_403_swallowed_not_raised() -> None:
    """Rollback is best-effort — when the token can't delete (e.g.
    classic PAT without `delete_repo` granted, or fine-grained without
    Administration write), we DON'T want to mask the original
    push-failure error with a misleading delete error. Just log."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Must have admin rights"})

    async with GithubClient("dummy", client=_mock_client(handler)) as gh:
        # Should NOT raise — just log a warning.
        await gh.delete_repo("octocat", "my-app")


@pytest.mark.asyncio
async def test_empty_token_rejected_at_construction() -> None:
    with pytest.raises(ScaffoldError) as exc:
        GithubClient("   ")
    assert exc.value.code is ScaffoldErrorCode.TOKEN_MISSING
