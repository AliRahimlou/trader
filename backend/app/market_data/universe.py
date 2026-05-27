from dataclasses import dataclass


@dataclass(frozen=True)
class UniverseAsset:
    ticker: str
    name: str
    sector: str
    sector_etf: str
    tradable: bool = True


DEFAULT_UNIVERSE: list[UniverseAsset] = [
    UniverseAsset("AAPL", "Apple", "Technology", "XLK"),
    UniverseAsset("MSFT", "Microsoft", "Technology", "XLK"),
    UniverseAsset("NVDA", "NVIDIA", "Technology", "XLK"),
    UniverseAsset("AMD", "Advanced Micro Devices", "Technology", "XLK"),
    UniverseAsset("AVGO", "Broadcom", "Technology", "XLK"),
    UniverseAsset("AMZN", "Amazon", "Consumer Discretionary", "XLY"),
    UniverseAsset("TSLA", "Tesla", "Consumer Discretionary", "XLY"),
    UniverseAsset("HD", "Home Depot", "Consumer Discretionary", "XLY"),
    UniverseAsset("META", "Meta Platforms", "Communication Services", "XLC"),
    UniverseAsset("GOOGL", "Alphabet", "Communication Services", "XLC"),
    UniverseAsset("NFLX", "Netflix", "Communication Services", "XLC"),
    UniverseAsset("JPM", "JPMorgan Chase", "Financials", "XLF"),
    UniverseAsset("BAC", "Bank of America", "Financials", "XLF"),
    UniverseAsset("V", "Visa", "Financials", "XLF"),
    UniverseAsset("LLY", "Eli Lilly", "Health Care", "XLV"),
    UniverseAsset("UNH", "UnitedHealth", "Health Care", "XLV"),
    UniverseAsset("ABBV", "AbbVie", "Health Care", "XLV"),
    UniverseAsset("XOM", "Exxon Mobil", "Energy", "XLE"),
    UniverseAsset("CVX", "Chevron", "Energy", "XLE"),
    UniverseAsset("CAT", "Caterpillar", "Industrials", "XLI"),
    UniverseAsset("GE", "GE Aerospace", "Industrials", "XLI"),
    UniverseAsset("COST", "Costco", "Consumer Staples", "XLP"),
    UniverseAsset("WMT", "Walmart", "Consumer Staples", "XLP"),
    UniverseAsset("NEE", "NextEra Energy", "Utilities", "XLU"),
    UniverseAsset("LIN", "Linde", "Materials", "XLB"),
]

SECTOR_ETFS = sorted({asset.sector_etf for asset in DEFAULT_UNIVERSE}) + ["SPY"]


def get_asset(ticker: str) -> UniverseAsset | None:
    normalized = ticker.upper()
    return next((asset for asset in DEFAULT_UNIVERSE if asset.ticker == normalized), None)


def get_tradable_universe() -> list[UniverseAsset]:
    return [asset for asset in DEFAULT_UNIVERSE if asset.tradable]
