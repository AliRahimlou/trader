from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from alpaca_api import fetch_stock_bars, period_to_start


@dataclass
class MarketContextCache:
    symbol: str
    alpaca_config: object
    minute_lookback: str
    five_minute_lookback: str
    daily_lookback: str
    minute_refresh_window: str
    five_minute_refresh_window: str
    daily_refresh_window: str
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)

    def refresh(self, end: pd.Timestamp) -> dict[str, pd.DataFrame]:
        end = _to_utc(end)
        self.frames["1m"] = self._refresh_timeframe(
            interval="1m",
            end=end,
            full_lookback=self.minute_lookback,
            refresh_window=self.minute_refresh_window,
        )
        self.frames["5m"] = self._refresh_timeframe(
            interval="5m",
            end=end,
            full_lookback=self.five_minute_lookback,
            refresh_window=self.five_minute_refresh_window,
        )
        self.frames["1d"] = self._refresh_timeframe(
            interval="1d",
            end=end,
            full_lookback=self.daily_lookback,
            refresh_window=self.daily_refresh_window,
        )
        return self.frames

    def _refresh_timeframe(
        self,
        *,
        interval: str,
        end: pd.Timestamp,
        full_lookback: str,
        refresh_window: str,
    ) -> pd.DataFrame:
        if interval not in self.frames:
            return fetch_stock_bars(
                self.symbol,
                interval,
                period_to_start(end, full_lookback),
                end,
                config=self.alpaca_config,
            )

        existing = self.frames[interval]
        refresh_start = period_to_start(end, refresh_window)
        if not existing.empty:
            refresh_start = min(refresh_start, existing.index.max().tz_convert("UTC"))

        incremental = fetch_stock_bars(
            self.symbol,
            interval,
            refresh_start,
            end,
            config=self.alpaca_config,
        )
        merged = pd.concat([existing, incremental]).sort_index()
        merged = merged[~merged.index.duplicated(keep="last")]
        return merged


def _to_utc(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")
