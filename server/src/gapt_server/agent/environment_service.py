"""GaptEnvironmentService — manifest resolution + pipeline instantiation.

Resolves a manifest id (e.g. ``gapt_default``) into a concrete
``EnvironmentManifest`` by checking three locations in order:

1. **project_override** — caller-supplied path (typically from
   ``projects.environments[*].manifest_override`` once that column
   lands in M1-E4). Highest priority.
2. **workspace_local** — ``.gapt/manifests/{id}.json`` inside the
   workspace tree. Lets users tweak the agent's behaviour per repo
   without server access.
3. **server_bundled** — ``gapt_server/manifests/{id}.json`` shipped
   with the binary. Three are bundled today: ``gapt_default``,
   ``gapt_planning``, ``gapt_review``.

The service intentionally does *not* know about credentials — those
are layered in by ``ProjectAwareSessionManager`` (Cycle 2.8) which
calls this service for the manifest, then the CredentialBundle
builder (Cycle 2.2) for the auth bundle, and finally hands both to
``Pipeline.from_manifest_async``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from geny_executor import EnvironmentManifest, Pipeline

if TYPE_CHECKING:
    from collections.abc import Sequence

    from geny_executor import CredentialBundle

logger = structlog.get_logger(__name__)


SERVER_MANIFESTS_DIR = Path(__file__).resolve().parent.parent / "manifests"


@dataclass(frozen=True)
class ManifestOverrides:
    """User-supplied manifest patches. Every field is optional —
    missing fields fall through to the manifest's bundled defaults.

    Sourced from `user_agent_prefs` row when the session is created
    or rehydrated. Stored as Python primitives so the patch logic
    stays JSON-friendly."""

    model: str | None = None
    max_tokens: int | None = None
    max_iterations: int | None = None
    cost_budget_usd: float | None = None
    timeout_s: int | None = None
    # Phase L.4 — Anthropic extended-thinking budget. `None` falls
    # through to the manifest's bundled value (typically thinking off
    # for default manifests). When `thinking_enabled` is None but
    # `thinking_budget_tokens` is set, treat budget>0 as enable=True
    # so the operator doesn't need to flip two switches at once.
    thinking_enabled: bool | None = None
    thinking_budget_tokens: int | None = None

    def has_any(self) -> bool:
        return any(
            v is not None
            for v in (
                self.model,
                self.max_tokens,
                self.max_iterations,
                self.cost_budget_usd,
                self.timeout_s,
                self.thinking_enabled,
                self.thinking_budget_tokens,
            )
        )


def _manifest_to_dict(manifest: EnvironmentManifest) -> dict[str, object]:
    """Round-trip the manifest through dict form using geny-executor's
    public `to_dict()` so we get a `from_dict()`-compatible payload
    we can mutate without reaching into executor internals."""
    return manifest.to_dict()


def apply_overrides(
    manifest_dict: dict[str, object], overrides: ManifestOverrides
) -> tuple[dict[str, object], dict[str, object]]:
    """Patch the manifest dict in-place style and return ``(patched,
    applied)``. ``applied`` lists what actually changed so audit logs
    can show only the diff, not the full override struct.

    Schema notes (geny-executor 2.1.0+):
      - `max_iterations` + `cost_budget_usd` live under `pipeline.*`
        (not at the top level — top-level keys are accepted by
        `from_dict` for backwards compat but silently dropped).
      - `model` + `max_tokens` + `timeout_s` live in the api stage's
        `config` dict (stage[name == "api"].config).
    """
    applied: dict[str, object] = {}

    if overrides.max_iterations is not None or overrides.cost_budget_usd is not None:
        pipeline = manifest_dict.get("pipeline")
        if not isinstance(pipeline, dict):
            pipeline = {}
            manifest_dict["pipeline"] = pipeline
        if overrides.max_iterations is not None:
            pipeline["max_iterations"] = overrides.max_iterations
            applied["max_iterations"] = overrides.max_iterations
        if overrides.cost_budget_usd is not None:
            pipeline["cost_budget_usd"] = overrides.cost_budget_usd
            applied["cost_budget_usd"] = overrides.cost_budget_usd

    # `model` + `max_tokens` ride on the manifest's *top-level* `model`
    # dict — that's what `s06_api.resolve_model_config` reads at run
    # time (via state.model, populated by `PipelineConfig.apply_to_state`).
    # Writing into the api stage's `config` looks plausible but is
    # ignored by the executor's modern stage class. We patch both
    # locations to be safe with older artifacts that still read
    # stage.config.
    if any(v is not None for v in (overrides.model, overrides.max_tokens, overrides.timeout_s)):
        model_dict = manifest_dict.get("model")
        if not isinstance(model_dict, dict):
            model_dict = {}
            manifest_dict["model"] = model_dict

        if overrides.model is not None:
            model_dict["model"] = overrides.model
            applied["model"] = overrides.model
        if overrides.max_tokens is not None:
            model_dict["max_tokens"] = overrides.max_tokens
            applied["max_tokens"] = overrides.max_tokens

        stages = manifest_dict.get("stages")
        if isinstance(stages, list):
            for stage in stages:
                if not isinstance(stage, dict) or stage.get("name") != "api":
                    continue
                cfg = stage.setdefault("config", {})
                if not isinstance(cfg, dict):
                    continue
                if overrides.model is not None:
                    cfg["model"] = overrides.model
                if overrides.max_tokens is not None:
                    cfg["max_tokens"] = overrides.max_tokens
                if overrides.timeout_s is not None:
                    cfg["timeout_s"] = overrides.timeout_s
                    applied["timeout_s"] = overrides.timeout_s
                break

    # Phase L.4 — thinking config. ModelConfig reads `thinking_enabled`
    # / `thinking_budget_tokens` / `thinking_type` from the top-level
    # `model` dict (same place `model` + `max_tokens` go).
    if (
        overrides.thinking_enabled is not None
        or overrides.thinking_budget_tokens is not None
    ):
        model_dict = manifest_dict.get("model")
        if not isinstance(model_dict, dict):
            model_dict = {}
            manifest_dict["model"] = model_dict
        # Operator convenience: a positive budget implies enabled
        # unless they explicitly said otherwise.
        effective_enabled = overrides.thinking_enabled
        if effective_enabled is None and (
            overrides.thinking_budget_tokens or 0
        ) > 0:
            effective_enabled = True
        if effective_enabled is not None:
            model_dict["thinking_enabled"] = effective_enabled
            applied["thinking_enabled"] = effective_enabled
        if overrides.thinking_budget_tokens is not None:
            model_dict["thinking_budget_tokens"] = (
                overrides.thinking_budget_tokens
            )
            applied["thinking_budget_tokens"] = overrides.thinking_budget_tokens
        # Mirror into the api stage config too — same back-compat
        # reason as `model` / `max_tokens` above.
        stages = manifest_dict.get("stages")
        if isinstance(stages, list):
            for stage in stages:
                if not isinstance(stage, dict) or stage.get("name") != "api":
                    continue
                cfg = stage.setdefault("config", {})
                if not isinstance(cfg, dict):
                    continue
                if effective_enabled is not None:
                    cfg["thinking_enabled"] = effective_enabled
                if overrides.thinking_budget_tokens is not None:
                    cfg["thinking_budget_tokens"] = (
                        overrides.thinking_budget_tokens
                    )
                break

    return manifest_dict, applied


class ManifestNotFoundError(RuntimeError):
    """Stable error code ``exec.pipeline.manifest_not_found`` from
    geny-executor's side — we wrap it locally so the router layer can
    translate to HTTP 404 without importing executor internals."""

    def __init__(self, env_id: str, tried: Sequence[Path]) -> None:
        super().__init__(
            f"manifest {env_id!r} not found; tried: {', '.join(str(p) for p in tried)}"
        )
        self.env_id = env_id
        self.tried = list(tried)


@dataclass(frozen=True)
class ManifestResolution:
    """Where a manifest came from — useful for audit logs."""

    env_id: str
    source: str  # "project_override" | "workspace_local" | "server_bundled"
    path: Path | None  # None for project_override when it carried dict directly
    manifest: EnvironmentManifest


class GaptEnvironmentService:
    """Stateless resolver + pipeline instantiator."""

    def __init__(
        self,
        *,
        server_manifests_dir: Path | None = None,
    ) -> None:
        self._server_dir = server_manifests_dir or SERVER_MANIFESTS_DIR
        if not self._server_dir.exists():
            raise RuntimeError(
                f"server_manifests_dir {self._server_dir} does not exist; "
                "shipping `manifests/*.json` is M1-E2 Cycle 2.1 scope"
            )

    # ─────────────────────────────────────────────── resolution ──

    def resolve(
        self,
        env_id: str,
        *,
        workspace_dir: Path | None = None,
        project_override_path: Path | None = None,
    ) -> ManifestResolution:
        """Walk the 3-tier lookup and return a loaded manifest.

        Raises ``ManifestNotFoundError`` listing every path we tried.
        """
        env_id = env_id.strip()
        if not env_id:
            raise ManifestNotFoundError(env_id, [])

        tried: list[Path] = []

        if project_override_path is not None:
            tried.append(project_override_path)
            if project_override_path.exists():
                manifest = self._load(project_override_path)
                return ManifestResolution(
                    env_id=env_id,
                    source="project_override",
                    path=project_override_path,
                    manifest=manifest,
                )

        if workspace_dir is not None:
            workspace_path = workspace_dir / ".gapt" / "manifests" / f"{env_id}.json"
            tried.append(workspace_path)
            if workspace_path.exists():
                manifest = self._load(workspace_path)
                return ManifestResolution(
                    env_id=env_id,
                    source="workspace_local",
                    path=workspace_path,
                    manifest=manifest,
                )

        bundled_path = self._server_dir / f"{env_id}.json"
        tried.append(bundled_path)
        if bundled_path.exists():
            manifest = self._load(bundled_path)
            return ManifestResolution(
                env_id=env_id,
                source="server_bundled",
                path=bundled_path,
                manifest=manifest,
            )

        raise ManifestNotFoundError(env_id, tried)

    # ────────────────────────────────────────────── instantiation ──

    async def instantiate_pipeline(
        self,
        env_id: str,
        *,
        credentials: CredentialBundle | None = None,
        workspace_dir: Path | None = None,
        project_override_path: Path | None = None,
        overrides: ManifestOverrides | None = None,
    ) -> Pipeline:
        """Resolve + boot pipeline. ``strict=True`` so a malformed
        manifest fails loudly here, not at first ``pipeline.run``.

        ``overrides``, when given, patches the resolved manifest
        *dict* with user-global preferences before
        ``EnvironmentManifest.from_dict`` — keeps the on-disk file
        untouched. See `apply_overrides()` below."""
        resolution = self.resolve(
            env_id,
            workspace_dir=workspace_dir,
            project_override_path=project_override_path,
        )
        manifest = resolution.manifest
        applied: dict[str, object] = {}
        if overrides is not None and overrides.has_any():
            patched_dict, applied = apply_overrides(_manifest_to_dict(manifest), overrides)
            manifest = EnvironmentManifest.from_dict(patched_dict)
        pipeline = await Pipeline.from_manifest_async(
            manifest,
            credentials=credentials,
            strict=True,
        )
        logger.info(
            "agent.manifest.instantiated",
            env_id=env_id,
            source=resolution.source,
            path=str(resolution.path) if resolution.path else None,
            overrides_applied=applied or None,
        )
        return pipeline

    # ─────────────────────────────────────────────────── helpers ──

    def list_bundled(self) -> list[str]:
        """Names of every manifest shipped with the server."""
        return sorted(p.stem for p in self._server_dir.glob("*.json"))

    def bundled_api_model(self, env_id: str) -> str | None:
        """Return the bundled (un-overridden) api stage model for ``env_id``.

        Phase M.2 — the chat panel's model pill labels the "inherit"
        option as "(uses sonnet)" sourced from the manifest's stored
        api-stage model. The `_baseline_model` the runtime uses to
        revert per-invoke overrides used to be whatever
        `pipeline._config.model.model` resolved to AFTER admin prefs +
        per-session overrides applied — so "inherit" reverted to
        whatever the admin pref locked in (e.g. opus), NOT the
        manifest's "sonnet" the pill promised. This helper returns
        the raw bundled value so the runtime can baseline-revert to
        the manifest's own default, matching the pill UX.

        Returns ``None`` when the manifest doesn't pin a model — the
        runtime then falls back to capturing whatever the live
        pipeline carries.
        """
        try:
            resolution = self.resolve(env_id)
        except ManifestNotFoundError:
            return None
        manifest = resolution.manifest
        # Prefer the api stage's config.model — that's what the chat
        # panel displays. Fall back to top-level model.model if the
        # stage didn't pin one (uncommon but legal).
        for entry in manifest.stages:
            if not isinstance(entry, dict):
                continue
            if entry.get("name") != "api":
                continue
            cfg = entry.get("config")
            if isinstance(cfg, dict):
                model = cfg.get("model")
                if isinstance(model, str) and model.strip():
                    return model.strip()
        top = manifest.model
        if isinstance(top, dict):
            model = top.get("model")
            if isinstance(model, str) and model.strip():
                return model.strip()
        return None

    @staticmethod
    def _load(path: Path) -> EnvironmentManifest:
        # geny-executor 2.1.0 exposes `EnvironmentManifest.from_dict` —
        # there is no `.load()` despite plan §2.1's wording. See
        # `docs/progress/m1/e2_agent_and_git.md` "사전 정정".
        data = json.loads(path.read_text(encoding="utf-8"))
        return EnvironmentManifest.from_dict(data)
