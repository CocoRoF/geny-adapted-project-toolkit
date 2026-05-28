"""Remote-branch listing for the workspace-creation modal.

`git ls-remote --symref <url> HEAD refs/heads/*` is fast enough
(typically <500ms even for big repos — no objects downloaded, just
ref discovery), but slow enough that we cache the result for a short
TTL so a quick close-and-reopen of the modal doesn't re-hit the
remote.

Auth reuse: we pipe the project's stored token through the same
`-c http.extraHeader=Authorization: Basic …` channel the bare-clone
path uses (see `domains.workspaces.service._github_basic_header`),
so the token never appears in argv / `ps`.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from dataclasses import dataclass


_GIT_BIN = shutil.which("git") or "/usr/bin/git"

# `git ls-remote` is a network call against the remote — we want to
# fail fast on bad URLs / dead hosts rather than blocking the modal.
_LS_REMOTE_TIMEOUT_S = 20.0

# How long a successful result stays warm. Long enough that the
# operator clicking "Create workspace" → "Cancel" → "Create workspace
# again" doesn't re-fetch, short enough that a freshly-pushed branch
# shows up within a minute without a manual refresh.
_CACHE_TTL_S = 60.0


@dataclass(frozen=True)
class RemoteBranches:
    """What we hand to the API layer. `head` is the symref target of
    HEAD (e.g. `"main"`); None if the remote refuses to advertise one
    (rare — most providers do)."""

    head: str | None
    branches: list[str]
    cached_at: float  # unix seconds — useful for the UI's "last fetched" hint


class RemoteBranchesError(RuntimeError):
    """Raised when ls-remote fails. The endpoint translates this into
    an HTTP error; the `reason` field is what the modal surfaces."""

    def __init__(self, reason: str, *, stderr: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.stderr = stderr


_cache: dict[str, RemoteBranches] = {}


def invalidate(project_id: str) -> None:
    """Drop the cached entry for one project. Called by the endpoint
    when the caller passes `refresh=true` so they can force a re-fetch
    (e.g. just-pushed branch isn't showing up)."""
    _cache.pop(project_id, None)


async def list_remote_branches(
    *,
    project_id: str,
    git_remote_url: str,
    github_token: str | None,
) -> RemoteBranches:
    """Return the heads + default-branch advertised by `git_remote_url`.

    Uses an in-memory TTL cache keyed by `project_id`. Two projects
    pointing at the same URL with different tokens each get their own
    cache slot, which is correct: a public clone's heads can differ
    from an authenticated view of the same URL in rare cases (forked
    workflows / private fork access).

    Raises `RemoteBranchesError` on any failure — the caller decides
    whether to surface that to the user or fall back to free-text.
    """
    cached = _cache.get(project_id)
    if cached is not None and (time.monotonic() - cached.cached_at) < _CACHE_TTL_S:
        return cached

    argv: list[str] = []
    if github_token:
        # Same Basic-auth shape the clone path uses. Importing the
        # private helper keeps the encoding (and any future tweaks)
        # in one place.
        from gapt_server.domains.workspaces.service import (  # noqa: PLC0415
            _github_basic_header,
        )

        argv += ["-c", f"http.extraHeader={_github_basic_header(github_token)}"]
    argv += ["ls-remote", "--symref", git_remote_url, "HEAD", "refs/heads/*"]

    proc = await asyncio.create_subprocess_exec(
        _GIT_BIN,
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            # Refuse to prompt — a bad URL or missing token should
            # error out immediately, not block on stdin.
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "/bin/true",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/tmp",
        },
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=_LS_REMOTE_TIMEOUT_S
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise RemoteBranchesError(
            f"ls-remote timed out after {_LS_REMOTE_TIMEOUT_S}s "
            f"against {git_remote_url!r}"
        ) from None

    if proc.returncode != 0:
        stderr = stderr_b.decode("utf-8", errors="replace")
        # Strip the inevitable `fatal: ` prefix so the modal's hint is
        # one short sentence instead of two.
        reason = stderr.strip().splitlines()[-1] if stderr.strip() else "ls-remote failed"
        raise RemoteBranchesError(reason, stderr=stderr)

    head, branches = _parse(stdout_b.decode("utf-8", errors="replace"))
    result = RemoteBranches(head=head, branches=branches, cached_at=time.monotonic())
    _cache[project_id] = result
    return result


def _parse(stdout: str) -> tuple[str | None, list[str]]:
    """Parse ls-remote output.

    With `--symref` the output looks like:

        ref: refs/heads/main\\tHEAD
        <sha>\\tHEAD
        <sha>\\trefs/heads/main
        <sha>\\trefs/heads/develop

    We pull the symref target as `head`, then every `refs/heads/<x>`
    line as a branch. Duplicates aren't possible in real output but
    `dict.fromkeys` makes the order deterministic in tests."""
    head: str | None = None
    branches: list[str] = []
    for line in stdout.splitlines():
        if line.startswith("ref: refs/heads/"):
            # `ref: refs/heads/main\tHEAD` → "main"
            target = line[len("ref: refs/heads/"):].split("\t", 1)[0].strip()
            if target:
                head = target
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        ref = parts[1].strip()
        if ref.startswith("refs/heads/"):
            branches.append(ref[len("refs/heads/"):])
    return head, list(dict.fromkeys(branches))
