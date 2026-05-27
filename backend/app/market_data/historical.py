import math
import random
from datetime import timedelta

from backend.app.core.time_utils import ny_now
from backend.app.market_data.base import Bar


def _seed(ticker: str) -> int:
    return sum((idx + 1) * ord(char) for idx, char in enumerate(ticker.upper()))


def _profile(ticker: str) -> tuple[float, float, float]:
    seed = _seed(ticker)
    base_price = 25 + seed % 420
    drift = ((seed % 17) - 6) / 10_000
    volatility = 0.009 + (seed % 13) / 1_000
    return float(base_price), drift, volatility


class DemoHistoricalMarketData:
    provider_name = "demo"

    def get_historical_bars(self, ticker: str, days: int = 180) -> list[Bar]:
        ticker = ticker.upper()
        base_price, drift, volatility = _profile(ticker)
        rng = random.Random(_seed(ticker) + days)
        today = ny_now().replace(hour=16, minute=0, second=0, microsecond=0)
        bars: list[Bar] = []
        price = base_price
        cursor = today - timedelta(days=days * 2)
        while len(bars) < days:
            cursor += timedelta(days=1)
            if cursor.weekday() >= 5:
                continue
            cycle = math.sin(len(bars) / 9.0 + (_seed(ticker) % 11))
            shock = rng.gauss(drift + cycle * volatility * 0.18, volatility)
            open_price = max(2.0, price * (1 + rng.gauss(0, volatility / 3)))
            close = max(2.0, open_price * (1 + shock))
            high = max(open_price, close) * (1 + abs(rng.gauss(volatility / 2, volatility / 4)))
            low = min(open_price, close) * (1 - abs(rng.gauss(volatility / 2, volatility / 4)))
            volume_base = 750_000 + (_seed(ticker) % 6_000_000)
            volume = int(volume_base * (1 + abs(rng.gauss(0.0, 0.25))))
            bars.append(
                Bar(
                    timestamp=cursor,
                    open=round(open_price, 2),
                    high=round(high, 2),
                    low=round(low, 2),
                    close=round(close, 2),
                    volume=volume,
                )
            )
            price = close
        return bars
