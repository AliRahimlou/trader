from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd
import pandas_ta as ta
import yfinance as yf

ET_TZ = "America/New_York"
LOGGER = logging.getLogger(__name__)
OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


@dataclass(frozen=True)
class BacktestConfig:
    risk_per_trade: float = 100.0
    rr_ratio: float = 2.0
    value_per_point: float = 1.0
    commission_per_unit: float = 0.0
    slippage_bps: float = 0.0
    min_gap_pct: float = 0.0
    min_gap_atr: float = 0.0
    require_displacement: bool = True


def configure_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def download_market_data(
    symbol: str,
    minute_period: str = "7d",
    five_min_period: str = "60d",
    daily_period: str = "1y",
) -> dict[str, pd.DataFrame]:
    LOGGER.info("Downloading market data for %s", symbol)
    data = {
        "1m": normalize_ohlcv(
            yf.download(
                symbol,
                interval="1m",
                period=minute_period,
                auto_adjust=False,
                progress=False,
                threads=False,
            ),
            interval="1m",
        ),
        "5m": normalize_ohlcv(
            yf.download(
                symbol,
                interval="5m",
                period=five_min_period,
                auto_adjust=False,
                progress=False,
                threads=False,
            ),
            interval="5m",
        ),
        "1d": normalize_ohlcv(
            yf.download(
                symbol,
                interval="1d",
                period=daily_period,
                auto_adjust=False,
                progress=False,
                threads=False,
            ),
            interval="1d",
        ),
    }
    LOGGER.info(
        "Loaded %s rows: 1m=%s 5m=%s 1d=%s",
        symbol,
        len(data["1m"]),
        len(data["5m"]),
        len(data["1d"]),
    )
    return data


def normalize_ohlcv(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    if df.empty:
        raise ValueError(f"No data returned for interval={interval}.")

    normalized = df.copy()
    if isinstance(normalized.columns, pd.MultiIndex):
        normalized.columns = normalized.columns.get_level_values(0)

    normalized.columns = [str(column).strip().lower() for column in normalized.columns]
    if "adj close" in normalized.columns and "close" not in normalized.columns:
        normalized["close"] = normalized["adj close"]

    missing_columns = [column for column in OHLCV_COLUMNS if column not in normalized.columns]
    if missing_columns:
        raise ValueError(f"Missing OHLCV columns for interval={interval}: {missing_columns}")

    normalized = normalized[OHLCV_COLUMNS].copy()
    normalized.index = pd.to_datetime(normalized.index)
    if normalized.index.tz is None:
        normalized.index = normalized.index.tz_localize(ET_TZ)
    else:
        normalized.index = normalized.index.tz_convert(ET_TZ)

    normalized = normalized.sort_index()
    normalized = normalized[~normalized.index.duplicated(keep="last")]
    normalized = normalized.dropna(subset=["open", "high", "low", "close"])
    normalized["volume"] = normalized["volume"].fillna(0)
    normalized["atr"] = ta.atr(
        normalized["high"],
        normalized["low"],
        normalized["close"],
        length=14,
    )
    return normalized


def restrict_to_regular_hours(
    df: pd.DataFrame,
    start: str = "09:30",
    end: str = "16:00",
) -> pd.DataFrame:
    if df.empty:
        return df
    return df.between_time(start_time=start, end_time=end, inclusive="both").copy()


def get_fair_value_gap_direction(
    df: pd.DataFrame,
    i: int,
    *,
    min_gap_pct: float = 0.0,
    min_gap_atr: float = 0.0,
    require_displacement: bool = True,
) -> str | None:
    if i < 2:
        return None

    c1, c2, c3 = df.iloc[i - 2], df.iloc[i - 1], df.iloc[i]
    bullish_gap = c3["low"] - c1["high"]
    bearish_gap = c1["low"] - c3["high"]

    if bullish_gap > 0 and _gap_is_valid(
        gap_size=bullish_gap,
        reference_price=c2["close"],
        atr_value=df.iloc[i].get("atr"),
        min_gap_pct=min_gap_pct,
        min_gap_atr=min_gap_atr,
        displacement_ok=(c2["close"] > c2["open"]) if require_displacement else True,
    ):
        return "bullish"

    if bearish_gap > 0 and _gap_is_valid(
        gap_size=bearish_gap,
        reference_price=c2["close"],
        atr_value=df.iloc[i].get("atr"),
        min_gap_pct=min_gap_pct,
        min_gap_atr=min_gap_atr,
        displacement_ok=(c2["close"] < c2["open"]) if require_displacement else True,
    ):
        return "bearish"

    return None


def is_fair_value_gap(
    df: pd.DataFrame,
    i: int,
    *,
    min_gap_pct: float = 0.0,
    min_gap_atr: float = 0.0,
    require_displacement: bool = True,
) -> bool:
    return (
        get_fair_value_gap_direction(
            df,
            i,
            min_gap_pct=min_gap_pct,
            min_gap_atr=min_gap_atr,
            require_displacement=require_displacement,
        )
        is not None
    )


def get_fair_value_gap_bounds(
    df: pd.DataFrame,
    i: int,
    direction: str,
) -> tuple[float, float]:
    c1, _, c3 = df.iloc[i - 2], df.iloc[i - 1], df.iloc[i]
    if direction == "bullish":
        return c1["high"], c3["low"]
    if direction == "bearish":
        return c3["high"], c1["low"]
    raise ValueError(f"Unsupported FVG direction: {direction}")


def calculate_position_size(
    entry_price: float,
    stop_price: float,
    config: BacktestConfig,
) -> int:
    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0 or config.value_per_point <= 0:
        return 0
    dollar_risk_per_unit = stop_distance * config.value_per_point
    return max(int(config.risk_per_trade / dollar_risk_per_unit), 0)


def check_exit(
    bar: pd.Series,
    direction: str,
    stop_price: float,
    target_price: float,
) -> tuple[float | None, str | None]:
    if direction == "long":
        hit_stop = bar["low"] <= stop_price
        hit_target = bar["high"] >= target_price
        if hit_stop and hit_target:
            return stop_price, "stop"
        if hit_stop:
            return stop_price, "stop"
        if hit_target:
            return target_price, "target"
        return None, None

    hit_stop = bar["high"] >= stop_price
    hit_target = bar["low"] <= target_price
    if hit_stop and hit_target:
        return stop_price, "stop"
    if hit_stop:
        return stop_price, "stop"
    if hit_target:
        return target_price, "target"
    return None, None


def settle_trade(
    trade: dict[str, Any],
    exit_price: float,
    exit_time: pd.Timestamp,
    reason: str,
    config: BacktestConfig,
) -> dict[str, Any]:
    direction = trade["direction"]
    entry_fill = apply_slippage(
        trade["entry_price"],
        direction=direction,
        side="entry",
        slippage_bps=config.slippage_bps,
    )
    exit_fill = apply_slippage(
        exit_price,
        direction=direction,
        side="exit",
        slippage_bps=config.slippage_bps,
    )
    gross_points = (exit_fill - entry_fill) if direction == "long" else (entry_fill - exit_fill)
    gross_pnl = gross_points * trade["quantity"] * config.value_per_point
    commissions = trade["quantity"] * config.commission_per_unit * 2
    net_pnl = gross_pnl - commissions

    return {
        **trade,
        "exit_time": exit_time,
        "exit_price": exit_price,
        "entry_fill": entry_fill,
        "exit_fill": exit_fill,
        "gross_pnl": gross_pnl,
        "commissions": commissions,
        "net_pnl": net_pnl,
        "reason": reason,
        "risk_multiple": (net_pnl / trade["planned_risk"]) if trade["planned_risk"] else 0.0,
    }


def summarize_trades(trades_df: pd.DataFrame) -> dict[str, float]:
    if trades_df.empty:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "gross_pnl": 0.0,
            "commissions": 0.0,
            "net_pnl": 0.0,
            "avg_r_multiple": 0.0,
        }

    wins = int((trades_df["net_pnl"] > 0).sum())
    losses = int((trades_df["net_pnl"] <= 0).sum())
    trades = len(trades_df)
    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / trades,
        "gross_pnl": float(trades_df["gross_pnl"].sum()),
        "commissions": float(trades_df["commissions"].sum()),
        "net_pnl": float(trades_df["net_pnl"].sum()),
        "avg_r_multiple": float(trades_df["risk_multiple"].mean()),
    }


def apply_slippage(
    price: float,
    *,
    direction: str,
    side: str,
    slippage_bps: float,
) -> float:
    if slippage_bps <= 0:
        return price

    move = price * (slippage_bps / 10_000)
    if direction == "long":
        return price + move if side == "entry" else price - move
    return price - move if side == "entry" else price + move


def _gap_is_valid(
    *,
    gap_size: float,
    reference_price: float,
    atr_value: float | None,
    min_gap_pct: float,
    min_gap_atr: float,
    displacement_ok: bool,
) -> bool:
    if not displacement_ok:
        return False

    if min_gap_pct > 0 and gap_size / max(abs(reference_price), 1e-9) < min_gap_pct:
        return False

    if min_gap_atr > 0:
        if atr_value is None or pd.isna(atr_value):
            return False
        if gap_size < atr_value * min_gap_atr:
            return False

    return True
