"""High-level Cloudflare orchestration for GAPT.

Where `client.py` is a thin HTTP wrapper, this module knows the
*intent* — "make sure `*.<domain>` reaches our Caddy" — and what
that means against real Cloudflare state. It also infers the
tunnel mode (remote vs local) from API response shape, which is
the single most important fact for the UI to convey.

Nothing in here touches the database; the router layer owns that.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from gapt_server.domains.providers.cloudflare.client import (
    CloudflareApiError,
    CloudflareClient,
)

TunnelMode = Literal["remote_managed", "local_config", "unknown"]


@dataclass(frozen=True)
class TunnelSummary:
    id: str
    name: str
    status: str
    """Free-form Cloudflare tunnel status — "healthy", "down", ..."""
    connections: int
    """Active connector count. 0 = no cloudflared instance is logged in."""


@dataclass(frozen=True)
class IngressEntry:
    hostname: str
    """Empty string for the catch-all entry. Wildcards like
    `*.example.com` are valid Cloudflare ingress values."""
    service: str
    """`http://host:port`, `https://...`, `http_status:404` for the
    default reject, or `tcp://...` for non-HTTP."""
    path: str = ""
    """Optional path prefix. Most entries leave it blank."""
    origin_request: dict[str, Any] | None = None
    """Per-entry origin overrides (TLS verify, host header, etc.)."""

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "IngressEntry":
        return cls(
            hostname=raw.get("hostname", "") or "",
            service=raw.get("service", "") or "",
            path=raw.get("path", "") or "",
            origin_request=raw.get("originRequest") or raw.get("origin_request"),
        )

    def to_api(self) -> dict[str, Any]:
        out: dict[str, Any] = {"service": self.service}
        if self.hostname:
            out["hostname"] = self.hostname
        if self.path:
            out["path"] = self.path
        if self.origin_request:
            out["originRequest"] = self.origin_request
        return out


@dataclass
class TunnelConfigSnapshot:
    """The full ingress + warp-routing config for a tunnel, plus our
    best guess of which mode the tunnel runs in."""

    mode: TunnelMode
    ingress: list[IngressEntry]
    warp_routing: dict[str, Any] | None
    raw: dict[str, Any]
    """Original API body. Surface to UI for debug / advanced view."""


def infer_tunnel_mode(raw_config_response: dict[str, Any]) -> TunnelMode:
    """Best-effort guess: did the operator set up this tunnel in
    remote-managed mode or local-config mode?

    The `configurations` endpoint returns these fields:
    - `source: "cloudflare" | "local"` — the actual hint Cloudflare
      gives us when the field is populated.
    - `version` — incremented on every remote-config PUT; stays at
      0 for local-config tunnels.
    - `config.ingress` — the remote-known ingress; an empty/single-
      entry array of just `http_status:404` strongly suggests
      local-config (the operator never pushed a remote config).

    We prefer `source` when available, fall back to the
    `version`/`ingress` heuristic otherwise. `unknown` means we
    couldn't tell — the caller should surface a "set this tunnel
    to remote-managed before automation can take over" hint."""

    src = (raw_config_response.get("source") or "").lower()
    if src == "cloudflare":
        return "remote_managed"
    if src == "local":
        return "local_config"

    version = raw_config_response.get("version")
    cfg = raw_config_response.get("config") or {}
    ingress = cfg.get("ingress") or []
    if isinstance(version, int) and version > 0 and ingress:
        return "remote_managed"
    if (
        isinstance(version, int)
        and version == 0
        and (
            not ingress
            or (
                len(ingress) == 1
                and ingress[0].get("service", "").startswith("http_status:")
            )
        )
    ):
        return "local_config"
    return "unknown"


class CloudflareService:
    """Stateful (per-request) orchestration over `CloudflareClient`.

    A service instance owns one client + the user-selected
    account/tunnel IDs. Higher-level intents (verify, snapshot,
    ensure-wildcard) live here so the router stays thin."""

    def __init__(self, client: CloudflareClient) -> None:
        self._c = client

    async def verify_and_discover(self) -> dict[str, Any]:
        """Verify the token, then enumerate accounts + tunnels +
        zones so the UI can show the operator what their token sees.

        Cloudflare's `/accounts` endpoint requires *some* Account-
        level scope on the token. A token created with only Zone
        scopes (`Zone:Zone:Read`, `Zone:DNS:Edit`) returns 200 with
        an empty `result` — even though those zones internally
        belong to an account. To keep the UI useful in that
        situation we synthesize "discovered" account entries from
        the `account.id` field on every zone the token *did* see.
        Tunnel listing is then attempted against those derived
        accounts; permission errors are absorbed (empty tunnel
        list) rather than killing the whole verify.

        Returns:
            ``{
                "token": {...},
                "accounts": [{id, name, source}, ...],
                "tunnels_by_account": {aid: [TunnelSummary, ...], ...},
                "zones": [{id, name, account_id}, ...],
                "warnings": [str, ...],
            }``
        """
        token_meta = await self._c.verify_token()
        accounts = await self._c.list_accounts()
        zones = await self._c.list_zones()
        warnings: list[str] = []

        # Combine explicit `/accounts` results with accounts derived
        # from zone ownership. The explicit results win on duplicate
        # ids (they have proper names; derived ones just say "from
        # zone X").
        derived_accounts: dict[str, dict[str, Any]] = {}
        for z in zones:
            aid = (z.get("account") or {}).get("id")
            if not aid:
                continue
            if aid not in derived_accounts:
                aname = (z.get("account") or {}).get("name") or f"(from zone {z.get('name')})"
                derived_accounts[aid] = {
                    "id": aid,
                    "name": aname,
                    "source": "zone",
                }

        seen_ids: set[str] = set()
        merged_accounts: list[dict[str, Any]] = []
        for a in accounts:
            aid = a.get("id")
            if not aid or aid in seen_ids:
                continue
            seen_ids.add(aid)
            merged_accounts.append(
                {"id": aid, "name": a.get("name"), "source": "token"}
            )
        for aid, derived in derived_accounts.items():
            if aid in seen_ids:
                continue
            seen_ids.add(aid)
            merged_accounts.append(derived)

        if not merged_accounts:
            warnings.append(
                "Token sees no accounts or zones — verify the token has at "
                "least `Account:Cloudflare Tunnel:Edit` or `Zone:Zone:Read`."
            )

        tunnels_by_account: dict[str, list[dict[str, Any]]] = {}
        tunnel_list_succeeded = False
        for acct in merged_accounts:
            aid = acct["id"]
            try:
                tunnels = await self._c.list_tunnels(aid)
                tunnel_list_succeeded = True
            except CloudflareApiError as exc:
                tunnels = []
                # Surface scope-related failures once so the UI can show
                # the operator what to add. Other (e.g. 5xx) errors stay
                # silent — they're transient.
                if 400 <= exc.status < 500:
                    warnings.append(
                        f"Account {aid[:8]}…: tunnel listing failed "
                        f"({exc}). Token likely missing "
                        "`Account:Cloudflare Tunnel:Read` permission."
                    )
            tunnels_by_account[aid] = [
                {
                    "id": t.get("id"),
                    "name": t.get("name"),
                    "status": t.get("status"),
                    "connections": len(t.get("connections") or []),
                }
                for t in tunnels
                if t.get("id")
            ]

        # NOTE: when `/accounts` is empty but tunnel listing still
        # works, we used to emit an "informational" warning here.
        # It read as a permission denial to operators even though
        # nothing was actually blocked — and the "· derived" badge
        # on the account dropdown already conveys the same info.
        # Silence is the right answer.
        _ = tunnel_list_succeeded  # mypy: variable still useful elsewhere
        return {
            "token": token_meta,
            "accounts": merged_accounts,
            "tunnels_by_account": tunnels_by_account,
            "zones": [
                {
                    "id": z.get("id"),
                    "name": z.get("name"),
                    "account_id": (z.get("account") or {}).get("id"),
                }
                for z in zones
            ],
            "warnings": warnings,
        }

    async def snapshot(self, account_id: str, tunnel_id: str) -> TunnelConfigSnapshot:
        raw = await self._c.get_tunnel_configuration(account_id, tunnel_id)
        cfg = raw.get("config") or {}
        ingress_raw = cfg.get("ingress") or []
        ingress = [IngressEntry.from_api(e) for e in ingress_raw if isinstance(e, dict)]
        return TunnelConfigSnapshot(
            mode=infer_tunnel_mode(raw),
            ingress=ingress,
            warp_routing=cfg.get("warp-routing"),
            raw=raw,
        )

    async def ensure_wildcard_ingress(
        self,
        account_id: str,
        tunnel_id: str,
        *,
        wildcard_hostname: str,
        upstream: str,
    ) -> TunnelConfigSnapshot:
        """Make sure `wildcard_hostname` (e.g. `*.preview.example.com`)
        is in the remote ingress, pointing at `upstream`
        (e.g. `http://localhost:38080`).

        Only valid for **remote-managed** tunnels. Caller is
        responsible for checking the snapshot's `mode` before
        invoking this — we still re-snapshot afterward so the UI
        sees ground truth.

        Idempotent: if an entry with the same hostname already
        exists, we update the `service` field; if not, we insert
        the new entry just before the catch-all (`http_status:404`
        with empty hostname). A catch-all is appended when missing
        — Cloudflare rejects ingress arrays that don't end with a
        catch-all entry."""

        before = await self.snapshot(account_id, tunnel_id)
        if before.mode == "local_config":
            raise CloudflareApiError(
                "Tunnel is in local-config mode — Cloudflare API ingress "
                "writes are ignored at runtime. Migrate the tunnel to "
                "remote-managed first.",
                status=409,
            )

        new_entries: list[IngressEntry] = []
        inserted = False
        catch_all_seen = False
        for e in before.ingress:
            if e.hostname == wildcard_hostname:
                new_entries.append(
                    IngressEntry(
                        hostname=wildcard_hostname,
                        service=upstream,
                        path=e.path,
                        origin_request=e.origin_request,
                    )
                )
                inserted = True
                continue
            if not e.hostname and e.service.startswith("http_status:"):
                # Catch-all — insert our wildcard right before it.
                if not inserted:
                    new_entries.append(
                        IngressEntry(
                            hostname=wildcard_hostname, service=upstream
                        )
                    )
                    inserted = True
                new_entries.append(e)
                catch_all_seen = True
                continue
            new_entries.append(e)
        if not inserted:
            new_entries.append(
                IngressEntry(hostname=wildcard_hostname, service=upstream)
            )
        if not catch_all_seen:
            new_entries.append(IngressEntry(hostname="", service="http_status:404"))

        cfg: dict[str, Any] = {"ingress": [e.to_api() for e in new_entries]}
        if before.warp_routing:
            cfg["warp-routing"] = before.warp_routing
        await self._c.put_tunnel_configuration(
            account_id, tunnel_id, config=cfg
        )
        return await self.snapshot(account_id, tunnel_id)
