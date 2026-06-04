"""Phase N.2.3 — empty preset render.

The simplest case: README, .gitignore, LICENSE. No docker, no compose,
no source. Operators picking this preset are bringing their own stack
and just want a starting commit that's not a literal empty tree
(which GitHub allows but is ugly to clone)."""

from __future__ import annotations

from gapt_server.domains.scaffolds.context import RenderContext
from gapt_server.domains.scaffolds.registry import ScaffoldPreset, register


_GITIGNORE = """\
# Editors
.vscode/
.idea/
*.swp
.DS_Store

# Logs
*.log
"""

_LICENSE_MIT = """\
MIT License

Copyright (c) {year} {github_owner}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

_README = """\
# {project_name}

Created with GAPT — empty preset.

```bash
git clone https://github.com/{github_owner}/{repo_name}.git
cd {repo_name}
```

Add your stack from here. The repo is intentionally bare so you can wire
in any framework / tooling combination you prefer — GAPT will track the
workspace you open against this repo from the IDE.
"""


def _render(ctx: RenderContext) -> dict[str, bytes]:
    from datetime import datetime, timezone  # noqa: PLC0415 — used only here

    year = datetime.now(tz=timezone.utc).year
    return {
        "README.md": _README.format(
            project_name=ctx.project_name,
            github_owner=ctx.github_owner,
            repo_name=ctx.repo_name,
        ).encode("utf-8"),
        ".gitignore": _GITIGNORE.encode("utf-8"),
        "LICENSE": _LICENSE_MIT.format(
            year=year, github_owner=ctx.github_owner
        ).encode("utf-8"),
    }


PRESET = ScaffoldPreset(
    id="empty",
    display_name="빈 프로젝트",
    description="README + .gitignore 만 — 내 스택은 내가 가져온다.",
    stack=("README", ".gitignore"),
    icon="square",
    deploy_target_kind="local",
    deploy_target_defaults={},
    option_schema=(),
    render=_render,
)

register(PRESET)
