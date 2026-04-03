from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, replace
from pathlib import Path

from alpaca_api import AlpacaConfig, load_env_file
from backtest_utils import BacktestConfig

SUPPORTED_STRATEGIES = ("break", "pullback", "both")
SUPPORTED_UNIVERSE_MODES = ("fixed", "alpaca_assets")


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_csv(name: str, default: str = "") -> tuple[str, ...]:
    raw = os.getenv(name, default)
    symbols = [item.strip().upper() for item in raw.split(",") if item.strip()]
    deduped = list(dict.fromkeys(symbols))
    return tuple(deduped)


def _bool_arg(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _demo_variant_path(path: Path) -> Path:
    if path.parts[:2] == ("runtime", "demo"):
        return path
    if path.parts and path.parts[0] == "runtime":
        suffix = Path(*path.parts[1:]) if len(path.parts) > 1 else Path()
        return Path("runtime") / "demo" / suffix
    if path.is_absolute():
        return path.parent / "demo" / path.name
    return Path("runtime") / "demo" / path


def isolate_demo_runtime(config: "PaperTradingConfig") -> "PaperTradingConfig":
    if not config.demo_mode:
        return config
    return replace(
        config,
        database_path=_demo_variant_path(config.database_path),
        log_dir=_demo_variant_path(config.log_dir),
        state_dir=_demo_variant_path(config.state_dir),
    )


@dataclass(frozen=True)
class PaperTradingConfig:
    symbol: str
    strategies: tuple[str, ...]
    universe_mode: str
    universe_symbols: tuple[str, ...]
    universe_max_symbols: int
    universe_refresh_seconds: int
    watchlist_size: int
    watchlist_hold_buffer: int
    pinned_symbols: tuple[str, ...]
    universe_allow_stocks: bool
    universe_allow_etfs: bool
    exclude_leveraged_etfs: bool
    scanner_min_price: float
    scanner_max_price: float
    scanner_min_avg_daily_volume: float
    scanner_max_spread_bps: float
    startup_timeout_seconds: float
    poll_seconds: float
    entry_timeout_seconds: int
    minute_lookback: str
    five_minute_lookback: str
    daily_lookback: str
    minute_refresh_window: str
    five_minute_refresh_window: str
    daily_refresh_window: str
    max_bar_age_seconds: int
    max_position_qty: float
    max_position_notional: float
    max_concurrent_positions: int
    max_capital_deployed: float
    max_capital_per_symbol: float
    max_daily_loss: float
    max_trades_per_day: int
    correlation_threshold: float
    one_position_per_symbol: bool
    cooldown_minutes: int
    flatten_at: str
    exit_mode: str
    allow_fractional_long: bool
    database_path: Path
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
    demo_mode: bool
    scanner_weight_liquidity: float
    scanner_weight_volatility: float
    scanner_weight_momentum: float
    scanner_weight_gap: float
    scanner_weight_trend: float
    scanner_weight_setup: float
    scanner_weight_spread: float
    scanner_weight_freshness: float

    @property
    def configured_symbols(self) -> tuple[str, ...]:
        ordered = [self.symbol, *self.universe_symbols, *self.pinned_symbols]
        return tuple(dict.fromkeys(symbol.upper() for symbol in ordered if symbol))

    @property
    def multi_symbol_mode(self) -> bool:
        return self.universe_mode != "fixed" or len(self.configured_symbols) > 1

    @property
    def state_path(self) -> Path:
        joined = "_".join(self.strategies)
        suffix = "dryrun" if self.dry_run else "paper"
        prefix = "multi" if self.multi_symbol_mode else self.symbol.lower()
        return self.state_dir / f"{prefix}_{joined}_{suffix}.json"

    @property
    def log_path(self) -> Path:
        joined = "_".join(self.strategies)
        suffix = "dryrun" if self.dry_run else "paper"
        prefix = "multi" if self.multi_symbol_mode else self.symbol.lower()
        return self.log_dir / f"{prefix}_{joined}_{suffix}.jsonl"


def build_argument_parser() -> argparse.ArgumentParser:
    load_env_file(".env")
    parser = argparse.ArgumentParser(description="Run the FVG strategy live on Alpaca paper.")
    parser.add_argument("--symbol", default=_env("LIVE_PAPER_SYMBOL", "SPY"))
    parser.add_argument(
        "--universe-mode",
        choices=SUPPORTED_UNIVERSE_MODES,
        default=_env("LIVE_PAPER_UNIVERSE_MODE", "alpaca_assets"),
    )
    parser.add_argument(
        "--universe-symbols",
        default=_env("LIVE_PAPER_UNIVERSE_SYMBOLS", ""),
    )
    parser.add_argument(
        "--universe-max-symbols",
        type=int,
        default=int(_env("LIVE_PAPER_UNIVERSE_MAX_SYMBOLS", "120")),
    )
    parser.add_argument(
        "--universe-refresh-seconds",
        type=int,
        default=int(_env("LIVE_PAPER_UNIVERSE_REFRESH_SECONDS", "180")),
    )
    parser.add_argument(
        "--watchlist-size",
        type=int,
        default=int(_env("LIVE_PAPER_WATCHLIST_SIZE", "10")),
    )
    parser.add_argument(
        "--watchlist-hold-buffer",
        type=int,
        default=int(_env("LIVE_PAPER_WATCHLIST_HOLD_BUFFER", "4")),
    )
    parser.add_argument(
        "--pinned-symbols",
        default=_env("LIVE_PAPER_PINNED_SYMBOLS", ""),
    )
    parser.add_argument(
        "--universe-allow-stocks",
        type=_bool_arg,
        default=_env_bool("LIVE_PAPER_UNIVERSE_ALLOW_STOCKS", True),
    )
    parser.add_argument(
        "--universe-allow-etfs",
        type=_bool_arg,
        default=_env_bool("LIVE_PAPER_UNIVERSE_ALLOW_ETFS", True),
    )
    parser.add_argument(
        "--exclude-leveraged-etfs",
        type=_bool_arg,
        default=_env_bool("LIVE_PAPER_EXCLUDE_LEVERAGED_ETFS", True),
    )
    parser.add_argument(
        "--scanner-min-price",
        type=float,
        default=float(_env("LIVE_PAPER_SCANNER_MIN_PRICE", "5")),
    )
    parser.add_argument(
        "--scanner-max-price",
        type=float,
        default=float(_env("LIVE_PAPER_SCANNER_MAX_PRICE", "0")),
    )
    parser.add_argument(
        "--scanner-min-avg-daily-volume",
        type=float,
        default=float(_env("LIVE_PAPER_SCANNER_MIN_AVG_DAILY_VOLUME", "1000000")),
    )
    parser.add_argument(
        "--scanner-max-spread-bps",
        type=float,
        default=float(_env("LIVE_PAPER_SCANNER_MAX_SPREAD_BPS", "35")),
    )
    parser.add_argument(
        "--startup-timeout-seconds",
        type=float,
        default=float(_env("LIVE_PAPER_STARTUP_TIMEOUT_SECONDS", "20")),
    )
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
        "--minute-refresh-window",
        default=_env("LIVE_PAPER_MINUTE_REFRESH_WINDOW", "2h"),
    )
    parser.add_argument(
        "--five-minute-refresh-window",
        default=_env("LIVE_PAPER_FIVE_MINUTE_REFRESH_WINDOW", "5d"),
    )
    parser.add_argument(
        "--daily-refresh-window",
        default=_env("LIVE_PAPER_DAILY_REFRESH_WINDOW", "20d"),
    )
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
        "--max-concurrent-positions",
        type=int,
        default=int(_env("LIVE_PAPER_MAX_CONCURRENT_POSITIONS", "3")),
    )
    parser.add_argument(
        "--max-capital-deployed",
        type=float,
        default=float(_env("LIVE_PAPER_MAX_CAPITAL_DEPLOYED", "60000")),
    )
    parser.add_argument(
        "--max-capital-per-symbol",
        type=float,
        default=float(_env("LIVE_PAPER_MAX_CAPITAL_PER_SYMBOL", "20000")),
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
    parser.add_argument(
        "--correlation-threshold",
        type=float,
        default=float(_env("LIVE_PAPER_CORRELATION_THRESHOLD", "0.92")),
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
    parser.add_argument(
        "--database-path",
        default=_env("LIVE_PAPER_DATABASE_PATH", "runtime/operator/paper_trading.db"),
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
        "--demo-mode",
        action="store_true",
        default=_env_bool("PAPER_PLATFORM_DEMO_MODE", False),
    )
    parser.add_argument(
        "--alpaca-feed",
        choices=("iex", "sip", "boats", "overnight"),
        default=_env("LIVE_PAPER_ALPACA_FEED", ""),
    )
    parser.add_argument(
        "--scanner-weight-liquidity",
        type=float,
        default=float(_env("LIVE_PAPER_SCANNER_WEIGHT_LIQUIDITY", "1.3")),
    )
    parser.add_argument(
        "--scanner-weight-volatility",
        type=float,
        default=float(_env("LIVE_PAPER_SCANNER_WEIGHT_VOLATILITY", "1.0")),
    )
    parser.add_argument(
        "--scanner-weight-momentum",
        type=float,
        default=float(_env("LIVE_PAPER_SCANNER_WEIGHT_MOMENTUM", "1.0")),
    )
    parser.add_argument(
        "--scanner-weight-gap",
        type=float,
        default=float(_env("LIVE_PAPER_SCANNER_WEIGHT_GAP", "0.8")),
    )
    parser.add_argument(
        "--scanner-weight-trend",
        type=float,
        default=float(_env("LIVE_PAPER_SCANNER_WEIGHT_TREND", "0.7")),
    )
    parser.add_argument(
        "--scanner-weight-setup",
        type=float,
        default=float(_env("LIVE_PAPER_SCANNER_WEIGHT_SETUP", "1.5")),
    )
    parser.add_argument(
        "--scanner-weight-spread",
        type=float,
        default=float(_env("LIVE_PAPER_SCANNER_WEIGHT_SPREAD", "0.8")),
    )
    parser.add_argument(
        "--scanner-weight-freshness",
        type=float,
        default=float(_env("LIVE_PAPER_SCANNER_WEIGHT_FRESHNESS", "0.9")),
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def config_from_args(args: argparse.Namespace) -> PaperTradingConfig:
    strategies = ("break", "pullback") if args.strategy == "both" else (args.strategy,)
    universe_symbols = _env_csv("LIVE_PAPER_UNIVERSE_SYMBOLS", args.universe_symbols)
    if not universe_symbols:
        universe_symbols = (args.symbol.upper(),)
    if args.symbol.upper() not in universe_symbols:
        universe_symbols = (args.symbol.upper(), *universe_symbols)
    pinned_symbols = _env_csv("LIVE_PAPER_PINNED_SYMBOLS", args.pinned_symbols)
    config = PaperTradingConfig(
        symbol=args.symbol.upper(),
        strategies=strategies,
        universe_mode=args.universe_mode,
        universe_symbols=universe_symbols,
        universe_max_symbols=args.universe_max_symbols,
        universe_refresh_seconds=args.universe_refresh_seconds,
        watchlist_size=args.watchlist_size,
        watchlist_hold_buffer=args.watchlist_hold_buffer,
        pinned_symbols=pinned_symbols,
        universe_allow_stocks=bool(args.universe_allow_stocks),
        universe_allow_etfs=bool(args.universe_allow_etfs),
        exclude_leveraged_etfs=bool(args.exclude_leveraged_etfs),
        scanner_min_price=args.scanner_min_price,
        scanner_max_price=args.scanner_max_price,
        scanner_min_avg_daily_volume=args.scanner_min_avg_daily_volume,
        scanner_max_spread_bps=args.scanner_max_spread_bps,
        startup_timeout_seconds=args.startup_timeout_seconds,
        poll_seconds=args.poll_seconds,
        entry_timeout_seconds=args.entry_timeout_seconds,
        minute_lookback=args.minute_lookback,
        five_minute_lookback=args.five_minute_lookback,
        daily_lookback=args.daily_lookback,
        minute_refresh_window=args.minute_refresh_window,
        five_minute_refresh_window=args.five_minute_refresh_window,
        daily_refresh_window=args.daily_refresh_window,
        max_bar_age_seconds=args.max_bar_age_seconds,
        max_position_qty=args.max_position_qty,
        max_position_notional=args.max_position_notional,
        max_concurrent_positions=args.max_concurrent_positions,
        max_capital_deployed=args.max_capital_deployed,
        max_capital_per_symbol=args.max_capital_per_symbol,
        max_daily_loss=args.max_daily_loss,
        max_trades_per_day=args.max_trades_per_day,
        correlation_threshold=args.correlation_threshold,
        one_position_per_symbol=not args.disable_one_position_per_symbol,
        cooldown_minutes=args.cooldown_minutes,
        flatten_at=args.flatten_at,
        exit_mode=args.exit_mode,
        allow_fractional_long=not args.no_fractional_long,
        database_path=Path(args.database_path),
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
        demo_mode=args.demo_mode,
        scanner_weight_liquidity=args.scanner_weight_liquidity,
        scanner_weight_volatility=args.scanner_weight_volatility,
        scanner_weight_momentum=args.scanner_weight_momentum,
        scanner_weight_gap=args.scanner_weight_gap,
        scanner_weight_trend=args.scanner_weight_trend,
        scanner_weight_setup=args.scanner_weight_setup,
        scanner_weight_spread=args.scanner_weight_spread,
        scanner_weight_freshness=args.scanner_weight_freshness,
    )
    return isolate_demo_runtime(config)


def config_from_env() -> PaperTradingConfig:
    parser = build_argument_parser()
    return config_from_args(parser.parse_args([]))


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
