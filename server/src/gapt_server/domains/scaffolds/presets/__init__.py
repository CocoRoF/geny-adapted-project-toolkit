"""Phase N — preset registration entry point.

Importing this package triggers the per-preset modules below to
register themselves via ``registry.register(...)``. The order of
imports here defines the order presets appear in the listing
endpoint (= the wizard's card grid order).

If you add a preset, append the import in the order you want it to
show up — the empty / "bring your own" preset stays LAST so the
wizard's primary CTA defaults to a real stack."""

# The 4 stack presets land before "empty" so the wizard's default
# selection is "fullstack" (the most-common starting point).
from gapt_server.domains.scaffolds.presets import fullstack_fastapi_nextjs  # noqa: F401
from gapt_server.domains.scaffolds.presets import backend_fastapi  # noqa: F401
from gapt_server.domains.scaffolds.presets import frontend_nextjs  # noqa: F401
from gapt_server.domains.scaffolds.presets import static_vite  # noqa: F401
from gapt_server.domains.scaffolds.presets import empty  # noqa: F401
