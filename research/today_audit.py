"""Read-only September 17 gate reconstruction; never an executor or VIX client."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import gzip
import json
import subprocess
import sys
import types

from dotenv import dotenv_values

from pivot.feeds import ET, ReadOnlyFeeds
from pivot.history_health import frame_gaps
from pivot.models import MAG7, timestamp
from pivot.strategy import analyze, closed, leader_diagnostics, reaction, zones
from .collect import ReadOnlySession
from .data import at_time, encode_bar, load
from .offline import disconnected


DAY = "2026-09-17"
OPEN = datetime(2026, 9, 17, 9, 30, tzinfo=ET)
CUTOFF = datetime(2026, 9, 17, 12, 15, tzinfo=ET)
SYMBOLS = ("QQQ", *MAG7)


def freeze_strategy(commit):
    """Load pure analyzer bytes from the committed audit baseline, never a worker."""
    source = subprocess.check_output(["git", "show", f"{commit}:pivot/strategy.py"], stderr=subprocess.DEVNULL)
    module = types.ModuleType("pivot._today_audit_frozen_strategy")
    module.__package__ = "pivot"
    sys.modules[module.__name__] = module
    exec(compile(source, f"{commit}:pivot/strategy.py", "exec"), module.__dict__)
    for name in ("analyze", "closed", "leader_diagnostics", "reaction", "zones"):
        globals()[name] = getattr(module, name)
    return sha256(source).hexdigest()


def checkpoints(opened=OPEN, cutoff=CUTOFF, minutes=5):
    at = opened + timedelta(minutes=minutes)
    while at <= cutoff:
        yield at
        at += timedelta(minutes=minutes)


def _latest_evidence(row, bars):
    """Attach actual candle/zone distances without changing a vote."""
    result = dict(row)
    if not bars:
        result.update(latest_bar=None, nearest_zone=None)
        return result
    latest = bars[-1]
    result["latest_bar"] = encode_bar(latest)
    distances = []
    for zone in row["zones"]:
        distance = latest.close - zone["low"] if latest.close < zone["low"] else latest.close - zone["high"] if latest.close > zone["high"] else 0.0
        distances.append({**zone, "close_minus_nearest_boundary": distance,
                          "absolute_distance_fraction_of_close": abs(distance) / latest.close})
    result["nearest_zone"] = min(distances, key=lambda z: abs(z["close_minus_nearest_boundary"])) if distances else None
    return result


def five_minute_leaders(markets, five_minute_stocks, at):
    """Use genuine five-minute bars and production zone/reaction functions.

    Four-hour levels retain their actual four-hour history. No Market object
    containing a falsely labeled fifteen-minute candle is ever constructed.
    """
    rows = {}
    for symbol in MAG7:
        source = five_minute_stocks.get(symbol, [])
        if any(b.minutes != 5 for b in source):
            raise ValueError("Five-minute diagnostic requires genuine five-minute bars")
        if any(a.end >= b.end for a, b in zip(source, source[1:])):
            raise ValueError("Five-minute candles must be unique and ordered")
        bars = [b for b in source if b.end <= at and b.end.astimezone(ET).date() == at.astimezone(ET).date()]
        row = {"vote": None, "reason": "current 5-minute data missing", "zones": [],
               "reaction_at": None, "age_minutes": None}
        if symbol not in markets or not bars or not 0 <= (at - bars[-1].end).total_seconds() <= 390:
            rows[symbol] = _latest_evidence(row, bars)
            continue
        current = bars[-1]
        levels = zones(closed(markets[symbol], 240, at), current.end - timedelta(minutes=5), .001)
        row.update(latest_bar_at=current.end.isoformat(),
                   zones=[{"low": z.low, "high": z.high, "established_at": z.established_at.isoformat(),
                           "touches": z.touches} for z in levels],
                   reason="no zone reaction" if levels else "no eligible zones")
        if len(bars) < 2:
            row["reason"] = "two actual current-session 5-minute candles required"
        elif bars[-1].end - bars[-2].end != timedelta(minutes=5):
            row["reason"] = "previous 5-minute candle missing"
        else:
            vote = next((direction for zone in levels if (direction := reaction(bars[-2], current, zone))), None)
            if vote:
                row.update(vote=vote, reason="current reaction", reaction_at=current.end.isoformat(), age_minutes=0)
        rows[symbol] = _latest_evidence(row, bars)
    return rows


def confirmation(rows):
    """Same four-company/no-opposition gate, with missing frames explicit."""
    counts = Counter(row["vote"] for row in rows.values())
    missing = [symbol for symbol, row in rows.items()
               if "missing" in row["reason"] or "required" in row["reason"]]
    return {"long_votes": counts["long"], "short_votes": counts["short"],
            "long_confirmation": not missing and counts["long"] >= 4 and counts["short"] == 0,
            "short_confirmation": not missing and counts["short"] >= 4 and counts["long"] == 0,
            "missing_companies": missing}


def gate_record(sessions, stocks15, stocks5, at):
    markets, _ = at_time(sessions, stocks15, [], at)
    # Missing today's actual-index cache is UNKNOWN evidence, not a measured
    # negative VIX reaction. The baseline analyzer receives no VIX object.
    setup = analyze(markets["QQQ"], markets, None, at)
    baseline = leader_diagnostics(markets, at)
    rows15 = {symbol: _latest_evidence(row, closed(markets[symbol], 15, at))
              for symbol, row in baseline.items()}
    rows5 = five_minute_leaders(markets, stocks5, at)
    gates = [{**check, "evidence_status": "unknown" if check["name"] == "Actual VIX zone reaction" else "historical_reconstruction"}
             for check in setup["checks"]]
    event = {key: setup.get(key) for key in ("state", "event", "event_at", "entry", "direction", "stop", "target", "levels")}
    return {"at": at.isoformat(), "quarter_hour_boundary": at.minute % 15 == 0,
            "nasdaq": event, "baseline_gates": gates,
            "first_baseline_failed_gate": next((g["name"] for g in gates if not g["passed"]), None),
            "leaders_15m": rows15, "leaders_5m": rows5,
            "confirmation_15m": confirmation(rows15), "confirmation_5m": confirmation(rows5),
            "vix": {"status": "unknown", "current_session_evidence": False,
                    "reason": "No parent-supplied validated current-session actual-index cache", "api_requests": 0},
            "five_minute_diagnostic_used_in_baseline": False,
            "could_trade_with_five_minute_leaders": None,
            "live_order_authorized": False}


def coverage(sessions, stocks15, stocks5, cutoff=CUTOFF):
    historical = {day: session for day, session in sessions.items() if day <= DAY}
    today = {DAY: sessions[DAY]}
    # This is a retrospective closed-bar audit, so include the cutoff candle
    # itself rather than the live helper's 90-second publication grace.
    strict_cutoff = cutoff + timedelta(seconds=90)
    return {"history_15m": {s: frame_gaps(stocks15[s], historical, 15, strict_cutoff) for s in SYMBOLS},
            "today_15m": {s: frame_gaps(stocks15[s], today, 15, strict_cutoff) for s in SYMBOLS},
            "today_5m": {s: frame_gaps(stocks5[s], today, 5, strict_cutoff) for s in SYMBOLS}}


def summarize_records(records):
    groups = {"all_5m_checkpoints": records,
              "quarter_hour_checkpoints": [r for r in records if r["quarter_hour_boundary"]]}
    summary = {}
    for label, group in groups.items():
        summary[label] = {"checkpoint_count": len(group),
            "nasdaq_event_checkpoints": sum(bool(r["nasdaq"]["event_at"]) for r in group),
            "unique_nasdaq_events": sorted({(r["nasdaq"]["event_at"], r["nasdaq"]["event"]) for r in group if r["nasdaq"]["event_at"]}),
            "baseline_first_failed_gates": dict(Counter(r["first_baseline_failed_gate"] for r in group)),
            "15m_confirmed_checkpoints": sum(r["confirmation_15m"]["long_confirmation"] or r["confirmation_15m"]["short_confirmation"] for r in group),
            "5m_confirmed_checkpoints": sum(r["confirmation_5m"]["long_confirmation"] or r["confirmation_5m"]["short_confirmation"] for r in group),
            "5m_confirmed_with_nasdaq_event": sum(bool(r["nasdaq"]["event_at"]) and (r["confirmation_5m"]["long_confirmation"] or r["confirmation_5m"]["short_confirmation"]) for r in group),
            "per_company_reaction_checkpoints": {symbol: {
                "15m": sum(r["leaders_15m"][symbol]["vote"] is not None for r in group),
                "5m": sum(r["leaders_5m"][symbol]["vote"] is not None for r in group)} for symbol in MAG7}}
    return summary


def _immutable_json(path, value):
    content = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()
    with Path(path).open("xb") as stream:
        stream.write(content)
    return sha256(content).hexdigest()


def collect(base_path, env_path):
    """Only authenticated calendar/stock history GETs; never broker requests."""
    _, _, base_stocks, _, base_hash = load(base_path)
    start = (OPEN - timedelta(days=60)).replace(hour=0, minute=0, second=0, microsecond=0)
    values = dotenv_values(env_path)
    safe = {key: values[key] for key in ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "APCA_API_BASE_URL") if key in values}
    safe["APCA_API_FEED"] = "iex"
    feed = ReadOnlyFeeds(safe, session=ReadOnlySession())
    sessions = feed.stock_calendar(start, CUTOFF)
    if DAY not in sessions or sessions[DAY]["open"] != OPEN or sessions[DAY]["close"] < CUTOFF:
        raise ValueError("Current exchange calendar does not cover the frozen audit window")
    stocks15 = {symbol: [b for b in base_stocks[symbol]
                         if start <= b.end - timedelta(minutes=15) and b.end < OPEN] for symbol in SYMBOLS}
    fetched_days = []
    corrections_retained = 0
    for day, session in sorted(sessions.items()):
        if day >= DAY:
            continue
        missing = [s for s in SYMBOLS if frame_gaps(stocks15[s], {day: session}, 15, session["close"] + timedelta(seconds=90))["missing_count"]]
        if not missing:
            continue
        fetched = feed.stock_bars(missing, 15, session["open"], session["close"], sessions)
        for symbol, rows in fetched.items():
            existing = {b.end: b for b in stocks15[symbol]}
            for bar in rows:
                if bar.end in existing:
                    corrections_retained += existing[bar.end] != bar
                else:
                    existing[bar.end] = bar
            stocks15[symbol] = sorted(existing.values(), key=lambda b: b.end)
        fetched_days.append({"day": day, "symbols": missing})
    today15 = feed.stock_bars(list(SYMBOLS), 15, OPEN, CUTOFF, sessions)
    today5 = feed.stock_bars(list(SYMBOLS), 5, OPEN, CUTOFF, sessions)
    for symbol in SYMBOLS:
        stocks15[symbol].extend(today15[symbol])
    provenance = {"base_dataset_sha256": base_hash, "source": "Alpaca IEX",
                  "adjustment": "split", "history_start": start.isoformat(), "cutoff": CUTOFF.isoformat(),
                  "collected_at": datetime.now(timezone.utc).isoformat(),
                  "supplemental_history_days": fetched_days,
                  "provider_corrections_not_overwriting_frozen_bars": corrections_retained,
                  "vix_requests": 0, "broker_requests": 0,
                  "current_session_vix_available": False, "point_in_time_arrival_verified": False}
    return sessions, stocks15, today5, provenance


def render_report(records, summary, data_coverage, provenance):
    lines = ["# September 17 gate audit through 12:15 p.m. Eastern", "",
             "Historical reconstruction of the unchanged production gates and a separate five-minute leader comparison. Baseline eligibility follows the observed gates: a failed leader gate excludes entry independently of VIX. Today's actual VIX reaction and quote availability were not verified, so a complete counterfactual setup cannot be claimed.", "",
             "## Coverage", "",
             f"Cutoff: {provenance['cutoff']}. Source: Alpaca IEX, split-adjusted candles. VIX requests: 0; broker requests: 0.", "",
             "| Frame | Missing bars across all eight stocks |", "|---|---:|"]
    for frame, rows in data_coverage.items():
        lines.append(f"| {frame} | {sum(r['missing_count'] for r in rows.values())} |")
    lines += ["", "## Quarter-hour comparison", "",
              "Votes are current long/short reactions. Four matching companies and no opposing company are required.", "",
              "| Eastern | Nasdaq event | 15m long / short | 5m long / short | First baseline blocker |",
              "|---|---|---:|---:|---|"]
    for record in records:
        if not record["quarter_hour_boundary"]:
            continue
        a, b = record["confirmation_15m"], record["confirmation_5m"]
        lines.append(f"| {timestamp(record['at']).astimezone(ET):%H:%M} | {record['nasdaq']['event'] or 'none'} | {a['long_votes']} / {a['short_votes']} | {b['long_votes']} / {b['short_votes']} | {record['first_baseline_failed_gate'] or 'none'} |")
    lines += ["", "## Comparison totals", ""]
    for label, row in summary.items():
        lines.append(f"- {label}: {row['checkpoint_count']} checkpoints; 15m leaders confirmed at {row['15m_confirmed_checkpoints']}; 5m leaders confirmed at {row['5m_confirmed_checkpoints']}; 5m leaders plus an existing Nasdaq event at {row['5m_confirmed_with_nasdaq_event']}.")
    lines += ["", "## Evidence limits", "",
              "- Five-minute diagnostics never replace the production analyzer or its fifteen-minute bars.",
              "- Reconstructed observation clocks do not prove live receipt times or quote freshness. Exact-boundary evaluations assume the just-closed candle was available.",
              "- A missing VIX observation is unknown, not a measured failure of the VIX strategy condition. No extra VIX requests were made.",
              "- IEX is one exchange. Frozen and newly fetched split-adjusted history can differ through provider corrections or corporate-action adjustments.",
              "- Missing bars are reported, never filled. The first current-session five-minute candle cannot establish a two-candle reaction.",
              "- No execution, fills, returns, profitability, strategy selection, or live authorization is inferred.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dataset", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--plan", default="docs/research/today-audit-plan.json")
    parser.add_argument("--plan-commit", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    if out.exists():
        raise SystemExit("Refusing to overwrite immutable current-day audit")
    plan_bytes = Path(args.plan).read_bytes()
    plan = json.loads(plan_bytes)
    if plan["version"] != "pivot-today-gates-20260917-v1" or timestamp(plan["bar_cutoff"]) != CUTOFF:
        raise SystemExit("Unexpected frozen plan")
    if len(args.plan_commit) != 40 or any(c not in "0123456789abcdef" for c in args.plan_commit):
        raise SystemExit("An explicit full frozen-plan commit identifier is required")
    baseline_hash = freeze_strategy(args.plan_commit)
    sessions, stocks15, stocks5, provenance = collect(args.base_dataset, args.env_file)
    with disconnected():
        records = [gate_record(sessions, stocks15, stocks5, at) for at in checkpoints()]
        data_coverage = coverage(sessions, stocks15, stocks5)
        summary = summarize_records(records)
    provenance.update(plan_sha256=sha256(plan_bytes).hexdigest(), plan_commit=args.plan_commit,
                      strategy_baseline_commit=args.plan_commit, strategy_source_sha256=baseline_hash,
                      replay_network_fence=True)
    dataset = {"schema": "pivot-today-gates-bars-v1", "provenance": provenance,
               "sessions": {day: {k: v.isoformat() for k, v in session.items()} for day, session in sessions.items()},
               "stocks_15m": {s: [encode_bar(b) for b in rows] for s, rows in stocks15.items()},
               "stocks_5m": {s: [encode_bar(b) for b in rows] for s, rows in stocks5.items()}}
    out.mkdir(parents=True)
    content = gzip.compress(json.dumps(dataset, sort_keys=True, allow_nan=False, separators=(",", ":")).encode(), mtime=0)
    with (out / "dataset.json.gz").open("xb") as stream:
        stream.write(content)
    (out / "dataset.json.gz").chmod(0o600)
    result = {"provenance": provenance, "dataset_sha256": sha256(content).hexdigest(),
              "coverage": data_coverage, "summary": summary, "records": records,
              "vix_status": "unknown", "live_order_authorized": False}
    result_hash = _immutable_json(out / "results.json", result)
    _immutable_json(out / "manifest.json", {"provenance": provenance, "dataset_sha256": sha256(content).hexdigest(),
                                           "results_sha256": result_hash, "summary": summary})
    with (out / "REPORT.md").open("x") as stream:
        stream.write(render_report(records, summary, data_coverage, provenance))
    print(json.dumps({"dataset_sha256": sha256(content).hexdigest(), "summary": summary, "vix_status": "unknown"}, indent=2))


if __name__ == "__main__":
    main()
