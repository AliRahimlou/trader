from datetime import datetime, timedelta

import pytest

from pivot.feeds import ET
from pivot.models import Bar, MAG7
from pivot.strategy import leader_diagnostics
from research.data import at_time
from research.today_audit import (CUTOFF, DAY, OPEN, SYMBOLS, checkpoints, confirmation,
                                  coverage, five_minute_leaders, gate_record, summarize_records)
from research.tests.test_diagnostics import leader, repeated_zones


def candle(end, minutes=5, o=100, h=105, l=95, c=100):
    return Bar(end, minutes, o, h, l, c)


def test_five_minute_reaction_uses_true_frame_without_changing_baseline():
    at = OPEN + timedelta(minutes=10)
    markets = {s: leader(s, direction=None, now=at) for s in MAG7}
    five = {s: [candle(at - timedelta(minutes=5)), candle(at, l=89.9, c=104)] for s in MAG7}
    before = leader_diagnostics(markets, at)
    result = five_minute_leaders(markets, five, at)
    assert all(row["vote"] == "long" for row in result.values())
    assert all(row["latest_bar"]["minutes"] == 5 for row in result.values())
    assert all(row["vote"] is None for row in before.values())
    assert leader_diagnostics(markets, at) == before
    assert confirmation(result)["long_confirmation"]


def test_diagnostic_rejects_mislabeled_fifteen_minute_candles():
    at = OPEN + timedelta(minutes=15)
    with pytest.raises(ValueError, match="genuine five-minute"):
        five_minute_leaders({"AAPL": leader(now=at)}, {"AAPL": [candle(at, minutes=15)]}, at)


def test_future_five_minute_candle_cannot_change_past_vote():
    at = OPEN + timedelta(minutes=10)
    markets = {"AAPL": leader(now=at)}
    bars = [candle(at - timedelta(minutes=5)), candle(at)]
    before = five_minute_leaders(markets, {"AAPL": bars}, at)
    after = five_minute_leaders(markets, {"AAPL": bars + [candle(at + timedelta(minutes=5), l=89.9, c=104)]}, at)
    assert before == after
    assert after["AAPL"]["vote"] is None


def test_first_five_minute_bar_and_missing_previous_bar_are_explicit():
    at = OPEN + timedelta(minutes=5)
    row = five_minute_leaders({"AAPL": leader(now=at)}, {"AAPL": [candle(at, l=89.9, c=104)]}, at)["AAPL"]
    assert row["vote"] is None and "two actual" in row["reason"]
    at += timedelta(minutes=10)
    row = five_minute_leaders({"AAPL": leader(now=at)},
                              {"AAPL": [candle(at - timedelta(minutes=10)), candle(at, l=89.9, c=104)]}, at)["AAPL"]
    assert row["vote"] is None and "previous" in row["reason"]


def test_threshold_uses_four_distinct_companies_and_no_opposition():
    rows = {s: {"vote": "long" if i < 4 else None, "reason": "current reaction"} for i, s in enumerate(MAG7)}
    assert confirmation(rows)["long_confirmation"]
    rows[MAG7[-1]]["vote"] = "short"
    assert not confirmation(rows)["long_confirmation"]
    rows[MAG7[-1]] = {"vote": None, "reason": "current 5-minute data missing"}
    assert not confirmation(rows)["long_confirmation"]


def inputs():
    previous_open = OPEN - timedelta(days=1)
    sessions = {(OPEN - timedelta(days=1)).date().isoformat(): {"open": previous_open, "close": previous_open.replace(hour=16, minute=0)},
                DAY: {"open": OPEN, "close": OPEN.replace(hour=16, minute=0)}}
    history = [candle(at, 15) for at in checkpoints(previous_open, previous_open.replace(hour=16, minute=0), 15)]
    current = [candle(at, 15) for at in checkpoints(OPEN, CUTOFF, 15)]
    stocks15 = {s: history + current for s in SYMBOLS}
    stocks5 = {s: [candle(at) for at in checkpoints()] for s in SYMBOLS}
    return sessions, stocks15, stocks5


def test_gate_reconstruction_uses_actual_baseline_and_vix_stays_unknown():
    sessions, stocks15, stocks5 = inputs()
    at = OPEN + timedelta(hours=1)
    record = gate_record(sessions, stocks15, stocks5, at)
    markets, _ = at_time(sessions, stocks15, [], at)
    baseline = leader_diagnostics(markets, at)
    assert {s: r["vote"] for s, r in record["leaders_15m"].items()} == {s: r["vote"] for s, r in baseline.items()}
    assert record["vix"]["status"] == "unknown"
    assert record["could_trade_with_five_minute_leaders"] is None
    assert not record["five_minute_diagnostic_used_in_baseline"]
    assert not record["live_order_authorized"]
    assert all(datetime.fromisoformat(r["latest_bar"]["end"]) <= at for r in record["leaders_15m"].values())
    assert all(datetime.fromisoformat(r["latest_bar"]["end"]) <= at for r in record["leaders_5m"].values())


def test_all_source_future_prices_are_excluded_from_past_gate():
    sessions, stocks15, stocks5 = inputs()
    at = OPEN + timedelta(hours=1)
    before = gate_record(sessions, stocks15, stocks5, at)
    stocks15 = {s: [b if b.end <= at else candle(b.end, 15, o=500, h=510, l=490, c=505) for b in bars] for s, bars in stocks15.items()}
    stocks5 = {s: [b if b.end <= at else candle(b.end, 5, o=500, h=510, l=490, c=505) for b in bars] for s, bars in stocks5.items()}
    assert gate_record(sessions, stocks15, stocks5, at) == before


def test_coverage_includes_the_frozen_cutoff_bar_and_counts_missing_candles():
    sessions, stocks15, stocks5 = inputs()
    assert not any(row["missing_count"] for group in coverage(sessions, stocks15, stocks5).values() for row in group.values())
    stocks5["AAPL"] = stocks5["AAPL"][:-1]
    stocks15["QQQ"] = stocks15["QQQ"][:-1]
    result = coverage(sessions, stocks15, stocks5)
    assert result["today_5m"]["AAPL"]["missing_count"] == 1
    assert result["today_15m"]["QQQ"]["missing_count"] == 1


def test_checkpoint_and_quarter_hour_counts_are_frozen():
    sessions, stocks15, stocks5 = inputs()
    records = [gate_record(sessions, stocks15, stocks5, at) for at in checkpoints()]
    summary = summarize_records(records)
    assert len(records) == 33
    assert records[-1]["at"] == CUTOFF.isoformat()
    assert summary["all_5m_checkpoints"]["checkpoint_count"] == 33
    assert summary["quarter_hour_checkpoints"]["checkpoint_count"] == 11
