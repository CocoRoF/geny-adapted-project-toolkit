"""Next.js basePath patcher — verifies idempotency + the file
transforms against fresh fixture worktrees."""

from __future__ import annotations

import textwrap
from pathlib import Path

from gapt_server.domains.introspection import patch_nextjs_basepath


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


# ─── happy path ───


def test_patches_typical_next_config_and_dockerfile(tmp_path: Path) -> None:
    _write(
        tmp_path / "frontend" / "src" / "next.config.ts",
        """\
        import type { NextConfig } from "next";

        const nextConfig: NextConfig = {
            // existing user-set fields
        };

        export default nextConfig;
        """,
    )
    _write(
        tmp_path / "frontend" / "Dockerfile",
        """\
        FROM node:22-slim
        WORKDIR /app
        COPY src/ /app/
        RUN npm install
        RUN npm run build
        EXPOSE 3000
        CMD ["npm", "run", "start"]
        """,
    )

    result = patch_nextjs_basepath(
        worktree=tmp_path,
        next_config_path="frontend/src/next.config.ts",
        frontend_dockerfile_path=None,
    )
    assert len(result.patched_files) == 2
    assert any("next.config.ts" in p for p in result.patched_files)
    assert any("Dockerfile" in p for p in result.patched_files)
    assert result.skipped == []

    cfg = (tmp_path / "frontend" / "src" / "next.config.ts").read_text()
    assert "process.env.NEXT_PUBLIC_BASE_PATH" in cfg
    assert "basePath:" in cfg
    assert "assetPrefix:" in cfg
    assert "unoptimized" in cfg
    assert "// gapt:" in cfg  # marker present

    df = (tmp_path / "frontend" / "Dockerfile").read_text()
    assert 'ARG NEXT_PUBLIC_BASE_PATH=""' in df
    assert "ENV NEXT_PUBLIC_BASE_PATH=" in df
    # ARG must come BEFORE `RUN npm run build`.
    arg_pos = df.index("ARG NEXT_PUBLIC_BASE_PATH")
    build_pos = df.index("RUN npm run build")
    assert arg_pos < build_pos
    assert "# gapt:" in df


# ─── idempotency ───


def test_rerun_is_noop(tmp_path: Path) -> None:
    _write(
        tmp_path / "frontend" / "src" / "next.config.ts",
        """\
        const nextConfig = { /* foo */ };
        export default nextConfig;
        """,
    )
    _write(
        tmp_path / "frontend" / "Dockerfile",
        """\
        FROM node:22-slim
        RUN npm run build
        """,
    )
    patch_nextjs_basepath(
        worktree=tmp_path,
        next_config_path="frontend/src/next.config.ts",
        frontend_dockerfile_path=None,
    )
    second = patch_nextjs_basepath(
        worktree=tmp_path,
        next_config_path="frontend/src/next.config.ts",
        frontend_dockerfile_path=None,
    )
    assert second.patched_files == []
    # Both files report as already patched in `skipped`.
    assert any("already patched" in s for s in second.skipped)


# ─── fallbacks ───


def test_no_next_config_reports_skipped(tmp_path: Path) -> None:
    _write(tmp_path / "frontend" / "Dockerfile", "FROM node:22-slim\nRUN npm run build\n")
    result = patch_nextjs_basepath(
        worktree=tmp_path,
        next_config_path=None,
        frontend_dockerfile_path=None,
    )
    assert result.patched_files == []
    assert any("next.config" in s for s in result.skipped)


def test_unrecognised_next_config_does_not_corrupt_file(tmp_path: Path) -> None:
    """When the config doesn't match our regex (e.g. uses a function
    style `export default () => ({...})`), the patcher skips it
    rather than corrupting the file."""
    weird = """\
    export default function getConfig() {
        return { reactStrictMode: true };
    }
    """
    cfg = tmp_path / "next.config.js"
    _write(cfg, weird)
    result = patch_nextjs_basepath(
        worktree=tmp_path,
        next_config_path="next.config.js",
        frontend_dockerfile_path=None,
    )
    # Skipped because the regex couldn't anchor.
    assert any("couldn't locate" in s for s in result.skipped)
    # File contents preserved verbatim.
    assert cfg.read_text() == textwrap.dedent(weird)


def test_dockerfile_without_build_command_still_injects(tmp_path: Path) -> None:
    """Dockerfile uses `next start` directly (no explicit
    `npm run build` — they pre-built outside). Patcher falls back
    to "before the first RUN" so the ARG is still in scope."""
    _write(
        tmp_path / "next.config.ts",
        "const nextConfig = {};\nexport default nextConfig;\n",
    )
    _write(
        tmp_path / "frontend" / "Dockerfile",
        """\
        FROM node:22-slim
        COPY . /app
        RUN echo "no build step"
        CMD ["next", "start"]
        """,
    )
    result = patch_nextjs_basepath(
        worktree=tmp_path,
        next_config_path="next.config.ts",
        frontend_dockerfile_path="frontend/Dockerfile",
    )
    assert any("Dockerfile" in p for p in result.patched_files)
