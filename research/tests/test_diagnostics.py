"""Golden baseline votes and point-in-time research-policy regressions."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from pivot.models import Bar, Market, MAG7
from research.baseline_v1 import AnalysisPolicy, leader_confirmation, leader_diagnostics, zones
from research.data import at_time, expected_ends


ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 16, 10, 30, tzinfo=ET)


def candle(at, o=100, h=105, l=95, c=100, minutes=15):
    return Bar(at, minutes, o, h, l, c)


def repeated_zones(now=NOW):
    ranges = [(105, 95), (110, 94), (105, 95), (106, 90),
              (105, 95), (110, 94), (105, 95), (106, 90), (105, 95)]
    return [candle(now - timedelta(hours=4 * (14 - i)), h=high, l=low, minutes=240)
            for i, (high, low) in enumerate(ranges)]


def reaction_bar(at, direction):
    if direction == "long":
        return candle(at, h=105, l=89.9, c=104)
    if direction == "short":
        return candle(at, h=110.1, l=95, c=96)
    return candle(at, h=105, l=95)


def leader(symbol="AAPL", direction="long", now=NOW, bars=None, history=None):
    return Market(symbol, {15: bars if bars is not None else [candle(now - timedelta(minutes=15)), reaction_bar(now, direction)],
                           240: history if history is not None else repeated_zones(now)}, "alpaca_iex", True, now)


@pytest.mark.parametrize("votes,direction,expected", [
    (["long"] * 4 + [None] * 3, "long", True),
    (["long"] * 3 + [None] * 4, "long", False),
    (["long"] * 4 + ["short"] + [None] * 2, "long", False),
    (["short"] * 4 + [None] * 3, "short", True),
    (["short"] * 7, "long", False),
    ([None] * 7, "short", False),
])
def test_default_votes_retain_production_golden_behavior(votes, direction, expected):
    leaders = {symbol: leader(symbol, vote) for symbol, vote in zip(MAG7, votes)}
    expected_detail = ", ".join(f"{symbol}: {vote or 'no zone reaction'}" for symbol, vote in zip(MAG7, votes))
    assert leader_confirmation(leaders, direction, NOW) == (expected, expected_detail)
    assert leader_confirmation(leaders, direction, NOW, AnalysisPolicy()) == (expected, expected_detail)
    assert [row["vote"] for row in leader_diagnostics(leaders, NOW).values()] == votes


def test_baseline_missing_data_retains_first_missing_company_message():
    leaders = {s: leader(s) for s in MAG7}
    leaders["MSFT"] = replace(leaders["MSFT"], observed_at=NOW - timedelta(seconds=91))
    leaders.pop("NVDA")
    assert leader_confirmation(leaders, "long", NOW) == (False, "MSFT: current 15-minute data missing")


def persistent_market(reaction_at=NOW - timedelta(minutes=15), latest_at=NOW):
    return leader(now=latest_at, bars=[candle(reaction_at - timedelta(minutes=15)),
                                     reaction_bar(reaction_at, "long"),
                                     candle(latest_at, o=104, h=106, l=103, c=104)])


def test_persistence_requires_same_active_event_and_reaction_after_event():
    market = persistent_market()
    policy = AnalysisPolicy(persistence_bars=2)
    row = leader_diagnostics({"AAPL": market}, NOW, policy, NOW - timedelta(minutes=15))["AAPL"]
    assert row["vote"] == "long"
    assert row["age_minutes"] == 15
    assert row["reason"] == "persistent reaction"
    assert leader_diagnostics({"AAPL": market}, NOW)["AAPL"]["vote"] is None
    assert leader_diagnostics({"AAPL": market}, NOW, policy, NOW)["AAPL"]["vote"] is None
    assert "no active Nasdaq" in leader_diagnostics({"AAPL": market}, NOW, policy)["AAPL"]["reason"]


def test_missing_candles_cannot_extend_persistence_age():
    market = persistent_market(reaction_at=NOW - timedelta(minutes=45))
    row = leader_diagnostics({"AAPL": market}, NOW, AnalysisPolicy(persistence_bars=3), NOW - timedelta(hours=1))["AAPL"]
    assert row["vote"] is None


def test_reaction_from_prior_session_is_not_carried():
    # The synthetic midnight boundary isolates session reset from age expiry.
    now = datetime(2026, 9, 17, 0, 0, tzinfo=ET)
    market = persistent_market(reaction_at=now - timedelta(minutes=15), latest_at=now)
    row = leader_diagnostics({"AAPL": market}, now, AnalysisPolicy(persistence_bars=2), now - timedelta(hours=1))["AAPL"]
    assert row["vote"] is None


def test_close_through_zone_invalidates_persistent_reaction():
    bars = [candle(NOW - timedelta(minutes=45)), reaction_bar(NOW - timedelta(minutes=30), "long"),
            candle(NOW - timedelta(minutes=15), o=88, h=89, l=87, c=88),
            candle(NOW, o=88, h=89, l=87, c=88)]
    market = leader(bars=bars)
    row = leader_diagnostics({"AAPL": market}, NOW, AnalysisPolicy(persistence_bars=3), NOW - timedelta(minutes=30))["AAPL"]
    assert row["vote"] is None
    assert row["reason"] == "invalidated by subsequent close through zone"


def test_newer_opposite_reaction_replaces_old_vote():
    bars = [candle(NOW - timedelta(minutes=30)), reaction_bar(NOW - timedelta(minutes=15), "long"),
            reaction_bar(NOW, "short")]
    row = leader_diagnostics({"AAPL": leader(bars=bars)}, NOW, AnalysisPolicy(persistence_bars=3), NOW - timedelta(minutes=30))["AAPL"]
    assert row["vote"] == "short"
    assert row["age_minutes"] == 0


def test_future_pivot_confirmation_cannot_create_earlier_reaction():
    # Two lows at 90 need the fifth bar to confirm the second pivot. That bar
    # closes only AFTER the earlier 15-minute reaction has occurred.
    points = [(105, 95), (105, 90), (105, 95), (105, 90), (105, 95)]
    history = [candle(NOW - timedelta(hours=16 - i * 4), h=h, l=l, minutes=240)
               for i, (h, l) in enumerate(points)]
    reaction_at = NOW - timedelta(minutes=15)
    market = persistent_market(reaction_at=reaction_at)
    market.bars[240] = history
    assert zones(history, NOW + timedelta(seconds=1))
    assert not zones(history, reaction_at - timedelta(minutes=15))
    row = leader_diagnostics({"AAPL": market}, NOW, AnalysisPolicy(persistence_bars=2), reaction_at)["AAPL"]
    assert row["vote"] is None


@pytest.mark.parametrize("kwargs", [
    {"zone_tolerance": float("nan")}, {"zone_tolerance": float("inf")},
    {"zone_tolerance": 0}, {"zone_tolerance": True},
    {"persistence_bars": 2.0}, {"persistence_bars": True},
    {"minimum_leaders": 4.5}, {"minimum_leaders": True},
    {"maximum_opposition": .5}, {"maximum_opposition": False},
])
def test_invalid_numeric_policies_rejected(kwargs):
    with pytest.raises(ValueError):
        AnalysisPolicy(**kwargs)


def session(day, close="16:00"):
    return {"open": datetime.fromisoformat(day + "T09:30").replace(tzinfo=ET),
            "close": datetime.fromisoformat(day + "T" + close).replace(tzinfo=ET)}


def test_point_in_time_excludes_future_candles_and_incomplete_aggregates():
    sessions = {"2026-09-16": session("2026-09-16")}
    source = [candle(end) for end in expected_ends(sessions["2026-09-16"])]
    cutoff = NOW - timedelta(minutes=15)
    markets, index = at_time(sessions, {"QQQ": source}, source, cutoff)
    assert markets["QQQ"].bars[15][-1].end == cutoff
    assert not markets["QQQ"].bars[60]
    assert not markets["QQQ"].bars[240]
    assert not markets["QQQ"].bars[1440]
    assert index.bars[15][-1].end == cutoff


def test_dst_and_early_close_calendar_preserve_real_session_boundaries():
    sessions = {"2026-03-06": session("2026-03-06"), "2026-03-09": session("2026-03-09"),
                "2026-11-27": session("2026-11-27", "13:00")}
    source = [candle(end) for s in sessions.values() for end in expected_ends(s)]
    for day in ("2026-03-06", "2026-03-09"):
        end = sessions[day]["close"]
        market = at_time(sessions, {"QQQ": source}, source, end)[0]["QQQ"]
        today_hourly = [b for b in market.bars[60] if b.end.astimezone(ET).date().isoformat() == day]
        assert today_hourly[0].end.astimezone(ET).strftime("%H:%M") == "10:30"
        assert today_hourly[0].end.astimezone(timezone.utc).hour == (15 if day.endswith("06") else 14)
    end = sessions["2026-11-27"]["close"]
    market = at_time(sessions, {"QQQ": source}, source, end)[0]["QQQ"]
    assert len(market.bars[15]) == 14
    assert len(market.bars[60]) == 3
    assert market.bars[60][-1].end.hour == 12
    assert not market.bars[240]
    assert len(market.bars[1440]) == 1
    assert market.bars[1440][0].end == end


def test_rolling_history_retains_whole_earliest_session_like_production():
    cutoff = datetime(2026, 9, 16, 15, 30, tzinfo=ET)
    earliest = (cutoff - timedelta(days=60)).date().isoformat()
    sessions = {earliest: session(earliest), "2026-09-16": session("2026-09-16")}
    source = [candle(end) for s in sessions.values() for end in expected_ends(s)]
    market = at_time(sessions, {"QQQ": source}, source, cutoff)[0]["QQQ"]
    assert market.bars[15][0].end == sessions[earliest]["open"] + timedelta(minutes=15)
    assert len(market.bars[1440]) == 1


def test_missing_source_bar_prevents_hour_four_hour_and_daily_fabrication():
    sessions = {"2026-09-16": session("2026-09-16")}
    source = [candle(end) for end in expected_ends(sessions["2026-09-16"])
              if end != NOW - timedelta(minutes=15)]
    market = at_time(sessions, {"QQQ": source}, source, sessions["2026-09-16"]["close"])[0]["QQQ"]
    assert len(market.bars[15]) == 25
    assert all(b.end != NOW for b in market.bars[60])
    assert not market.bars[240]
    assert not market.bars[1440]


@pytest.mark.parametrize("method,url", [
    ("POST", "https://api.alpaca.markets/v2/orders"),
    ("DELETE", "https://api.alpaca.markets/v2/orders"),
    ("GET", "https://api.alpaca.markets/v2/account"),
    ("GET", "https://example.com/v2/stocks/bars"),
    ("GET", "http://data.alpaca.markets/v2/stocks/bars"),
    ("GET", "https://api.insightsentry.com/v2/symbols/CBOE:VIX/series"),
])
def test_collector_rejects_unapproved_routes_without_network(method, url):
    from research.collect import ReadOnlySession
    with pytest.raises(RuntimeError, match="only calendar and historical-stock GET"):
        ReadOnlySession().request(method, url)


def test_collector_forces_redirects_off(monkeypatch):
    import requests
    from research.collect import ReadOnlySession
    recorded = {}
    def request(self, method, url, **kwargs):
        recorded.update(method=method, url=url, **kwargs)
        return "mock response"
    monkeypatch.setattr(requests.Session, "request", request)
    assert ReadOnlySession().request("GET", "https://data.alpaca.markets/v2/stocks/bars",
                                     allow_redirects=True) == "mock response"
    assert recorded["allow_redirects"] is False
