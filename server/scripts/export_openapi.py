"""Dump the FastAPI OpenAPI spec to ``web/src/api/openapi.json``.

The web app's TS codegen consumes this file so its `api/types.ts`
stays in lockstep with the server. The script is intentionally tiny
so it can run in CI as a doc-freshness check.

Usage:
    uv run python scripts/export_openapi.py            # writes default location
    uv run python scripts/export_openapi.py --check    # fails if drift detected
    uv run python scripts/export_openapi.py --out path/to/openapi.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gapt_server.app import create_app

SERVER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVER_ROOT.parent
DEFAULT_OUT = REPO_ROOT / "web" / "src" / "api" / "openapi.json"


def _dump() -> str:
    app = create_app()
    spec = app.openapi()
    # Stable formatting so the file stays diff-friendly.
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="Where to write the spec."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare the live spec against --out and exit non-zero on drift.",
    )
    args = parser.parse_args()

    current = _dump()

    if args.check:
        if not args.out.exists():
            print(f"openapi file missing: {args.out}", file=sys.stderr)
            return 1
        existing = args.out.read_text(encoding="utf-8")
        if existing.strip() != current.strip():
            print(
                "openapi spec drift detected — re-run "
                "`uv run python scripts/export_openapi.py` and commit the result.",
                file=sys.stderr,
            )
            return 1
        print("openapi spec up to date")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(current, encoding="utf-8")
    print(f"wrote {args.out} ({len(current)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
