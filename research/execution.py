"""Offline, deliberately conservative OHLC execution; no broker or live imports.

Bars are end-stamped. A decision at 10:00 can first use the OPEN of a bar
starting at 10:00, never an earlier price from the bar ending at 10:00.
Costs and fault scenarios are assumptions, not claims about any broker.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from math import floor, isfinite
from zoneinfo import ZoneInfo

from pivot.models import Bar


@dataclass(frozen=True)
class TradeSignal:
    setup_id: str
    at: datetime
    direction: str
    entry: float
    stop: float
    target: float


@dataclass(frozen=True)
class ExecutionConfig:
    starting_capital: float = 92.05
    trade_notional: float = 25.0
    spread_bps: float = 2.0
    slippage_bps: float = 2.0
    fee_bps: float = 0.0
    fee_per_order: float = 0.01
    fee_per_share: float = 0.0
    latency_seconds: float = 0.0
    max_entry_drift: float | None = 0.01
    entry_fill_fraction: float = 1.0
    reject_every_n: int = 0
    protection_failure_every_n: int = 0
    fractional_precision: int = 6
    min_trade_notional: float = 1.0
    timezone: str = "America/New_York"
    flatten_minutes_before_close: int = 5
    # ISO date -> aware session-close datetime (e.g. exchange-calendar early close).
    session_closes: dict[str, datetime] | None = None
    # ISO date -> new shares / old shares. Needed only if incomplete data leaves
    # overnight exposure. Signals must already use contemporaneous price units.
    split_factors: dict[str, float] | None = None
    # A durable live submission also consumes a slot when its outcome is
    # rejected/unknown. This simulator models rejected outcomes, not timeouts.
    max_entries_per_session: int | None = None

    def __post_init__(self):
        positive = (self.starting_capital, self.trade_notional, self.min_trade_notional)
        nonnegative = (self.spread_bps, self.slippage_bps, self.fee_bps,
                       self.fee_per_order, self.fee_per_share, self.latency_seconds)
        if not all(isfinite(v) and v > 0 for v in positive):
            raise ValueError("Capital and sizing values must be finite and positive")
        if not all(isfinite(v) and v >= 0 for v in nonnegative):
            raise ValueError("Costs and latency must be finite and nonnegative")
        if self.spread_bps / 2 + self.slippage_bps >= 10000:
            raise ValueError("Sell-side price haircut must be less than 100%")
        if not 0 < self.entry_fill_fraction <= 1:
            raise ValueError("entry_fill_fraction must be in (0, 1]")
        if self.max_entry_drift is not None and (not isfinite(self.max_entry_drift) or self.max_entry_drift < 0):
            raise ValueError("max_entry_drift must be nonnegative or None")
        if not isinstance(self.fractional_precision, int) or not 0 <= self.fractional_precision <= 9:
            raise ValueError("fractional_precision must be between 0 and 9")
        if not all(isinstance(v, int) for v in (self.reject_every_n, self.protection_failure_every_n,
                                               self.flatten_minutes_before_close)):
            raise ValueError("Fault cadence and close buffer must be integers")
        if min(self.reject_every_n, self.protection_failure_every_n,
               self.flatten_minutes_before_close) < 0:
            raise ValueError("Fault cadence and close buffer cannot be negative")
        ZoneInfo(self.timezone)
        for day, close in (self.session_closes or {}).items():
            _aware(close)
            if close.astimezone(ZoneInfo(self.timezone)).date().isoformat() != day:
                raise ValueError("Session close must belong to its local date")
        if not all(isfinite(v) and v > 0 for v in (self.split_factors or {}).values()):
            raise ValueError("Split factors must be finite and positive")
        if (self.max_entries_per_session is not None
                and (type(self.max_entries_per_session) is not int or self.max_entries_per_session < 0)):
            raise ValueError("Session entry cap must be a nonnegative integer or None")


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("All timestamps must include timezone")


def run_execution(bars: list[Bar], signals: list[TradeSignal],
                  config: ExecutionConfig | None = None) -> dict:
    """Replay one fractional, unlevered, long QQQ position at a time.

    The final bar spanning the scheduled exit deadline is flattened at its
    OPEN: with 15-minute data this is 15:45, not an invented 15:55 quote.
    Missing closing bars cause an explicit unresolved exposure; if a later
    session is available, it is liquidated at that session's first open with
    gap risk. No profitable overnight liquidation is silently assumed.
    """
    cfg = config or ExecutionConfig()
    tz = ZoneInfo(cfg.timezone)
    ordered_bars = sorted(bars, key=lambda b: b.end)
    # Stable chronological sorting retains the caller's point-in-time rank
    # among simultaneous candidates. An opaque event ID is not a ranking rule.
    ordered_signals = sorted(signals, key=lambda s: s.at)
    previous_end = None
    for bar in ordered_bars:
        _aware(bar.end)
        start = bar.end - timedelta(minutes=bar.minutes)
        if previous_end is not None and start < previous_end:
            raise ValueError("Execution bars must not overlap or duplicate")
        previous_end = bar.end
    for signal in ordered_signals:
        _aware(signal.at)

    cash = cfg.starting_capital
    position = None
    trades, events, rejections, equity = [], [], [], []
    accepted_setups = set()
    attempted_setups = set()
    cursor = 0
    attempt = 0
    position_count = 0
    turnover = 0.0
    exposure_minutes = 0.0
    max_notional = 0.0
    last_day = None
    last_mark = cfg.starting_capital
    day_start = cfg.starting_capital
    daily = []
    session_attempts = {}
    exposure_gaps = []
    previous_observed_end = None
    haircut = (cfg.spread_bps / 2 + cfg.slippage_bps) / 10000

    def event(kind, at, **details):
        events.append({"kind": kind, "at": at.isoformat(), **details})

    def reject(signal, reason, at):
        row = {"setup_id": signal.setup_id, "signal_at": signal.at.isoformat(),
               "at": at.isoformat(), "reason": reason}
        rejections.append(row)
        event("entry_rejected", at, setup_id=signal.setup_id, reason=reason)

    def fee(qty, price):
        return cfg.fee_per_order + cfg.fee_per_share * qty + qty * price * cfg.fee_bps / 10000

    def close_position(reference_price, at, reason, *, ambiguous=False):
        nonlocal cash, position, turnover, exposure_minutes
        p = position
        exit_price = reference_price * (1 - haircut)
        exit_fee = fee(p["quantity"], exit_price)
        proceeds = p["quantity"] * exit_price
        cash += proceeds - exit_fee
        turnover += proceeds
        exposure_minutes += max(0, (at - p["entered_dt"]).total_seconds() / 60)
        gross = p["quantity"] * (exit_price - p["entry_price"])
        trades.append({key: value for key, value in p.items() if key != "entered_dt"} | {
            "exit_at": at.isoformat(), "exit_price": exit_price,
            "exit_reference_price": reference_price, "exit_fee": exit_fee,
            "gross_pnl": gross, "net_pnl": gross - p["entry_fee"] - exit_fee,
            "fees": p["entry_fee"] + exit_fee, "reason": reason,
            "ohlc_ambiguous": ambiguous, "cash_after": cash,
            "exit_day": at.astimezone(tz).date().isoformat(),
        })
        event("exit", at, setup_id=p["setup_id"], reason=reason,
              quantity=p["quantity"], price=exit_price, fee=exit_fee,
              ohlc_ambiguous=ambiguous)
        position = None

    def mark(bar):
        nonlocal last_mark, max_notional
        notional = position["quantity"] * bar.close if position else 0.0
        # Liquidation value includes assumed exit costs for honest drawdown.
        last_mark = cash + (notional * (1 - haircut) - fee(position["quantity"],
                        bar.close * (1 - haircut)) if position else 0.0)
        max_notional = max(max_notional, notional)
        equity.append({"at": bar.end.isoformat(), "day": bar.end.astimezone(tz).date().isoformat(),
                       "equity": last_mark, "cash": cash, "exposure": notional,
                       "quantity": position["quantity"] if position else 0.0})

    for bar in ordered_bars:
        start = bar.end - timedelta(minutes=bar.minutes)
        local = start.astimezone(tz)
        day = local.date().isoformat()
        regular_open = datetime.combine(local.date(), time(9, 30), tz)
        regular_close = (cfg.session_closes or {}).get(day, datetime.combine(local.date(), time(16), tz))
        if start < regular_open or start >= regular_close or bar.end > regular_close:
            continue
        deadline = regular_close - timedelta(minutes=cfg.flatten_minutes_before_close)
        closing_bar = start >= deadline or bar.end > deadline or bar.end == regular_close

        if (position is not None and previous_observed_end is not None
                and start > previous_observed_end and last_day == day):
            # Missing prices may hide a stop/target hit. Endpoint accounting
            # remains a simulation, but cannot support an uncensored return.
            exposure_gaps.append({'after': previous_observed_end.isoformat(),
                                  'before': start.isoformat(), 'day': day,
                                  'setup_id': position['setup_id']})

        if last_day != day:
            if last_day is not None:
                daily.append({"day": last_day, "start_equity": day_start,
                              "end_equity": last_mark, "pnl": last_mark - day_start,
                              "unresolved_exposure": position is not None})
            day_start = last_mark
            if position is not None:
                factor = (cfg.split_factors or {}).get(day, 1.0)
                if factor != 1:
                    position["quantity"] *= factor
                    for key in ("entry_price", "entry_reference_price", "stop", "target"):
                        position[key] /= factor
                    event("split_adjustment", start, shares_multiplier=factor)
                event("missing_prior_session_liquidation", start, setup_id=position["setup_id"])
                close_position(bar.open, start, "missing_close_data_next_open")
            last_day = day

        had_position_at_open = position is not None
        if position and closing_bar:
            close_position(bar.open, start, "scheduled_session_close")
        elif position:
            stop_hit, target_hit = bar.low <= position["stop"], bar.high >= position["target"]
            if stop_hit:
                reference = min(bar.open, position["stop"])
                close_position(reference, start if bar.open <= position["stop"] else bar.end,
                               "stop", ambiguous=target_hit)
            elif target_hit:
                close_position(position["target"], bar.end, "target")

        # Like production, reserve a setup only after admission checks. A
        # preflight skip can qualify on a later snapshot; an actual submission
        # (including broker rejection) must never be retried for that setup.
        while cursor < len(ordered_signals):
            signal = ordered_signals[cursor]
            if signal.at + timedelta(seconds=cfg.latency_seconds) > start:
                break
            cursor += 1
            if signal.setup_id in attempted_setups:
                reject(signal, "duplicate_setup", start)
                continue
            if signal.at.astimezone(tz).date().isoformat() != day:
                reject(signal, "expired_session", start)
                continue
            if signal.direction != "long":
                reject(signal, "short_or_unknown_direction_disabled", start)
                continue
            if not all(isfinite(v) and v > 0 for v in (signal.entry, signal.stop, signal.target)) or not signal.stop < signal.entry < signal.target:
                reject(signal, "invalid_signal_prices", start)
                continue
            if closing_bar:
                reject(signal, "session_exit_window", start)
                continue
            if position is not None or had_position_at_open:
                reject(signal, "one_position_limit", start)
                continue
            price = bar.open * (1 + haircut)
            if not signal.stop < price < signal.target:
                reject(signal, "gap_invalidated_risk_levels", start)
                continue
            if cfg.max_entry_drift is not None and abs(bar.open / signal.entry - 1) > cfg.max_entry_drift + 1e-12:
                reject(signal, "entry_drift_limit", start)
                continue
            requested_qty = floor((cfg.trade_notional / price) * 10**cfg.fractional_precision) / 10**cfg.fractional_precision
            requested_cost = requested_qty * price + fee(requested_qty, price)
            if requested_cost > cash + 1e-9:
                reject(signal, "insufficient_buying_power", start)
                continue
            qty = floor(requested_qty * cfg.entry_fill_fraction * 10**cfg.fractional_precision) / 10**cfg.fractional_precision
            if qty <= 0 or qty * price < cfg.min_trade_notional:
                reject(signal, "below_minimum_size", start)
                continue
            if (cfg.max_entries_per_session is not None
                    and session_attempts.get(day, 0) >= cfg.max_entries_per_session):
                reject(signal, "session_entry_limit", start)
                continue
            attempted_setups.add(signal.setup_id)
            session_attempts[day] = session_attempts.get(day, 0) + 1
            attempt += 1
            if cfg.reject_every_n and attempt % cfg.reject_every_n == 0:
                reject(signal, "simulated_broker_rejection", start)
                continue
            entry_fee = fee(qty, price)
            cash -= qty * price + entry_fee
            turnover += qty * price
            position_count += 1
            accepted_setups.add(signal.setup_id)
            position = {"setup_id": signal.setup_id, "signal_at": signal.at.isoformat(),
                        "entry_at": start.isoformat(), "entered_dt": start,
                        "entry_day": day, "direction": "long", "quantity": qty,
                        "entry_price": price, "entry_reference_price": bar.open,
                        "stop": signal.stop, "target": signal.target,
                        "entry_fee": entry_fee, "partial_fill": cfg.entry_fill_fraction < 1}
            max_notional = max(max_notional, qty * price)
            event("entry", start, setup_id=signal.setup_id, quantity=qty,
                  price=price, fee=entry_fee, partial_fill=cfg.entry_fill_fraction < 1)
            if cfg.entry_fill_fraction < 1:
                event("unfilled_remainder_canceled", start, setup_id=signal.setup_id,
                      quantity=requested_qty - qty)
            if cfg.protection_failure_every_n and position_count % cfg.protection_failure_every_n == 0:
                # A deliberately adverse stress bound, not a causal intrabar
                # timing claim: emergency liquidation at the entry bar LOW.
                event("simulated_protection_failure", start, setup_id=signal.setup_id)
                close_position(bar.low, bar.end, "protection_failure_low_bound")
            else:
                event("assumed_protection_active", start, setup_id=signal.setup_id)
                stop_hit, target_hit = bar.low <= signal.stop, bar.high >= signal.target
                if stop_hit:
                    close_position(min(bar.open, signal.stop), bar.end, "stop", ambiguous=target_hit)
                elif target_hit:
                    close_position(signal.target, bar.end, "target")
            # No second trade on an OHLC bar whose internal sequencing is unknown.
            had_position_at_open = True
        mark(bar)
        previous_observed_end = bar.end

    for signal in ordered_signals[cursor:]:
        reject(signal, "no_executable_bar_after_signal", signal.at)
    if last_day is not None:
        daily.append({"day": last_day, "start_equity": day_start,
                      "end_equity": last_mark, "pnl": last_mark - day_start,
                      "unresolved_exposure": position is not None})
    if position is not None:
        event("unresolved_exposure_at_data_end", ordered_bars[-1].end,
              setup_id=position["setup_id"], quantity=position["quantity"])
    serialized_cfg = asdict(cfg)
    serialized_cfg["session_closes"] = {k: v.isoformat() for k, v in (cfg.session_closes or {}).items()}
    return {"config": serialized_cfg, "trades": trades, "events": events,
            "rejections": rejections, "equity": equity, "daily_equity": daily,
            "open_position": {k: v for k, v in position.items() if k != "entered_dt"} if position else None,
            "unobserved_exposure_intervals": exposure_gaps,
            "summary": {"starting_capital": cfg.starting_capital, "ending_cash": cash,
                        "ending_equity": last_mark, "net_pnl": last_mark - cfg.starting_capital,
                        "realized_net_pnl": sum(t["net_pnl"] for t in trades),
                        "turnover_dollars": turnover, "turnover_over_starting_capital": turnover / cfg.starting_capital,
                        "exposure_minutes_closed_trades": exposure_minutes,
                        "max_observed_position_notional": max_notional,
                        "independent_setup_count": len(accepted_setups),
                        "attempted_setup_count": len(attempted_setups),
                        "session_submission_attempts": session_attempts,
                        "price_path_complete": not exposure_gaps,
                        "closed_trade_count": len(trades), "unresolved_exposure": position is not None},
            "assumptions": ["Research simulation only; costs are hypothetical, not broker quotes or fees.",
                            "Long fractional QQQ, fixed dollar target, one position, no borrowing or shorts.",
                            "Entry uses next available bar open after decision plus configured latency.",
                            "Both levels touched: stop first. Adverse stop gaps fill at worse open.",
                            "Protective orders assumed active immediately except explicit fault stress.",
                            "Partial unfilled remainder canceled immediately; no partial exits modeled.",
                            "Protection failure uses entry-bar low liquidation as a pessimistic stress bound.",
                            "Exit uses open of bar spanning close buffer; no invented intrabar exit quote.",
                            "Without injected calendar, observed dates use 09:30–16:00 New York hours.",
                            "Missing closing data is explicitly flagged; later open bears overnight gap.",
                            "Missing intraday prices while exposed censor the path; returned P&L is an endpoint simulation, not a verified stop/target outcome.",
                            "Caller order ranks simultaneous candidates; a configured NY-session cap counts every attempted submission including rejection.",
                            "No dividends, financing interest, taxes, market impact, or borrow costs modeled."]}
