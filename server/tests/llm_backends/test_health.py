"""Phase G.1.d — provider health collection contract.

The router endpoint is one short wrapper around `collect_health`,
so these tests target the domain function. Avoids needing a
running FastAPI + DSN — `collect_health` only depends on:

  - a fake `vault.list` / `vault.read` pair to drive the
    "anthropic_api_key found" path
  - process env (`os.environ`) for the fallback path
  - subprocess (we monkeypatch `_run_cmd` to skip the real
    `claude --version` / `claude auth status` calls)
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from gapt_server.domains.llm_backends import collect_health, health as health_mod


class _FakeMetadata:
    def __init__(self, key_name: str, secret_id: str = "01KSEC0000") -> None:
        self.id = secret_id
        self.key_name = key_name


class _FakeVault:
    """Drop-in for SecretVault — supports the subset
    `collect_health` reaches through (`_resolve_user_secret`)."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = values or {}

    async def list(self, _db: Any, *, scope: Any, owner_id: str) -> list[_FakeMetadata]:  # noqa: ARG002
        return [_FakeMetadata(k) for k in self._values]

    async def read(
        self,
        _db: Any,
        *,
        secret_id: str,  # noqa: ARG002
        purpose: str,  # noqa: ARG002
        actor_id: str,  # noqa: ARG002
    ) -> str:
        # We re-key on key_name via the secret_id ↔ key_name mapping
        # the fake provides — but the resolver only ever calls
        # read() after iterating list(), so we can rely on insertion
        # order: the test must seed values in the order it expects.
        # Simpler: encode `key_name` into `secret_id` so the lookup
        # is unambiguous.
        for k, v in self._values.items():
            if secret_id == "01KSEC0000":
                self._values.pop(k, None)
                return v
        return ""


class _Vault:
    """Cleaner fake than the above — `list()` returns metadata whose
    id encodes the key_name, `read()` decodes it directly. Keeps
    the test setup short."""

    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    async def list(self, _db: Any, *, scope: Any, owner_id: str) -> list[Any]:  # noqa: ARG002
        class _Md:
            def __init__(self, key_name: str) -> None:
                self.id = f"id::{key_name}"
                self.key_name = key_name

        return [_Md(k) for k in self._values]

    async def read(
        self,
        _db: Any,
        *,
        secret_id: str,
        purpose: str,  # noqa: ARG002
        actor_id: str,  # noqa: ARG002
    ) -> str:
        _, _, key = secret_id.partition("::")
        return self._values.get(key, "")


# ───────────────────────────────────── collect_health ──


@pytest.mark.asyncio
async def test_health_all_missing_when_nothing_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare host: no vault entries, no env, no claude binary. Every
    card should be `missing`, none should crash."""
    for env in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "VLLM_BASE_URL"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(health_mod, "claude_binary_path", lambda: None)

    rows = await collect_health(db=None, vault=_Vault({}), actor_id="admin")
    by_provider = {r.provider: r for r in rows}
    assert {r.provider for r in rows} == {
        "anthropic",
        "openai",
        "google",
        "vllm",
        "claude_code_cli",
    }
    for slug in ("anthropic", "openai", "google", "vllm", "claude_code_cli"):
        assert by_provider[slug].state == "missing", slug


@pytest.mark.asyncio
async def test_health_anthropic_ok_when_vault_has_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vault entry alone is enough — env var doesn't need to be
    set. Claude Code card flips to `ok` too because the API key is
    the universal path."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(health_mod, "claude_binary_path", lambda: "/usr/local/bin/claude")
    monkeypatch.setattr(
        health_mod,
        "_run_cmd",
        _stub_run_cmd({("--version",): (0, "1.2.3", "")}),
    )

    rows = await collect_health(
        db=None,
        vault=_Vault({"anthropic_api_key": "sk-ant-real"}),
        actor_id="admin",
    )
    by_provider = {r.provider: r for r in rows}
    assert by_provider["anthropic"].state == "ok"
    assert by_provider["anthropic"].auth_method == "api_key"
    assert by_provider["claude_code_cli"].state == "ok"
    assert by_provider["claude_code_cli"].auth_method == "api_key"
    assert by_provider["claude_code_cli"].binary_version == "1.2.3"


@pytest.mark.asyncio
async def test_health_env_var_fallback_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator who exports `OPENAI_API_KEY=...` instead of using
    the vault still sees an ok card. The fallback exists for parity
    with `geny_executor`'s credential resolution."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-key")
    monkeypatch.setattr(health_mod, "claude_binary_path", lambda: None)

    rows = await collect_health(db=None, vault=_Vault({}), actor_id="admin")
    by_provider = {r.provider: r for r in rows}
    assert by_provider["openai"].state == "ok"


@pytest.mark.asyncio
async def test_health_vllm_ok_when_base_url_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VLLM_BASE_URL", "http://vllm.local:8000/v1")
    monkeypatch.setattr(health_mod, "claude_binary_path", lambda: None)

    rows = await collect_health(db=None, vault=_Vault({}), actor_id="admin")
    by_provider = {r.provider: r for r in rows}
    assert by_provider["vllm"].state == "ok"
    assert "http://vllm.local:8000/v1" in by_provider["vllm"].detail


@pytest.mark.asyncio
async def test_health_claude_subscription_path_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subscription detection: claude binary present, no API key,
    but `claude auth status --json` reports `loggedIn: true` and
    the OAuth file's `expiresAt` is in the future."""
    import time

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(health_mod, "claude_binary_path", lambda: "/usr/local/bin/claude")
    future_ms = int((time.time() + 3600) * 1000)
    monkeypatch.setattr(
        health_mod, "_read_claude_oauth_expires_at_ms", lambda: future_ms
    )
    monkeypatch.setattr(
        health_mod,
        "_run_cmd",
        _stub_run_cmd(
            {
                ("--version",): (0, "1.2.3", ""),
                ("auth", "status", "--json"): (
                    0,
                    json.dumps({"loggedIn": True, "subscriptionType": "max"}),
                    "",
                ),
            }
        ),
    )

    rows = await collect_health(db=None, vault=_Vault({}), actor_id="admin")
    cc = next(r for r in rows if r.provider == "claude_code_cli")
    assert cc.state == "ok"
    assert cc.auth_method == "subscription"
    assert "type=max" in cc.detail


@pytest.mark.asyncio
async def test_health_claude_subscription_expired_surfaces_warn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Critical case from the Geny 2026-05-18 incident: CLI says
    `loggedIn: true` but the OAuth token is past its `expiresAt`.
    Card must flip to `expired`, not `ok` — otherwise every chat
    call 401s after a green light."""
    import time

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(health_mod, "claude_binary_path", lambda: "/usr/local/bin/claude")
    past_ms = int((time.time() - 60) * 1000)
    monkeypatch.setattr(health_mod, "_read_claude_oauth_expires_at_ms", lambda: past_ms)
    monkeypatch.setattr(
        health_mod,
        "_run_cmd",
        _stub_run_cmd(
            {
                ("--version",): (0, "1.2.3", ""),
                ("auth", "status", "--json"): (
                    0,
                    json.dumps({"loggedIn": True}),
                    "",
                ),
            }
        ),
    )

    rows = await collect_health(db=None, vault=_Vault({}), actor_id="admin")
    cc = next(r for r in rows if r.provider == "claude_code_cli")
    assert cc.state == "expired"
    assert cc.expires_at_ms == past_ms
    assert "expired" in cc.detail.lower()


def _stub_run_cmd(table: dict[tuple[str, ...], tuple[int, str, str]]):
    """Return a fake `_run_cmd` that looks up the argv tail (after
    the binary path) in `table` and replays the stored
    `(rc, stdout, stderr)`. Missing keys default to a non-zero
    failure so misuses don't silently pass."""

    async def _fake(argv: list[str], timeout: float = 5.0) -> tuple[int, str, str]:  # noqa: ARG001
        tail = tuple(argv[1:])
        return table.get(tail, (1, "", "stub: unhandled argv"))

    return _fake
