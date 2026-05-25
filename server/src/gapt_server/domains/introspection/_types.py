"""Shared value types for project introspection.

`ProjectIntrospection` is the canonical "GAPT understanding of this
project" object. Every detector contributes fields; the merger
combines them by confidence-weighted union (see `_detector.merge`).

Fields explicitly capture both *what* was found AND *how confident*
we are — the UI uses confidence to decide whether to auto-apply the
suggestion or surface it as a draft the user reviews."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ProjectKind(StrEnum):
    """Top-level project classification. Drives which dev / prod
    heuristics apply. Multi-stack projects (e.g. Next.js frontend
    + FastAPI backend in one repo) take whichever framework owns the
    primary service; secondary services land in `notes`."""

    NEXTJS = "nextjs"
    VITE = "vite"  # SPA only (Vue/React/Svelte all flatten to "vite")
    EXPRESS = "express"  # Node Express / Fastify / etc.
    FASTAPI = "fastapi"
    DJANGO = "django"
    FLASK = "flask"
    GO = "go"
    RUST = "rust"
    STATIC = "static"  # pure HTML/CSS/JS with no build step
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProjectIntrospection:
    """The merged result of running every detector. Consumed by the
    auto-config layer to seed `WorkspaceService` (dev) and
    `Environment` (prod) rows."""

    # ─── identity ───
    # Framework of the primary user-facing tier (regardless of
    # whether prod uses compose or single-image deploy). UNKNOWN when
    # nothing recognisable was found.
    kind: ProjectKind = ProjectKind.UNKNOWN
    # True when the project ships compose files — drives whether the
    # prod environment gets `deploy_target_kind = LOCAL` (compose) vs
    # a single-image build. Orthogonal to `kind`.
    has_compose: bool = False
    # Free-form hint of secondary stacks the user might care about,
    # e.g. ["fastapi-backend", "postgres", "minio"]. Pure annotation.
    secondary_stacks: list[str] = field(default_factory=list)

    # ─── dev mode ───
    # The command the user runs locally to start a hot-reload server.
    # Examples: "npm run dev", "uvicorn app:app --reload", "vite".
    dev_command: str | None = None
    # The port that command listens on. Read from package.json scripts
    # arg (`next dev -p 3000`), vite config, or framework defaults.
    dev_port: int | None = None
    # Working directory inside the workspace where dev_command should
    # run. None means worktree root. Set for monorepos like
    # `apps/web/`.
    dev_cwd: str | None = None
    # Extra env vars the dev command typically needs. Suggested only —
    # the UI shows them as a starter template the user edits.
    dev_env_hints: dict[str, str] = field(default_factory=dict)
    # Command that installs the project's dependencies. Run once
    # before `dev_command` (and again if `dev_command` ever fails
    # with a "module not found" — the wrapper handles the retry).
    # Empty for stacks that don't need an install step (Go, Rust
    # compiled-on-build, static sites).
    install_command: str | None = None

    # ─── tests ───
    # The command to run the project's test suite. Examples:
    # "npm test", "pytest", "vitest run". Same `dev_cwd` applies —
    # the test runner panel wraps `cd <dev_cwd> && <test_command>`.
    test_command: str | None = None

    # ─── prod mode (compose-based) ───
    # Most projects with a real prod story use compose. We pick the
    # first compose file we see; multi-file overrides land in
    # `prod_compose_paths`.
    prod_compose_path: str | None = None
    prod_compose_paths: list[str] = field(default_factory=list)
    prod_primary_service: str | None = None
    prod_primary_port: int | None = None
    # True when ANY compose service has a `build:` directive — tells
    # GAPT to pass `--build` on first deploy.
    prod_build_required: bool = False

    # ─── env files ───
    # Paths (relative to worktree root) of `.env`-style files we
    # found. UI offers to seed them from `.env.example` siblings.
    env_files: list[str] = field(default_factory=list)
    # Same files but the `.example`/`.template` siblings — useful for
    # seeding new env files with the right keys.
    env_examples: list[str] = field(default_factory=list)

    # ─── routing hints ───
    # True when the framework respects a basePath at build time
    # (Next.js, Vite, Nuxt). The UI offers "auto-patch with
    # NEXT_PUBLIC_BASE_PATH=/preview/<slug>".
    needs_basepath: bool = False
    # Which file the auto-patcher would touch — relative path inside
    # the worktree. None when no patcher is wired for this kind.
    basepath_config_file: str | None = None

    # ─── meta ───
    # 0-1 confidence. Above 0.8 the UI auto-applies; below the user
    # has to confirm each field. The merger combines per-field
    # confidences but exposes a single roll-up here for simplicity.
    confidence: float = 0.0
    # Human-readable findings: "found docker-compose.prod.yml with 5
    # services; picked `frontend` (port 3000) as primary." UI shows
    # these as a bullet list under "What I found".
    notes: list[str] = field(default_factory=list)
    # Detectors that contributed to this result. Helps the UI explain
    # *why* a field has a given value ("port came from package.json
    # `scripts.dev`").
    sources: list[str] = field(default_factory=list)

    def with_update(self, **changes: object) -> ProjectIntrospection:
        """Return a copy with the named fields replaced. Helper for
        merging — keeps the dataclass frozen + composes well."""
        from dataclasses import replace  # noqa: PLC0415

        return replace(self, **changes)  # type: ignore[arg-type]
