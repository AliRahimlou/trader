from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests

from backtest_utils import normalize_ohlcv

PERIOD_RE = re.compile(r"^\s*(\d+)\s*([A-Za-z]+)\s*$")
TIMEFRAME_MAP = {
    "1m": (1, "minute"),
    "5m": (5, "minute"),
    "1d": (1, "day"),
}


@dataclass(frozen=True)
class MassiveConfig:
    api_key: str
    base_url: str = "https://api.massive.com"

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    @classmethod
    def from_env(cls, env_path: str = ".env") -> "MassiveConfig":
        load_env_file(env_path)
        api_key = _get_env_value("MASSIVE_API_KEY", "MASSIVE_API")
        if not api_key:
            raise ValueError("Missing Massive credentials: MASSIVE_API_KEY or MASSIVE_API.")
        return cls(api_key=api_key, base_url=os.getenv("MASSIVE_BASE_URL", "https://api.massive.com").rstrip("/"))


def load_env_file(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)
        os.environ.setdefault(key.replace("-", "_"), value)


def fetch_market_data(
    symbol: str,
    *,
    minute_period: str = "7d",
    five_min_period: str = "60d",
    daily_period: str = "1y",
    config: MassiveConfig,
) -> dict[str, pd.DataFrame]:
    end = pd.Timestamp.now(tz="UTC")
    return {
        "1m": fetch_aggregates(symbol, "1m", period_to_start(end, minute_period), end, config=config),
        "5m": fetch_aggregates(symbol, "5m", period_to_start(end, five_min_period), end, config=config),
        "1d": fetch_aggregates(symbol, "1d", period_to_start(end, daily_period), end, config=config),
    }


def fetch_aggregates(
    symbol: str,
    interval: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    config: MassiveConfig,
) -> pd.DataFrame:
    multiplier, timespan = TIMEFRAME_MAP[interval]
    encoded_symbol = quote(symbol.upper(), safe="")
    start_str = start.date().isoformat()
    end_str = end.date().isoformat()
    url = (
        f"{config.base_url}/v2/aggs/ticker/{encoded_symbol}/range/"
        f"{multiplier}/{timespan}/{start_str}/{end_str}"
    )
    response = requests.get(
        url,
        headers=config.headers,
        params={
            "adjusted": "false",
            "sort": "asc",
            "limit": 50_000,
        },
        timeout=30,
    )
    _raise_for_status(response, f"fetch Massive {interval} aggregates for {symbol}")
    payload = response.json()
    results = payload.get("results", [])
    if not results:
        raise ValueError(f"No Massive aggregates returned for {symbol} interval={interval}.")

    frame = pd.DataFrame(
        {
            "timestamp": [bar["t"] for bar in results],
            "open": [bar["o"] for bar in results],
            "high": [bar["h"] for bar in results],
            "low": [bar["l"] for bar in results],
            "close": [bar["c"] for bar in results],
            "volume": [bar.get("v", 0) for bar in results],
        }
    ).set_index("timestamp")
    frame.index = parse_massive_timestamps(frame.index, interval)
    return normalize_ohlcv(frame, interval=interval)


def parse_massive_timestamps(index: pd.Index, interval: str) -> pd.DatetimeIndex:
    timestamps = pd.to_datetime(index, unit="ms", utc=True)
    if interval == "1d":
        return timestamps.tz_convert("America/New_York").normalize()
    return timestamps


def period_to_start(end: pd.Timestamp, period: str) -> pd.Timestamp:
    match = PERIOD_RE.match(period)
    if not match:
        raise ValueError(f"Unsupported period format: {period}")

    value = int(match.group(1))
    unit = match.group(2).lower()
    if unit in {"m", "min", "mins", "minute", "minutes"}:
        return end - pd.Timedelta(minutes=value)
    if unit in {"h", "hr", "hrs", "hour", "hours"}:
        return end - pd.Timedelta(hours=value)
    if unit in {"d", "day", "days"}:
        return end - pd.Timedelta(days=value)
    if unit in {"w", "wk", "wks", "week", "weeks"}:
        return end - pd.Timedelta(weeks=value)
    if unit in {"mo", "mon", "month", "months"}:
        return end - pd.DateOffset(months=value)
    if unit in {"y", "yr", "yrs", "year", "years"}:
        return end - pd.DateOffset(years=value)
    raise ValueError(f"Unsupported period unit in {period}")


def _get_env_value(*keys: str) -> str:
    for key in keys:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


def _raise_for_status(response: requests.Response, action: str) -> None:
    if response.ok:
        return

    body = response.text.strip()
    if response.status_code == 429:
        raise RuntimeError(
            f"Failed to {action}: HTTP 429. Massive rate-limited the request under your current plan. "
            f"Response: {body}"
        )
    if response.status_code == 403:
        raise RuntimeError(
            f"Failed to {action}: HTTP 403. Massive accepted the request format but rejected "
            f"your current plan or entitlements. Response: {body}"
        )
    raise RuntimeError(f"Failed to {action}: HTTP {response.status_code}. Response: {body}")
