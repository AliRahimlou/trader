from __future__ import annotations

import argparse
from dataclasses import replace

import uvicorn

from live_config import config_from_env
from paper_api import create_app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local paper-trading backend API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--demo", action="store_true", help="Run in local demo mode without Alpaca calls.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = config_from_env()
    if args.demo:
        config = replace(config, demo_mode=True, dry_run=True)
    uvicorn.run(create_app(config), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
