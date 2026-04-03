from __future__ import annotations

import argparse
import os
from dataclasses import replace

import uvicorn

from live_config import config_from_env, isolate_demo_runtime
from paper_api import create_app


def _env_truthy(name: str) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def create_runtime_app():
    config = config_from_env()
    if _env_truthy("PAPER_PLATFORM_FORCE_DEMO_MODE"):
        config = isolate_demo_runtime(replace(config, demo_mode=True, dry_run=True))
    return create_app(config)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local paper-trading backend API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--demo", action="store_true", help="Run in local demo mode without Alpaca calls.")
    parser.add_argument("--reload", action="store_true", help="Auto-reload the backend when Python files change.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.demo:
        os.environ["PAPER_PLATFORM_FORCE_DEMO_MODE"] = "true"
    else:
        os.environ.pop("PAPER_PLATFORM_FORCE_DEMO_MODE", None)

    if args.reload:
        uvicorn.run(
            "backend_server:create_runtime_app",
            factory=True,
            host=args.host,
            port=args.port,
            log_level="info",
            reload=True,
        )
        return

    uvicorn.run(create_runtime_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
