import argparse
import sys

from aiohttp import web

from gapt_runtime import __version__
from gapt_runtime.daemon import create_app
from gapt_runtime.settings import DaemonSettings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="toolkit-agent", description="GAPT runtime daemon")
    parser.add_argument("command", choices=["serve", "version"])
    parser.add_argument(
        "--socket",
        help="Unix socket path (default: GAPT_AGENT_SOCKET or /run/agent.sock)",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.command == "version":
        print(__version__)
        return 0

    settings = DaemonSettings.from_env()
    if args.socket:
        settings = DaemonSettings(
            socket_path=type(settings.socket_path)(args.socket),
            jwt_secret=settings.jwt_secret,
            project_id=settings.project_id,
            workspace_id=settings.workspace_id,
            session_id=settings.session_id,
            workspace_root=settings.workspace_root,
        )

    app = create_app(settings)
    web.run_app(app, path=str(settings.socket_path), print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
