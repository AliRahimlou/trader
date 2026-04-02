from __future__ import annotations

from live_config import config_from_args, build_argument_parser
from live_logging import setup_console_logging
from operator_store import OperatorStore
from paper_engine import PaperTradingEngine


def main() -> None:
    parser = build_argument_parser()
    args = parser.parse_args()
    config = config_from_args(args)
    setup_console_logging(args.verbose)

    if not config.dry_run and not config.paper_confirm and not config.demo_mode:
        raise SystemExit("Refusing to run paper execution without --paper-confirm.")

    if config.reset_state and config.state_path.exists():
        config.state_path.unlink()

    store = OperatorStore(config.database_path)
    engine = PaperTradingEngine(config, store)

    if config.smoke_test:
        engine.run_smoke_test()
        return

    if config.once:
        engine.run_once()
        return

    engine.run_forever()


if __name__ == "__main__":
    main()
