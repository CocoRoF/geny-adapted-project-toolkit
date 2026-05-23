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
    ) -> Pipeline:
        """Resolve + boot pipeline. ``strict=True`` so a malformed
        manifest fails loudly here, not at first ``pipeline.run``."""
        resolution = self.resolve(
            env_id,
            workspace_dir=workspace_dir,
            project_override_path=project_override_path,
        )
        pipeline = await Pipeline.from_manifest_async(
            resolution.manifest,
            credentials=credentials,
            strict=True,
        )
        logger.info(
            "agent.manifest.instantiated",
            env_id=env_id,
            source=resolution.source,
            path=str(resolution.path) if resolution.path else None,
        )
        return pipeline

    # ─────────────────────────────────────────────────── helpers ──

    def list_bundled(self) -> list[str]:
        """Names of every manifest shipped with the server."""
        return sorted(p.stem for p in self._server_dir.glob("*.json"))

    @staticmethod
    def _load(path: Path) -> EnvironmentManifest:
        # geny-executor 2.1.0 exposes `EnvironmentManifest.from_dict` —
        # there is no `.load()` despite plan §2.1's wording. See
        # `docs/progress/m1/e2_agent_and_git.md` "사전 정정".
        data = json.loads(path.read_text(encoding="utf-8"))
        return EnvironmentManifest.from_dict(data)
