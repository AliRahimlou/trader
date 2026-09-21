"""Descriptive replay results with deterministic trading-day clustered intervals.

Inference remains unavailable for a small number of observed trading days.
An interval is not evidence that a strategy will perform similarly in live use.
"""
from __future__ import annotations

from math import isfinite
from random import Random
from statistics import mean


def _quantile(values, q):
    ordered = sorted(values)
    offset = (len(ordered) - 1) * q
    lower = int(offset)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (offset - lower)


def summarize(result: dict, *, seed: int = 1729, bootstrap_samples: int = 2000,
              confidence_level: float = .95, minimum_sessions: int = 20) -> dict:
    """Summarize a run without discarding idle trading days or trading costs.

    Bootstrap units are complete observed trading days. All trades in a day
    travel together, retaining their within-day dependence. This method does
    not correct serial dependence between days or research selection bias.
    """
    if not 0 < confidence_level < 1:
        raise ValueError("confidence_level must be between 0 and 1")
    if bootstrap_samples < 100 or minimum_sessions < 2:
        raise ValueError("Use at least 100 bootstrap samples and 2 minimum sessions")
    summary = result["summary"]
    capital = summary["starting_capital"]
    if not isfinite(capital) or capital <= 0:
        raise ValueError("Starting capital must be finite and positive")
    trades = result["trades"]
    pnls = [t["net_pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    daily = result["daily_equity"]
    daily_pnls = [d["pnl"] for d in daily]
    day_trade_count = {}
    day_realized = {}
    for trade in trades:
        day = trade["exit_day"]
        day_trade_count[day] = day_trade_count.get(day, 0) + 1
        day_realized[day] = day_realized.get(day, 0.0) + trade["net_pnl"]
    realized_days = [day_realized.get(d["day"], 0.0) for d in daily]
    peak = capital
    drawdown = drawdown_pct = 0.0
    for point in result["equity"]:
        value = point["equity"]
        peak = max(peak, value)
        drawdown = max(drawdown, peak - value)
        drawdown_pct = max(drawdown_pct, (peak - value) / peak if peak > 0 else 0)
    loss_streak = win_streak = max_losses = max_wins = 0
    for pnl in pnls:
        loss_streak = loss_streak + 1 if pnl < 0 else 0
        win_streak = win_streak + 1 if pnl > 0 else 0
        max_losses, max_wins = max(max_losses, loss_streak), max(max_wins, win_streak)
    realized = sum(pnls)
    best_trade = max(pnls, default=0.0)
    best_day = max(realized_days, default=0.0)
    fees = sum(t["fees"] for t in trades)
    price_friction = sum(t["quantity"] * ((t["entry_price"] - t["entry_reference_price"])
                            + (t["exit_reference_price"] - t["exit_price"])) for t in trades)
    counts = [day_trade_count.get(d["day"], 0) for d in daily]
    unresolved_days = sum(bool(d.get("unresolved_exposure")) for d in daily)
    uncertainty = {"method": "trading_day_cluster_bootstrap", "available": False,
                   "confidence_level": confidence_level, "seed": seed,
                   "bootstrap_samples": bootstrap_samples,
                   "minimum_sessions": minimum_sessions,
                   "limitations": "Retains within-day dependence; does not model dependence between days, selection bias, or future market regimes."}
    if len(daily) < minimum_sessions:
        uncertainty["reason"] = f"Only {len(daily)} observed sessions; at least {minimum_sessions} required by this reporting rule."
    elif unresolved_days or summary.get("unresolved_exposure"):
        uncertainty["reason"] = "Incomplete session liquidation or unresolved exposure makes the result censored."
    elif summary.get("price_path_complete") is False:
        uncertainty["reason"] = "Missing intraday prices while exposed may hide stop/target events; the result is censored."
    elif sum(n > 0 for n in counts) < 5:
        uncertainty["reason"] = "Fewer than five sessions contain completed trades; uncertainty is inconclusive."
    else:
        rng = Random(seed)
        net_samples, mean_samples, expectancy_samples = [], [], []
        for _ in range(bootstrap_samples):
            indices = [rng.randrange(len(daily)) for _ in daily]
            net = sum(daily_pnls[i] for i in indices)
            realized_sample = sum(realized_days[i] for i in indices)
            count = sum(counts[i] for i in indices)
            net_samples.append(net)
            mean_samples.append(net / len(daily))
            if count:
                expectancy_samples.append(realized_sample / count)
        tail = (1 - confidence_level) / 2
        interval = lambda values: [_quantile(values, tail), _quantile(values, 1 - tail)]
        uncertainty.update({"available": True, "net_pnl_interval": interval(net_samples),
                            "mean_daily_pnl_interval": interval(mean_samples),
                            "expectancy_per_trade_interval": interval(expectancy_samples),
                            "resampled_zero_trade_periods": bootstrap_samples - len(expectancy_samples)})
    return {"period_start": daily[0]["day"] if daily else None,
            "period_end": daily[-1]["day"] if daily else None,
            "observed_sessions": len(daily), "active_sessions": sum(n > 0 for n in counts),
            "starting_capital": capital, "ending_equity": summary["ending_equity"],
            "net_profit": summary["net_pnl"], "return_on_starting_capital": summary["net_pnl"] / capital,
            "realized_net_profit": realized, "trade_count": len(trades),
            "independent_setup_count": len({t["setup_id"] for t in trades}),
            "expectancy_per_trade": mean(pnls) if pnls else None,
            "win_rate": len(wins) / len(pnls) if pnls else None,
            "average_win": mean(wins) if wins else None,
            "average_loss": mean(losses) if losses else None,
            "profit_factor": sum(wins) / -sum(losses) if losses else None,
            "max_drawdown_dollars": drawdown, "max_drawdown_fraction": drawdown_pct,
            "drawdown_basis": "Observed bar-end equity, after estimated liquidation costs; intrabar drawdown may be larger.",
            "worst_day": min(daily_pnls) if daily_pnls else None,
            "best_day": max(daily_pnls) if daily_pnls else None,
            "max_losing_streak": max_losses, "max_winning_streak": max_wins,
            "realized_net_without_best_trade": realized - best_trade,
            "realized_net_without_best_day": realized - best_day,
            "closed_trade_fees": fees, "closed_trade_spread_slippage_cost": price_friction,
            "turnover_dollars": summary["turnover_dollars"],
            "turnover_over_starting_capital": summary["turnover_over_starting_capital"],
            "max_observed_position_notional": summary["max_observed_position_notional"],
            "unresolved_exposure": summary.get("unresolved_exposure", False),
            "price_path_complete": summary.get("price_path_complete"),
            "unobserved_exposure_interval_count": len(result.get("unobserved_exposure_intervals", [])),
            "incomplete_session_count": unresolved_days,
            "rejection_count": len(result["rejections"]),
            "rejection_reasons": {reason: sum(r["reason"] == reason for r in result["rejections"])
                                  for reason in sorted({r["reason"] for r in result["rejections"]})},
            "uncertainty": uncertainty}


def cash_benchmark(starting_capital: float, sessions: int) -> dict:
    """Non-interest-bearing cash, matching replay capital and sample length."""
    return {"name": "cash", "starting_capital": starting_capital,
            "ending_equity": starting_capital, "net_profit": 0.0,
            "return_on_starting_capital": 0.0, "observed_sessions": sessions,
            "assumption": "No cash interest, deposits, withdrawals, or fees."}
