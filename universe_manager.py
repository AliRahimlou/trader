from __future__ import annotations

from dataclasses import dataclass

from alpaca_api import AlpacaConfig, get_asset, list_assets
from scanner_models import UniverseMember

ETF_KEYWORDS = (
    " etf",
    " fund",
    " trust",
    " shares",
    " index",
    " treasury",
    " bond",
)
LEVERAGED_KEYWORDS = (
    "2x",
    "3x",
    " ultra",
    " leverage",
    " leveraged",
    " bull",
    " bear",
    " inverse",
)


@dataclass
class UniverseManager:
    config: object
    alpaca_config: AlpacaConfig | None

    def load(self) -> list[UniverseMember]:
        if self.alpaca_config is None:
            return [self._placeholder_member(symbol) for symbol in self.config.configured_symbols]

        if self.config.universe_mode == "alpaca_assets":
            assets = list_assets(self.alpaca_config, status="active", asset_class="us_equity")
            members = [self._asset_to_member(asset) for asset in assets]
        else:
            members = []
            for symbol in self.config.configured_symbols:
                try:
                    members.append(self._asset_to_member(get_asset(self.alpaca_config, symbol)))
                except Exception:
                    members.append(self._placeholder_member(symbol))

        deduped: dict[str, UniverseMember] = {}
        for member in members:
            if not self._member_allowed(member):
                continue
            deduped[member.symbol] = member
        for symbol in self.config.configured_symbols:
            deduped.setdefault(symbol, self._placeholder_member(symbol))
        return sorted(deduped.values(), key=lambda member: member.symbol)

    def _asset_to_member(self, asset: dict[str, object]) -> UniverseMember:
        symbol = str(asset.get("symbol") or "").upper()
        name = str(asset.get("name") or symbol)
        lower_name = name.lower()
        is_etf = any(keyword in lower_name for keyword in ETF_KEYWORDS)
        is_leveraged = any(keyword in lower_name for keyword in LEVERAGED_KEYWORDS)
        return UniverseMember(
            symbol=symbol,
            name=name,
            exchange=str(asset.get("exchange") or "") or None,
            asset_class=str(asset.get("class") or "us_equity"),
            tradable=bool(asset.get("tradable", False)),
            shortable=bool(asset.get("shortable", False)),
            fractionable=bool(asset.get("fractionable", False)),
            easy_to_borrow=bool(asset.get("easy_to_borrow", False)),
            is_etf=is_etf,
            is_leveraged=is_leveraged,
        )

    def _member_allowed(self, member: UniverseMember) -> bool:
        if not member.tradable:
            return False
        if member.is_etf and not self.config.universe_allow_etfs:
            return False
        if not member.is_etf and not self.config.universe_allow_stocks:
            return False
        if self.config.exclude_leveraged_etfs and member.is_leveraged:
            return False
        return True

    def _placeholder_member(self, symbol: str) -> UniverseMember:
        return UniverseMember(symbol=symbol.upper(), name=symbol.upper(), tradable=True)
