"""Phase N — render context shared by every preset.

A ``RenderContext`` is what each preset's ``render()`` method receives.
It carries the values the preset needs to interpolate into its
templates without leaking auth state (the token never enters here —
that's the responsibility of the pusher).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RenderContext:
    """Inputs every preset can rely on.

    * ``project_name`` — human-readable display name; used in README
      titles + LICENSE owner line.
    * ``slug`` — GAPT-internal kebab-case identifier; used wherever
      we need a filesystem-safe / URL-safe project name (Docker
      container names, compose service prefixes if the preset wants
      to namespace them).
    * ``repo_name`` — GitHub repository name. May differ from slug
      (operator can pick separately, per Phase N plan §Q1).
    * ``github_owner`` — the authenticated GitHub user's login;
      embedded in README clone instructions.
    * ``options`` — preset-specific knobs the operator answered in
      Step 3 of the wizard. Pre-validated by ``ScaffoldOption.validate``
      against the preset's schema before render runs.
    """

    project_name: str
    slug: str
    repo_name: str
    github_owner: str
    options: dict[str, Any] = field(default_factory=dict)
