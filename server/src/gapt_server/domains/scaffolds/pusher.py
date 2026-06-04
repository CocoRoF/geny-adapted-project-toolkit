"""Phase N.2.4 — write rendered scaffold files to a tempdir + git push.

Why subprocess git rather than libgit2:
  * git CLI is already in every container we ship + dev host.
  * one tempdir per call, GC'd by the kernel — no long-lived bare
    repo to keep clean.
  * push semantics (HTTP basic via x-access-token URL) are well-trodden;
    libgit2's auth callback dance is more surface area for the same
    outcome.

The token shows up in the remote URL exactly once. We never log the
URL — only the redacted form ``https://x-access-token:***@github.com/...``.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

import structlog

from gapt_server.domains.scaffolds.errors import ScaffoldError, ScaffoldErrorCode

logger = structlog.get_logger(__name__)

# git author for the initial scaffold commit. The wizard-created repo
# always starts with one commit credited to "GAPT scaffold". Every
# subsequent commit (from the workspace agent) uses the operator's
# normal git identity inside the sandbox.
_SCAFFOLD_AUTHOR_NAME = "GAPT scaffold"
_SCAFFOLD_AUTHOR_EMAIL = "scaffold@gapt.local"


def _redact_token(url: str) -> str:
    """Replace the embedded `x-access-token:<TOKEN>` payload so logs
    don't leak the secret. Pattern handles the only shape we use —
    `https://x-access-token:<TOKEN>@github.com/...`."""
    parts = url.split("@", 1)
    if len(parts) != 2:
        return url
    scheme_user, host_path = parts
    # scheme_user is `https://x-access-token:<TOKEN>` — replace post-colon.
    head, _, _tail = scheme_user.rpartition(":")
    return f"{head}:***@{host_path}"


def _build_remote_url(clone_url: str, token: str) -> str:
    """Embed the PAT into the clone URL so `git push` over HTTPS
    authenticates without an interactive credential helper."""
    # clone_url is canonical `https://github.com/<owner>/<name>.git`.
    if "://" not in clone_url:
        raise ScaffoldError(
            ScaffoldErrorCode.PUSH_FAILED,
            f"unexpected clone_url shape: {clone_url!r}",
        )
    scheme, rest = clone_url.split("://", 1)
    return f"{scheme}://x-access-token:{token}@{rest}"


async def _run(
    args: list[str], *, cwd: Path, env: dict[str, str] | None = None
) -> tuple[int, str, str]:
    """Run a subprocess + capture stdout/stderr. Returns
    ``(returncode, stdout, stderr)``."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )


def _write_files(root: Path, files: dict[str, bytes]) -> None:
    """Materialise the in-memory file tree under ``root``. Parent
    directories are created as needed."""
    for relpath, content in files.items():
        target = root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


async def push_scaffold(
    *,
    clone_url: str,
    token: str,
    files: dict[str, bytes],
    branch: str,
    commit_message: str,
) -> str:
    """Push the rendered files as the initial commit on ``branch``.

    Returns the freshly-created commit SHA. Raises
    ``ScaffoldError(PUSH_FAILED)`` on any subprocess failure with the
    captured stderr summarised — the token is never echoed.

    The clone_url + token are merged into the remote URL ephemerally;
    we never write it to disk except through `git remote add`, which
    stores it in the tempdir's `.git/config` (deleted on cleanup).
    """
    if not files:
        raise ScaffoldError(
            ScaffoldErrorCode.RENDER_FAILED,
            "preset render returned no files; refusing to push an empty tree",
        )
    if not branch:
        branch = "main"

    remote_url = _build_remote_url(clone_url, token)
    redacted = _redact_token(remote_url)

    workdir = Path(tempfile.mkdtemp(prefix="gapt-scaffold-"))
    logger.info("scaffold.push.begin", workdir=str(workdir), branch=branch, remote=redacted)

    # Don't let the host's `~/.gitconfig` leak through (e.g. a `user.email`
    # the operator set globally would override our scaffold identity). We
    # also disable any per-user / per-system git config so the subprocess
    # is reproducible.
    env = {
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": str(workdir),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }

    try:
        # 1. init + identity + branch
        for cmd in (
            ["git", "init", "-b", branch],
            ["git", "config", "user.email", _SCAFFOLD_AUTHOR_EMAIL],
            ["git", "config", "user.name", _SCAFFOLD_AUTHOR_NAME],
            ["git", "config", "commit.gpgsign", "false"],
        ):
            rc, _out, err = await _run(cmd, cwd=workdir, env=env)
            if rc != 0:
                raise ScaffoldError(
                    ScaffoldErrorCode.PUSH_FAILED,
                    f"`{' '.join(cmd)}` failed: {err[:300]}",
                )

        # 2. materialise the scaffold tree.
        _write_files(workdir, files)

        # 3. add + commit
        rc, _out, err = await _run(["git", "add", "-A"], cwd=workdir, env=env)
        if rc != 0:
            raise ScaffoldError(
                ScaffoldErrorCode.PUSH_FAILED, f"git add failed: {err[:300]}"
            )
        rc, _out, err = await _run(
            ["git", "commit", "-m", commit_message], cwd=workdir, env=env
        )
        if rc != 0:
            raise ScaffoldError(
                ScaffoldErrorCode.PUSH_FAILED, f"git commit failed: {err[:300]}"
            )
        rc, sha, err = await _run(
            ["git", "rev-parse", "HEAD"], cwd=workdir, env=env
        )
        if rc != 0:
            raise ScaffoldError(
                ScaffoldErrorCode.PUSH_FAILED, f"rev-parse failed: {err[:300]}"
            )
        commit_sha = sha.strip()

        # 4. remote add + push. The remote URL embeds the PAT.
        rc, _out, err = await _run(
            ["git", "remote", "add", "origin", remote_url], cwd=workdir, env=env
        )
        if rc != 0:
            raise ScaffoldError(
                ScaffoldErrorCode.PUSH_FAILED, f"remote add failed: {err[:300]}"
            )
        rc, _out, err = await _run(
            ["git", "push", "-u", "origin", branch], cwd=workdir, env=env
        )
        if rc != 0:
            # Scrub stderr in case git mirrors the URL.
            safe_err = err.replace(token, "***")[:400]
            raise ScaffoldError(
                ScaffoldErrorCode.PUSH_FAILED,
                f"git push failed: {safe_err}",
            )
        logger.info(
            "scaffold.push.done",
            branch=branch,
            commit_sha=commit_sha,
            files_pushed=len(files),
        )
        return commit_sha
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
