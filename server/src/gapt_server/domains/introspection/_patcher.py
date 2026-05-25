"""Workspace-clone patchers — invasive but only inside the
worktree (the user's GitHub fork stays untouched).

When the IDE wizard sees `needs_basepath=true` it can call into
this module to patch `next.config.ts` + `frontend/Dockerfile` so
the app builds with the right basePath baked in, without forcing
the user to remember three places that need editing.

Idempotency: every patch checks for the marker comment before
touching the file. Re-running a patcher is a no-op.

Currently shipped:
  * `patch_nextjs_basepath` — Next.js basePath + assetPrefix +
    images.unoptimized + Dockerfile ARG injection.

Anything else (Vite base, Django FORCE_SCRIPT_NAME, FastAPI
root_path) lives behind its own helper for future cycles."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


# GAPT marker — every patch we write carries this so the patcher
# can detect prior runs and skip cleanly. Also lets a future
# `unpatch` operation find the right anchor.
_MARKER = "// gapt: next-basepath-patch"
_DOCKERFILE_MARKER = "# gapt: next-basepath-patch"


@dataclass
class PatchResult:
    """What changed (or didn't). The UI shows this back to the user
    as a checklist + reverts the screen state if `patched_files` is
    empty + `skipped` is non-empty (meaning the patcher had to bail
    and the user should hand-edit)."""

    patched_files: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)


def patch_nextjs_basepath(
    *,
    worktree: Path,
    next_config_path: str | None,
    frontend_dockerfile_path: str | None,
) -> PatchResult:
    """Make a Next.js app build cleanly under a runtime `basePath`.

    Three file touches, each idempotent:

      1. **next.config.{ts,js,mjs}** — wrap the existing
         `NextConfig` with a basePath that reads
         `process.env.NEXT_PUBLIC_BASE_PATH` at build time +
         assetPrefix + `images.unoptimized=true` when basePath is
         non-empty. Empty string falls through to the original
         behaviour so non-GAPT deploys (the user's own nginx) keep
         working.

      2. **frontend/Dockerfile** — declare
         `ARG NEXT_PUBLIC_BASE_PATH=""` and
         `ENV NEXT_PUBLIC_BASE_PATH=${NEXT_PUBLIC_BASE_PATH}` before
         the `RUN npm run build` line so the next config gets the
         right value when compose builds it with `--build-arg`.

      3. *(Not in this function — the caller's compose override is
         expected to carry the `build.args.NEXT_PUBLIC_BASE_PATH`
         field itself; we surface the recommendation in
         `next_steps` rather than guessing the slug here.)*

    Args:
      worktree: absolute path to the workspace root.
      next_config_path: relative path the detector found (e.g.
        `"frontend/src/next.config.ts"`). When None, falls back to
        the common locations; if nothing matches, skipped.
      frontend_dockerfile_path: relative path. When None, infers
        `<dir>/Dockerfile` from `next_config_path`.
    """
    result = PatchResult()
    cfg = _resolve_next_config(worktree, next_config_path)
    if cfg is None:
        result.skipped.append(
            "next.config not found in the worktree — skipped basePath patch"
        )
        return result
    _patch_next_config(cfg, result)

    df = _resolve_frontend_dockerfile(worktree, frontend_dockerfile_path, cfg)
    if df is None:
        result.skipped.append(
            "frontend Dockerfile not found — basePath ARG injection skipped"
        )
    else:
        _patch_dockerfile(df, result)

    result.next_steps.append(
        "compose override 또는 환경 설정에 "
        "`build.args.NEXT_PUBLIC_BASE_PATH = /preview/<slug>`를 추가해 "
        "재배포하세요."
    )
    return result


# ──────────────────────────────────────────────────────── helpers ─


def _resolve_next_config(worktree: Path, hint: str | None) -> Path | None:
    if hint:
        p = worktree / hint
        if p.is_file():
            return p
    # Fallback search — same locations the Node detector probes.
    for rel in (
        "next.config.ts",
        "next.config.js",
        "next.config.mjs",
        "frontend/next.config.ts",
        "frontend/next.config.js",
        "frontend/next.config.mjs",
        "frontend/src/next.config.ts",
        "frontend/src/next.config.js",
        "web/next.config.ts",
        "apps/web/next.config.ts",
    ):
        p = worktree / rel
        if p.is_file():
            return p
    return None


def _resolve_frontend_dockerfile(
    worktree: Path, hint: str | None, next_config: Path
) -> Path | None:
    if hint:
        p = worktree / hint
        if p.is_file():
            return p
    # The Dockerfile usually lives next to the package root, which
    # is one level *up* from next.config when the config is under
    # `src/`. Try that first, then the same dir.
    parent = next_config.parent
    for candidate in (
        parent / "Dockerfile",
        parent.parent / "Dockerfile",
        worktree / "frontend" / "Dockerfile",
    ):
        if candidate.is_file():
            return candidate
    return None


def _patch_next_config(path: Path, result: PatchResult) -> None:
    """Mutate the Next config file in place. Two cases:

      * **Detected `nextConfig` object literal** — inject a
        `basePath` + `assetPrefix` + `images` block right after the
        opening `{` of the config. Leaves any user-set keys intact.
      * **Anything weirder** — give up and append a sibling export
        with our values. Caller sees this as "skipped + manual
        guidance" and can hand-edit.

    Both paths drop the GAPT marker so re-runs are no-ops."""
    text = path.read_text(encoding="utf-8")
    rel = path.name
    if _MARKER in text:
        result.skipped.append(f"{rel}: already patched")
        return

    snippet = _BASEPATH_SNIPPET.strip()
    # Match `const nextConfig: NextConfig = {` or
    # `const nextConfig = {` (JS file without TS annotation).
    pattern = re.compile(
        r"(const\s+nextConfig\s*(?::\s*NextConfig)?\s*=\s*\{)",
        re.MULTILINE,
    )
    new_text, count = pattern.subn(
        lambda m: f"{m.group(1)}\n{snippet}",
        text,
        count=1,
    )
    if count == 0:
        result.skipped.append(
            f"{rel}: couldn't locate `const nextConfig = {{` — hand-edit "
            "and add `basePath / assetPrefix / images.unoptimized` from "
            "`process.env.NEXT_PUBLIC_BASE_PATH`."
        )
        return

    # Inject the `const __basePath__ = …` initialiser at file top
    # (right after the imports block). Falls back to prepending if
    # we can't find an import line.
    new_text = _inject_top_const(new_text)

    path.write_text(new_text, encoding="utf-8")
    result.patched_files.append(str(path))


def _inject_top_const(text: str) -> str:
    """Place `const __gapt_basePath = …` after the last `import …`
    line. When no imports exist, prepend at file top."""
    init = _BASEPATH_INIT.strip() + "\n"
    lines = text.splitlines(keepends=True)
    last_import = -1
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("import ") or stripped.startswith("import\t"):
            last_import = i
    if last_import == -1:
        return init + text
    lines.insert(last_import + 1, "\n" + init)
    return "".join(lines)


_BASEPATH_INIT = f"""
{_MARKER}
const __gapt_basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
"""

_BASEPATH_SNIPPET = """
    basePath: __gapt_basePath || undefined,
    assetPrefix: __gapt_basePath || undefined,
    // When mounted under a basePath via a reverse proxy (GAPT
    // preview), Next.js's image-optimization server can't fetch
    // its own /public/* — its internal fetch bypasses basePath.
    // Disabling optimisation makes <Image> render plain <img> with
    // the right prefix.
    images: __gapt_basePath ? { unoptimized: true } : undefined,
"""


def _patch_dockerfile(path: Path, result: PatchResult) -> None:
    """Inject `ARG NEXT_PUBLIC_BASE_PATH` + `ENV` before the build
    step. Idempotent via the GAPT marker."""
    text = path.read_text(encoding="utf-8")
    rel = path.name
    if _DOCKERFILE_MARKER in text:
        result.skipped.append(f"{rel}: already patched")
        return

    snippet = _DOCKERFILE_SNIPPET.strip() + "\n"
    # Insert before the first `RUN npm run build` (or `RUN yarn
    # build`, `RUN pnpm build`). Falls back to before the first
    # `RUN` line.
    build_re = re.compile(r"^RUN\s+(?:npm\s+run\s+build|yarn\s+build|pnpm\s+build)\b", re.MULTILINE)
    m = build_re.search(text)
    if m is None:
        # Fall back: before the first RUN — still earlier than the
        # implicit build step (next start etc.). Better than nothing.
        first_run = re.search(r"^RUN\s+", text, re.MULTILINE)
        if first_run is None:
            result.skipped.append(
                f"{rel}: no RUN line found — hand-add ARG / ENV before build"
            )
            return
        idx = first_run.start()
    else:
        idx = m.start()

    new_text = text[:idx] + snippet + text[idx:]
    path.write_text(new_text, encoding="utf-8")
    result.patched_files.append(str(path))


_DOCKERFILE_SNIPPET = f"""
{_DOCKERFILE_MARKER}
ARG NEXT_PUBLIC_BASE_PATH=""
ENV NEXT_PUBLIC_BASE_PATH=${{NEXT_PUBLIC_BASE_PATH}}

"""
