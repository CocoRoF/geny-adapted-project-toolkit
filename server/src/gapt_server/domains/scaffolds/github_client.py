"""Phase N — thin GitHub REST client for the scaffold pipeline.

Only the endpoints we need today. Each call lifts httpx errors into
``ScaffoldError`` so the upstream router can map to a stable HTTP code
without inspecting httpx internals.

The client carries a single token + a single httpx.AsyncClient. Inject
a custom client (test fixtures use ``httpx.MockTransport``) — when
``client`` is None we lazily build one with sane timeouts.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import structlog

from gapt_server.domains.scaffolds.errors import ScaffoldError, ScaffoldErrorCode

logger = structlog.get_logger(__name__)

# GitHub recommends ``2022-11-28`` as the stable REST surface as of the
# 2025 docs; the `User-Agent` is required (anonymous calls get 403).
_DEFAULT_API_BASE = "https://api.github.com"
_DEFAULT_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "GAPT-scaffold/1.0",
}


@dataclass(frozen=True)
class GithubRepoInfo:
    """The slice of the create-repo response we need downstream."""

    name: str
    full_name: str  # "owner/name"
    html_url: str
    clone_url: str
    default_branch: str
    private: bool


class GithubClient:
    """Thin async wrapper around the GitHub REST endpoints we use.

    Methods raise ``ScaffoldError`` with one of ``ScaffoldErrorCode``
    on any non-2xx (or transport) failure. Successful calls return
    typed payloads — never the raw dict — so changes to the GitHub
    schema fail at the boundary instead of leaking through.
    """

    def __init__(
        self,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        api_base: str = _DEFAULT_API_BASE,
    ) -> None:
        if not token or not token.strip():
            raise ScaffoldError(
                ScaffoldErrorCode.TOKEN_MISSING,
                "empty GitHub token passed to client",
            )
        self._token = token.strip()
        self._api_base = api_base.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=15.0)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> GithubClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    # ──────────────────────────────────────────────────── private ──

    def _headers(self) -> dict[str, str]:
        return {
            **_DEFAULT_HEADERS,
            "Authorization": f"Bearer {self._token}",
        }

    async def _request(
        self, method: str, path: str, **kwargs: object
    ) -> httpx.Response:
        url = f"{self._api_base}{path}"
        try:
            return await self._client.request(
                method, url, headers=self._headers(), **kwargs
            )
        except httpx.HTTPError as exc:
            raise ScaffoldError(
                ScaffoldErrorCode.CREATE_FAILED,
                f"github transport error: {exc!s}",
            ) from exc

    # ──────────────────────────────────────────────────────── API ──

    async def get_user(self) -> dict[str, object]:
        """``GET /user``. Returns the authenticated user payload.

        Used to:
          * confirm the token is valid (401 → TOKEN_INVALID)
          * extract the owner login for subsequent calls (``data["login"]``)
        """
        resp = await self._request("GET", "/user")
        if resp.status_code == 401:
            raise ScaffoldError(
                ScaffoldErrorCode.TOKEN_INVALID,
                "GitHub rejected the token (401). Re-issue in Settings → Credentials.",
            )
        if resp.status_code != 200:
            raise ScaffoldError(
                ScaffoldErrorCode.USER_FETCH_FAILED,
                f"GET /user → {resp.status_code}: {resp.text[:200]}",
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise ScaffoldError(
                ScaffoldErrorCode.USER_FETCH_FAILED,
                f"GET /user returned non-JSON body: {exc!s}",
            ) from exc

    async def get_scopes(self) -> set[str]:
        """Return the OAuth scopes the token carries.

        Reads ``X-OAuth-Scopes`` from a lightweight ``GET /user`` —
        present on classic PATs + OAuth Device Flow tokens. Fine-grained
        PATs DON'T return this header (their permissions are matrix-
        based, not scope-based); we treat the absence as "fine-grained"
        and surface it to the caller as an empty set.

        The caller is expected to compare against the required set
        (e.g. ``{"repo"}`` or ``{"public_repo"}``) and emit
        TOKEN_SCOPE_INSUFFICIENT when the intersection is empty.
        """
        resp = await self._request("GET", "/user")
        if resp.status_code == 401:
            raise ScaffoldError(
                ScaffoldErrorCode.TOKEN_INVALID,
                "GitHub rejected the token (401).",
            )
        raw = resp.headers.get("X-OAuth-Scopes", "")
        return {s.strip() for s in raw.split(",") if s.strip()}

    async def repo_exists(self, owner: str, name: str) -> bool:
        """``GET /repos/{owner}/{name}`` — true on 200, false on 404.

        404 is the documented "doesn't exist OR you can't see it"
        response. For the scaffold flow we treat "can't see" as
        "doesn't exist" because the operator can't push to it either,
        which means the next ``create_repo`` would 422 anyway.
        """
        resp = await self._request("GET", f"/repos/{owner}/{name}")
        if resp.status_code == 200:
            return True
        if resp.status_code == 404:
            return False
        raise ScaffoldError(
            ScaffoldErrorCode.CREATE_FAILED,
            f"GET /repos/{owner}/{name} → {resp.status_code}: {resp.text[:200]}",
        )

    async def create_repo(
        self,
        *,
        name: str,
        private: bool,
        description: str,
        auto_init: bool = False,
    ) -> GithubRepoInfo:
        """``POST /user/repos`` — creates a repo under the authenticated user.

        ``auto_init=False`` because we push our own README + scaffold
        as the initial commit; GitHub's auto_init would race with that
        and end up with two parallel histories.

        422 with ``name`` validation error → REPO_EXISTS (the most
        common 422 cause). Any other 422 surfaces as CREATE_FAILED
        with the parsed message.
        """
        payload = {
            "name": name,
            "description": description[:350],  # GitHub caps at 350
            "private": private,
            "auto_init": auto_init,
            "has_issues": True,
            "has_projects": False,
            "has_wiki": False,
        }
        resp = await self._request("POST", "/user/repos", json=payload)
        if resp.status_code == 422:
            try:
                detail = resp.json()
            except ValueError:
                detail = {"message": resp.text[:200]}
            errors = detail.get("errors") if isinstance(detail, dict) else None
            if isinstance(errors, list):
                for err in errors:
                    if isinstance(err, dict) and err.get("field") == "name":
                        # GitHub uses message "name already exists on this account"
                        raise ScaffoldError(
                            ScaffoldErrorCode.REPO_EXISTS,
                            str(err.get("message") or "repository name already exists"),
                        )
            raise ScaffoldError(
                ScaffoldErrorCode.CREATE_FAILED,
                f"POST /user/repos 422: {detail}",
            )
        if resp.status_code != 201:
            raise ScaffoldError(
                ScaffoldErrorCode.CREATE_FAILED,
                f"POST /user/repos → {resp.status_code}: {resp.text[:200]}",
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise ScaffoldError(
                ScaffoldErrorCode.CREATE_FAILED,
                f"create_repo returned non-JSON: {exc!s}",
            ) from exc
        logger.info(
            "github.repo_created",
            owner=data.get("owner", {}).get("login"),
            name=data.get("name"),
            private=data.get("private"),
        )
        return GithubRepoInfo(
            name=str(data["name"]),
            full_name=str(data["full_name"]),
            html_url=str(data["html_url"]),
            clone_url=str(data["clone_url"]),
            default_branch=str(data.get("default_branch") or "main"),
            private=bool(data.get("private", True)),
        )

    async def delete_repo(self, owner: str, name: str) -> None:
        """``DELETE /repos/{owner}/{name}`` — rollback after a failed push.

        Requires ``delete_repo`` scope. Classic PATs with `repo` scope
        DO include it; fine-grained PATs need the explicit
        "Administration" write permission. We log + swallow on
        permission failure so a "scaffold creation half-succeeded"
        case still bubbles up the original error, not a misleading
        rollback error.
        """
        resp = await self._request("DELETE", f"/repos/{owner}/{name}")
        if resp.status_code == 204:
            logger.info("github.repo_deleted", owner=owner, name=name)
            return
        if resp.status_code == 403:
            logger.warning(
                "github.repo_delete_forbidden",
                owner=owner,
                name=name,
                reason=resp.text[:200],
            )
            return
        logger.warning(
            "github.repo_delete_failed",
            owner=owner,
            name=name,
            status=resp.status_code,
            body=resp.text[:200],
        )
