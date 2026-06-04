"""Phase N — scaffold-based new project creation.

This package owns the end-to-end "create a fresh GitHub repo + push an
opinionated stack + register a GAPT project" flow. Components:

  github_client.py   — thin REST wrapper (token / scopes / repo lifecycle)
  token_resolver.py  — admin-scope vault lookup for `github_token`
  errors.py          — domain-specific error codes + HTTP mapping
  context.py         — RenderContext dataclass shared by every preset
  registry.py        — ScaffoldPreset / ScaffoldOption + registry surface
  pusher.py          — tempdir + git push helper
  presets/           — one module per preset

Public surface is whatever ``routers/scaffolds.py`` imports — keep the
package boundary tight so test mocks have a small footprint.
"""

from gapt_server.domains.scaffolds.errors import ScaffoldError, ScaffoldErrorCode
from gapt_server.domains.scaffolds.github_client import GithubClient, GithubRepoInfo

__all__ = [
    "GithubClient",
    "GithubRepoInfo",
    "ScaffoldError",
    "ScaffoldErrorCode",
]
