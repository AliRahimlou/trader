from backend.app.core.time_utils import utc_now
from backend.app.market_data.base import MarketDataStatus, Quote
from backend.app.market_data.historical import DemoHistoricalMarketData, _seed


class DemoMarketDataProvider(DemoHistoricalMarketData):
    def get_latest_quote(self, ticker: str) -> Quote:
        ticker = ticker.upper()
        latest = self.get_historical_bars(ticker, days=80)[-1]
        spread_bps = 4 + (_seed(ticker) % 24)
        half_spread = latest.close * spread_bps / 20_000
        bid = round(latest.close - half_spread, 2)
        ask = round(latest.close + half_spread, 2)
        return Quote(
            ticker=ticker,
            bid=bid,
            ask=ask,
            last=latest.close,
            spread_bps=float(spread_bps),
            timestamp=utc_now(),
            provider=self.provider_name,
        )

    def get_status(self) -> MarketDataStatus:
        return MarketDataStatus(
            provider=self.provider_name,
            connected=True,
            fresh=True,
            message="Demo provider is available with deterministic synthetic data.",
            checked_at=utc_now(),
        )
