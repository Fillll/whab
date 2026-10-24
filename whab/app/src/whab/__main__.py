"""Run with: .venv/bin/python -m whab"""

import argparse
import asyncio
import os
import ssl

import uvicorn

from . import __version__
from .config import Settings
from .tls import validate_tls


def main():
    parser = argparse.ArgumentParser(description="Run WHAB on this computer.")
    parser.add_argument("--version", action="version", version=f"WHAB {__version__}")
    parser.add_argument("--port", type=int, help="Local HTTPS port (default: PORT or 5173)")
    parser.add_argument("--budget", help="Open a portable .whab budget file")
    parser.add_argument("--budget-password-env", metavar="VARIABLE", help="Read the budget password from this environment variable")
    parser.add_argument("--create", metavar="NAME", help="Create the --budget file with this budget name")
    parser.add_argument("--copy", action="append", default=[], metavar="FILE", help="Also save the opened budget to this unused file location")
    parser.add_argument("--recover-plugins", action="store_true", help="Start minimal local plugin recovery without opening plugin factories")
    parser.add_argument(
        "--reload", action="store_true", help="Reload Python files during development"
    )
    args = parser.parse_args()
    if args.recover_plugins and (args.create is not None or args.copy or args.reload):
        parser.error("Use --recover-plugins separately from --create, --copy and --reload.")
    if (args.create is not None or args.copy) and not args.budget:
        parser.error("Use --budget FILE with --create or --copy.")
    if args.budget_password_env is not None:
        if not args.budget:
            parser.error("Use --budget FILE with --budget-password-env.")
        if not args.budget_password_env or args.budget_password_env not in os.environ:
            parser.error("The named budget password environment variable is not set.")
        os.environ["WHAB_BUDGET_PASSWORD_ENV"] = args.budget_password_env
    if args.port is not None and not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535.")
    if args.port is not None:
        os.environ["PORT"] = str(args.port)
    if args.create is not None and not args.create.strip():
        parser.error("Give the new budget a nonempty name.")
    if args.budget:
        from pathlib import Path

        path = Path(args.budget).expanduser().resolve()
        if args.create is not None:
            if path.exists():
                parser.error("Choose an unused file location for the new budget.")
            from whab.app import create_app
            from whab.budgets.session import BudgetSession

            initial = Settings.load()
            try:
                validate_tls(initial.tls_cert_file, initial.tls_key_file)
            except ValueError as error:
                parser.error(str(error))
            session = BudgetSession.create(initial.home, initial.plaid_env, args.create, initial.plugins, initial.storage, path=path)
            created = create_app(settings=initial, budget_session=session)
            try:
                # Construction owns journals/workers before ASGI startup.
                runtime = getattr(created.state, "budget_runtime", None)
                if runtime is not None:
                    asyncio.run(runtime.shutdown())
            finally:
                created.state.store.close()
        os.environ["WHAB_BUDGET"] = str(path)
    if args.copy:
        os.environ["WHAB_BUDGET_COPIES"] = os.pathsep.join(str(Path(path).expanduser().resolve()) for path in args.copy)
    settings = Settings.load(read_plugin_selection=False) if args.recover_plugins else Settings.load()
    try:
        validate_tls(settings.tls_cert_file, settings.tls_key_file)
    except ValueError as error:
        parser.error(str(error))
    uvicorn.run(
        "whab.plugins.recovery:create_app" if args.recover_plugins else "whab.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=args.port or settings.port,
        reload=args.reload,
        access_log=False,
        log_level="warning",
        ssl_certfile=str(settings.tls_cert_file),
        ssl_keyfile=str(settings.tls_key_file),
        ssl_version=ssl.PROTOCOL_TLS_SERVER,
        proxy_headers=False,
        forwarded_allow_ips="",
    )


if __name__ == "__main__":
    main()
