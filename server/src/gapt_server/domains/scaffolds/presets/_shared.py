"""Phase N.2.3 — preset render helpers shared across stack presets.

Holds the multi-line text constants the four stack presets reuse so
each preset module stays focused on its own structure.
"""

from __future__ import annotations

from datetime import datetime, timezone


LICENSE_MIT = """\
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

PYTHON_GITIGNORE = """\
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/

# Editors / OS
.vscode/
.idea/
.DS_Store
*.swp

# Env
.env
.env.local
.env.*.local

# Logs
*.log
"""

NODE_GITIGNORE = """\
# Node
node_modules/
.next/
out/
dist/
build/
.turbo/
.cache/

# Env
.env
.env.local
.env.*.local

# Editors / OS
.vscode/
.idea/
.DS_Store
*.swp

# Logs
*.log
npm-debug.log*
yarn-error.log*
pnpm-debug.log*
"""


def mit_license(github_owner: str) -> bytes:
    """Render the MIT LICENSE for the operator. Year is "now" in UTC
    so the resulting commit is deterministic per-day, not per-second."""
    year = datetime.now(tz=timezone.utc).year
    return LICENSE_MIT.format(year=year, github_owner=github_owner).encode("utf-8")


def combined_gitignore(*sections: str) -> bytes:
    """Concatenate two or more language gitignore sections with a
    blank-line separator. Order matters — duplicates aren't deduped
    (git treats them as no-ops) so this just stitches them."""
    parts = [s.rstrip() + "\n" for s in sections]
    return "\n".join(parts).encode("utf-8")
