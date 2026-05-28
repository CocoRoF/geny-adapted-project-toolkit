"""Per-provider health probes for the Settings → LLM Backends page.

Adapted from Geny's `llm_backends_controller._check_*` but rewired
to GAPT's `SecretVault` + single-admin secret model rather than
Geny's `CredentialBundleBuilder`. The shape (`ProviderHealth`) is
kept stable so the same frontend modal can render against either
backend with only an i18n catalog swap.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gapt_server.domains.secrets import SecretVault
    from sqlalchemy.ext.asyncio import AsyncSession


# Stable order — also drives the grid row order in the UI.
PROVIDER_LABELS: dict[str, str] = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "google": "Google Gemini",
    "vllm": "vLLM (self-host)",
    "claude_code_cli": "Claude Code (CLI)",
}


# Maps the `provider` slug to the *vault* key that stores its API
# key. `claude_code_cli` doesn't appear here — the CLI authenticates
# itself, the vault entry is the optional `claude_setup_token`.
_API_KEY_VAULT_NAMES: dict[str, str] = {
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "google": "google_api_key",
}


# Same mapping for the env-var fallback that geny_executor reads
# when no vault entry is found. UI surfaces these so the user knows
# which env var to set if they prefer that path.
_API_KEY_ENV_NAMES: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "vllm": "VLLM_API_KEY",
}


@dataclass(frozen=True)
class ProviderHealth:
    """One row of the health grid.

    `state` is the load-bearing field; the UI maps it to a colour
    band: `ok` (green), `missing` (neutral), `expired` (warn),
    `unreachable` (danger), `unknown` (subtle).
    """

    provider: str
    label: str
    kind: str  # "api" | "cli"
    state: str  # "ok" | "missing" | "expired" | "unreachable" | "unknown"
    detail: str
    env_var: str | None = None
    binary_path: str | None = None
    binary_version: str | None = None
    auth_method: str | None = None  # "api_key" | "subscription" | "setup_token"
    expires_at_ms: int | None = None


# ─────────────────────────────────────────── helpers ──


async def _run_cmd(argv: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
    """Run a short command + capture stdout/stderr. Returns
    `(rc, stdout, stderr)`. On timeout or spawn failure returns
    `(-1, "", error_text)`. Used by every CLI probe — caller doesn't
    have to know about asyncio plumbing."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        return (-1, "", str(e))
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return (-1, "", "command timed out")
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace").strip(),
        stderr.decode("utf-8", errors="replace").strip(),
    )


def _read_claude_oauth_expires_at_ms() -> int | None:
    """Return the OAuth `expiresAt` (ms epoch) from
    `~/.claude/.credentials.json`, or `None` when the file is
    missing / malformed / uses a different auth method.

    The CLI returns `loggedIn: true` whenever this file exists,
    even when the access token has actually expired. We cross-check
    against the wall clock to surface the real state instead of
    "logged in but every request 401s" — see the Geny 2026-05-18
    incident note in the source we ported from.
    """
    try:
        creds_path = Path(os.path.expanduser("~/.claude/.credentials.json"))
        if not creds_path.exists():
            return None
        data = json.loads(creds_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    oauth = (data or {}).get("claudeAiOauth") or {}
    raw = oauth.get("expiresAt")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def claude_binary_path() -> str | None:
    """Resolve the `claude` binary. Order: `CLAUDE_BIN` env →
    `CLAUDE_CODE_BINARY` env (Geny compat) → PATH lookup.

    Returns `None` when nothing on disk matches — the health card
    then renders an install hint."""
    for env_name in ("CLAUDE_BIN", "CLAUDE_CODE_BINARY"):
        override = os.environ.get(env_name, "").strip()
        if override and os.path.exists(override) and os.access(override, os.X_OK):
            return override
    return shutil.which("claude")


# ─────────────────────────────── per-provider probes ──


async def _check_api_provider(
    *,
    provider: str,
    vault_key: str,
    env_var: str,
    api_key: str | None,
) -> ProviderHealth:
    """Generic API-key provider check. Says `ok` when a key was
    resolved (from vault OR env), `missing` otherwise. We don't
    actually call the upstream API here — that's a separate "test
    connection" action the user explicitly opts into."""
    have = bool(api_key)
    return ProviderHealth(
        provider=provider,
        label=PROVIDER_LABELS[provider],
        kind="api",
        state="ok" if have else "missing",
        detail=(
            f"Vault key `{vault_key}` or env `{env_var}` present."
            if have
            else f"Set vault key `{vault_key}` or env `{env_var}` to enable."
        ),
        env_var=env_var,
        auth_method="api_key" if have else None,
    )


async def _check_vllm(*, base_url: str | None) -> ProviderHealth:
    have = bool(base_url)
    return ProviderHealth(
        provider="vllm",
        label=PROVIDER_LABELS["vllm"],
        kind="api",
        state="ok" if have else "missing",
        detail=(
            f"base_url={base_url}"
            if have
            else (
                "vLLM base URL not set. Paste an OpenAI-compatible "
                "endpoint to enable this provider."
            )
        ),
        env_var="VLLM_BASE_URL",
        auth_method="api_key" if have else None,
    )


async def _check_claude_code(*, anthropic_api_key: str | None) -> ProviderHealth:
    """The Claude Code CLI has three viable auth modes:

    1. `ANTHROPIC_API_KEY` in env (or vault). Treated as `ok`
       immediately — the CLI uses it via `apiKeyHelper` / direct.
    2. Subscription via `claude auth login`. Detected by querying
       `claude auth status --json` (we keep this lightweight).
    3. `claude setup-token` long-lived token. Same surface as #1
       once the env var is materialised by the executor.

    Mode #2's `loggedIn: true` is checked against the OAuth
    `expiresAt` to surface stale credentials.
    """
    label = PROVIDER_LABELS["claude_code_cli"]
    install_hint = (
        "Install Claude Code (https://docs.anthropic.com/claude/code) "
        "and ensure `claude` is on PATH. Then auth via this modal."
    )
    binary = claude_binary_path()
    if binary is None:
        return ProviderHealth(
            provider="claude_code_cli",
            label=label,
            kind="cli",
            state="missing",
            detail=install_hint,
        )

    version: str | None = None
    rc, out, _err = await _run_cmd([binary, "--version"], timeout=4.0)
    if rc == 0 and out:
        version = out.splitlines()[0].strip()

    # 1. API key path wins — fastest decision.
    if anthropic_api_key:
        return ProviderHealth(
            provider="claude_code_cli",
            label=label,
            kind="cli",
            state="ok",
            detail=f"CLI at {binary} · version={version or 'unknown'} · auth=api_key",
            binary_path=binary,
            binary_version=version,
            auth_method="api_key",
            env_var="ANTHROPIC_API_KEY",
        )

    # 2. Subscription path — `auth status` is fast + side-effect free.
    rc, out, _err = await _run_cmd([binary, "auth", "status", "--json"], timeout=3.0)
    if rc == 0 and out.strip():
        try:
            status = json.loads(out)
        except json.JSONDecodeError:
            status = {}
        logged_in = bool(status.get("loggedIn"))
        if logged_in:
            expires_at_ms = _read_claude_oauth_expires_at_ms()
            now_ms = int(time.time() * 1000)
            if expires_at_ms is not None and now_ms >= expires_at_ms:
                return ProviderHealth(
                    provider="claude_code_cli",
                    label=label,
                    kind="cli",
                    state="expired",
                    detail=(
                        "OAuth token expired. Re-run `claude auth login` "
                        "from the auth modal."
                    ),
                    binary_path=binary,
                    binary_version=version,
                    auth_method="subscription",
                    expires_at_ms=expires_at_ms,
                )
            return ProviderHealth(
                provider="claude_code_cli",
                label=label,
                kind="cli",
                state="ok",
                detail=(
                    f"CLI at {binary} · version={version or 'unknown'} · "
                    f"auth=subscription"
                    + (f" · type={status.get('subscriptionType')}"
                       if status.get("subscriptionType") else "")
                ),
                binary_path=binary,
                binary_version=version,
                auth_method="subscription",
                expires_at_ms=expires_at_ms,
            )

    # 3. Neither path available.
    return ProviderHealth(
        provider="claude_code_cli",
        label=label,
        kind="cli",
        state="missing",
        detail=(
            f"CLI at {binary} · version={version or 'unknown'} · "
            "no API key and no active subscription. "
            "Auth via the modal."
        ),
        binary_path=binary,
        binary_version=version,
    )


# ─────────────────────────────────────── public entry ──


async def collect_health(
    *,
    db: AsyncSession,
    vault: SecretVault,
    actor_id: str,
) -> list[ProviderHealth]:
    """Run every provider probe in parallel. Returned in stable
    `PROVIDER_LABELS` order so the frontend grid doesn't reflow on
    each refresh."""
    from gapt_server.agent.credentials import _resolve_user_secret  # noqa: PLC0415

    # Resolve vault keys in parallel — each is one short DB read.
    keys = {
        slug: await _resolve_user_secret(
            db=db,
            vault=vault,
            actor_id=actor_id,
            key_name=vault_key,
            purpose="llm_backends.health",
        )
        for slug, vault_key in _API_KEY_VAULT_NAMES.items()
    }
    # Fall back to process env so an operator running `export
    # ANTHROPIC_API_KEY=...` sees the green light too.
    for slug, env_var in _API_KEY_ENV_NAMES.items():
        if slug in keys and not keys[slug]:
            env_val = os.environ.get(env_var, "").strip()
            keys[slug] = env_val or None

    vllm_url = os.environ.get("VLLM_BASE_URL", "").strip() or None
    anthropic_key = keys.get("anthropic")

    return list(
        await asyncio.gather(
            _check_api_provider(
                provider="anthropic",
                vault_key=_API_KEY_VAULT_NAMES["anthropic"],
                env_var=_API_KEY_ENV_NAMES["anthropic"],
                api_key=anthropic_key,
            ),
            _check_api_provider(
                provider="openai",
                vault_key=_API_KEY_VAULT_NAMES["openai"],
                env_var=_API_KEY_ENV_NAMES["openai"],
                api_key=keys.get("openai"),
            ),
            _check_api_provider(
                provider="google",
                vault_key=_API_KEY_VAULT_NAMES["google"],
                env_var=_API_KEY_ENV_NAMES["google"],
                api_key=keys.get("google"),
            ),
            _check_vllm(base_url=vllm_url),
            _check_claude_code(anthropic_api_key=anthropic_key),
        )
    )
