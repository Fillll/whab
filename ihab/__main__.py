"""Run with: .venv/bin/python -m ihab"""

import argparse
import os

import uvicorn

from . import __version__
from .config import Settings


def main():
    parser = argparse.ArgumentParser(description="Run IHAB on this computer.")
    parser.add_argument("--version", action="version", version=f"IHAB {__version__}")
    parser.add_argument("--port", type=int, help="Local HTTP port (default: PORT or 5173)")
    parser.add_argument(
        "--reload", action="store_true", help="Reload Python files during development"
    )
    args = parser.parse_args()
    if args.port is not None:
        if not 1024 <= args.port <= 65535:
            parser.error("Port must be between 1024 and 65535.")
        os.environ["PORT"] = str(args.port)
    settings = Settings.load()
    uvicorn.run(
        "ihab.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=args.port or settings.port,
        reload=args.reload,
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
