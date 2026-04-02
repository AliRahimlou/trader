from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from alpaca_api import AlpacaConfig, load_env_file
from backtest_utils import BacktestConfig

SUPPORTED_STRATEGIES = ("break", "pullback", "both")


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class PaperTradingConfig:
    symbol: str
    strategies: tuple[str, ...]
    poll_seconds: float
    entry_timeout_seconds: int
    minute_lookback: str
    five_minute_lookback: str
    daily_lookback: str
    max_bar_age_seconds: int
    max_position_qty: float
    max_position_notional: float
    max_daily_loss: float
    max_trades_per_day: int
    one_position_per_symbol: bool
    cooldown_minutes: int
    flatten_at: str
    exit_mode: str
    allow_fractional_long: bool
    log_dir: Path
    state_dir: Path
    strategy_config: BacktestConfig
    dry_run: bool
    once: bool
    reset_state: bool
    paper_confirm: bool
    smoke_test: bool
    smoke_test_symbol: str | None
    smoke_test_notional: float
    keep_state_days: int
    alpaca_feed: str | None

    @property
    def state_path(self) -> Path:
        joined = "_".join(self.strategies)
        suffix = "dryrun" if self.dry_run else "paper"
        return self.state_dir / f"{self.symbol.lower()}_{joined}_{suffix}.json"

    @property
    def log_path(self) -> Path:
        joined = "_".join(self.strategies)
        suffix = "dryrun" if self.dry_run else "paper"
        return self.log_dir / f"{self.symbol.lower()}_{joined}_{suffix}.jsonl"


def build_argument_parser() -> argparse.ArgumentParser:
    load_env_file(".env")
    parser = argparse.ArgumentParser(description="Run the FVG strategy live on Alpaca paper.")
    parser.add_argument("--symbol", default=_env("LIVE_PAPER_SYMBOL", "SPY"))
    parser.add_argument(
        "--strategy",
        choices=SUPPORTED_STRATEGIES,
        default=_env("LIVE_PAPER_STRATEGY", "both"),
    )
    parser.add_argument("--poll-seconds", type=float, default=float(_env("LIVE_PAPER_POLL_SECONDS", "5")))
    parser.add_argument(
        "--entry-timeout-seconds",
        type=int,
        default=int(_env("LIVE_PAPER_ENTRY_TIMEOUT_SECONDS", "30")),
    )
    parser.add_argument("--minute-lookback", default=_env("LIVE_PAPER_MINUTE_LOOKBACK", "3d"))
    parser.add_argument("--five-minute-lookback", default=_env("LIVE_PAPER_FIVE_MINUTE_LOOKBACK", "15d"))
    parser.add_argument("--daily-lookback", default=_env("LIVE_PAPER_DAILY_LOOKBACK", "60d"))
    parser.add_argument(
        "--max-bar-age-seconds",
        type=int,
        default=int(_env("LIVE_PAPER_MAX_BAR_AGE_SECONDS", "130")),
    )
    parser.add_argument("--risk-per-trade", type=float, default=float(_env("LIVE_PAPER_RISK_PER_TRADE", "100")))
    parser.add_argument("--rr-ratio", type=float, default=float(_env("LIVE_PAPER_RR_RATIO", "2")))
    parser.add_argument("--value-per-point", type=float, default=float(_env("LIVE_PAPER_VALUE_PER_POINT", "1")))
    parser.add_argument(
        "--commission-per-unit",
        type=float,
        default=float(_env("LIVE_PAPER_COMMISSION_PER_UNIT", "0")),
    )
    parser.add_argument("--min-gap-pct", type=float, default=float(_env("LIVE_PAPER_MIN_GAP_PCT", "0")))
    parser.add_argument("--min-gap-atr", type=float, default=float(_env("LIVE_PAPER_MIN_GAP_ATR", "0")))
    parser.add_argument(
        "--no-displacement",
        action="store_true",
        default=not _env_bool("LIVE_PAPER_REQUIRE_DISPLACEMENT", True),
    )
    parser.add_argument(
        "--max-position-qty",
        type=float,
        default=float(_env("LIVE_PAPER_MAX_POSITION_QTY", "100")),
    )
    parser.add_argument(
        "--max-position-notional",
        type=float,
        default=float(_env("LIVE_PAPER_MAX_POSITION_NOTIONAL", "25000")),
    )
    parser.add_argument(
        "--max-daily-loss",
        type=float,
        default=float(_env("LIVE_PAPER_MAX_DAILY_LOSS", "300")),
    )
    parser.add_argument(
        "--max-trades-per-day",
        type=int,
        default=int(_env("LIVE_PAPER_MAX_TRADES_PER_DAY", "2")),
    )
    parser.add_argument(
        "--disable-one-position-per-symbol",
        action="store_true",
        default=not _env_bool("LIVE_PAPER_ONE_POSITION_PER_SYMBOL", True),
    )
    parser.add_argument(
        "--cooldown-minutes",
        type=int,
        default=int(_env("LIVE_PAPER_COOLDOWN_MINUTES", "5")),
    )
    parser.add_argument("--flatten-at", default=_env("LIVE_PAPER_FLATTEN_AT", "15:55"))
    parser.add_argument(
        "--exit-mode",
        choices=("bracket", "in_process"),
        default=_env("LIVE_PAPER_EXIT_MODE", "bracket"),
    )
    parser.add_argument(
        "--no-fractional-long",
        action="store_true",
        default=not _env_bool("LIVE_PAPER_ALLOW_FRACTIONAL_LONG", True),
    )
    parser.add_argument("--log-dir", default=_env("LIVE_PAPER_LOG_DIR", "runtime/logs"))
    parser.add_argument("--state-dir", default=_env("LIVE_PAPER_STATE_DIR", "runtime/state"))
    parser.add_argument(
        "--keep-state-days",
        type=int,
        default=int(_env("LIVE_PAPER_KEEP_STATE_DAYS", "10")),
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset-state", action="store_true")
    parser.add_argument("--paper-confirm", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--smoke-test-symbol", default=_env("LIVE_PAPER_SMOKE_TEST_SYMBOL", ""))
    parser.add_argument(
        "--smoke-test-notional",
        type=float,
        default=float(_env("LIVE_PAPER_SMOKE_TEST_NOTIONAL", "10")),
    )
    parser.add_argument(
        "--alpaca-feed",
        choices=("iex", "sip", "boats", "overnight"),
        default=_env("LIVE_PAPER_ALPACA_FEED", ""),
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def config_from_args(args: argparse.Namespace) -> PaperTradingConfig:
    strategies = ("break", "pullback") if args.strategy == "both" else (args.strategy,)
    return PaperTradingConfig(
        symbol=args.symbol.upper(),
        strategies=strategies,
        poll_seconds=args.poll_seconds,
        entry_timeout_seconds=args.entry_timeout_seconds,
        minute_lookback=args.minute_lookback,
        five_minute_lookback=args.five_minute_lookback,
        daily_lookback=args.daily_lookback,
        max_bar_age_seconds=args.max_bar_age_seconds,
        max_position_qty=args.max_position_qty,
        max_position_notional=args.max_position_notional,
        max_daily_loss=args.max_daily_loss,
        max_trades_per_day=args.max_trades_per_day,
        one_position_per_symbol=not args.disable_one_position_per_symbol,
        cooldown_minutes=args.cooldown_minutes,
        flatten_at=args.flatten_at,
        exit_mode=args.exit_mode,
        allow_fractional_long=not args.no_fractional_long,
        log_dir=Path(args.log_dir),
        state_dir=Path(args.state_dir),
        strategy_config=BacktestConfig(
            risk_per_trade=args.risk_per_trade,
            rr_ratio=args.rr_ratio,
            value_per_point=args.value_per_point,
            commission_per_unit=args.commission_per_unit,
            min_gap_pct=args.min_gap_pct,
            min_gap_atr=args.min_gap_atr,
            require_displacement=not args.no_displacement,
        ),
        dry_run=args.dry_run,
        once=args.once,
        reset_state=args.reset_state,
        paper_confirm=args.paper_confirm,
        smoke_test=args.smoke_test,
        smoke_test_symbol=args.smoke_test_symbol.strip() or None,
        smoke_test_notional=args.smoke_test_notional,
        keep_state_days=args.keep_state_days,
        alpaca_feed=args.alpaca_feed or None,
    )


def build_alpaca_config(trading_config: PaperTradingConfig) -> AlpacaConfig:
    config = AlpacaConfig.from_env()
    if trading_config.alpaca_feed:
        config = AlpacaConfig(
            api_key_id=config.api_key_id,
            api_secret_key=config.api_secret_key,
            trading_base_url=config.trading_base_url,
            data_base_url=config.data_base_url,
            feed=trading_config.alpaca_feed,
        )
    return config
