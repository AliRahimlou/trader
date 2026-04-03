from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests

from backtest_utils import normalize_ohlcv

LOGGER = logging.getLogger(__name__)
PERIOD_RE = re.compile(r"^\s*(\d+)\s*([A-Za-z]+)\s*$")
TIMEFRAME_MAP = {
    "1m": "1Min",
    "5m": "5Min",
    "1d": "1Day",
}
TERMINAL_ORDER_STATUSES = {
    "filled",
    "canceled",
    "expired",
    "rejected",
    "done_for_day",
    "stopped",
    "suspended",
    "calculated",
}


@dataclass(frozen=True)
class AlpacaConfig:
    api_key_id: str
    api_secret_key: str
    trading_base_url: str = "https://paper-api.alpaca.markets"
    data_base_url: str = "https://data.alpaca.markets"
    feed: str = "iex"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key_id,
            "APCA-API-SECRET-KEY": self.api_secret_key,
        }

    @classmethod
    def from_env(cls, env_path: str = ".env") -> "AlpacaConfig":
        load_env_file(env_path)
        api_key_id = _get_env_value("APCA_API_KEY_ID", "APCA-API-KEY-ID")
        api_secret_key = _get_env_value("APCA_API_SECRET_KEY", "APCA-API-SECRET-KEY")
        trading_base_url = os.getenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets").strip()
        data_base_url = os.getenv("APCA_API_DATA_URL", "https://data.alpaca.markets").strip()
        feed = os.getenv("APCA_API_FEED", "").strip() or infer_default_feed(trading_base_url)

        missing = [
            name
            for name, value in (
                ("APCA_API_KEY_ID", api_key_id),
                ("APCA_API_SECRET_KEY", api_secret_key),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "Missing Alpaca credentials: {}. Put them in environment variables or .env.".format(
                    ", ".join(missing)
                )
            )

        return cls(
            api_key_id=api_key_id,
            api_secret_key=api_secret_key,
            trading_base_url=trading_base_url.rstrip("/"),
            data_base_url=data_base_url.rstrip("/"),
            feed=feed,
        )


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


def infer_default_feed(trading_base_url: str) -> str:
    if "paper-api" in trading_base_url:
        return "iex"
    return "sip"


def _get_env_value(*keys: str) -> str:
    for key in keys:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


def fetch_account(config: AlpacaConfig) -> dict[str, Any]:
    return _request_trading(config, "GET", "/v2/account", action="fetch Alpaca account")


def fetch_clock(config: AlpacaConfig) -> dict[str, Any]:
    return _request_trading(config, "GET", "/v2/clock", action="fetch market clock")


def get_asset(config: AlpacaConfig, symbol: str) -> dict[str, Any]:
    encoded_symbol = quote(symbol.upper(), safe="")
    return _request_trading(config, "GET", f"/v2/assets/{encoded_symbol}", action=f"fetch asset {symbol}")


def list_assets(
    config: AlpacaConfig,
    *,
    status: str = "active",
    asset_class: str = "us_equity",
) -> list[dict[str, Any]]:
    response = _request_trading(
        config,
        "GET",
        "/v2/assets",
        params={"status": status, "asset_class": asset_class},
        action=f"list {status} {asset_class} assets",
    )
    return list(response)


def list_positions(config: AlpacaConfig) -> list[dict[str, Any]]:
    response = _request_trading(config, "GET", "/v2/positions", action="list positions")
    return list(response)


def get_position(config: AlpacaConfig, symbol: str) -> dict[str, Any]:
    encoded_symbol = quote(symbol.upper(), safe="")
    return _request_trading(
        config,
        "GET",
        f"/v2/positions/{encoded_symbol}",
        action=f"get position {symbol}",
    )


def list_orders(
    config: AlpacaConfig,
    *,
    status: str = "open",
    limit: int = 50,
    nested: bool = True,
) -> list[dict[str, Any]]:
    response = _request_trading(
        config,
        "GET",
        "/v2/orders",
        params={"status": status, "limit": limit, "nested": str(nested).lower()},
        action=f"list {status} orders",
    )
    return list(response)


def get_order(config: AlpacaConfig, order_id: str, *, nested: bool = True) -> dict[str, Any]:
    encoded_order_id = quote(order_id, safe="")
    return _request_trading(
        config,
        "GET",
        f"/v2/orders/{encoded_order_id}",
        params={"nested": str(nested).lower()},
        action=f"get order {order_id}",
    )


def cancel_order(config: AlpacaConfig, order_id: str) -> None:
    encoded_order_id = quote(order_id, safe="")
    _request_trading(
        config,
        "DELETE",
        f"/v2/orders/{encoded_order_id}",
        action=f"cancel order {order_id}",
        expected_statuses=(204,),
    )


def submit_order(
    config: AlpacaConfig,
    *,
    symbol: str,
    side: str,
    order_type: str = "market",
    time_in_force: str = "day",
    qty: float | None = None,
    notional: float | None = None,
    limit_price: float | None = None,
    stop_price: float | None = None,
    extended_hours: bool = False,
    client_order_id: str | None = None,
    order_class: str = "simple",
    take_profit: dict[str, float] | None = None,
    stop_loss: dict[str, float] | None = None,
) -> dict[str, Any]:
    if (qty is None and notional is None) or (qty is not None and notional is not None):
        raise ValueError("Provide exactly one of qty or notional when submitting an order.")

    payload: dict[str, Any] = {
        "symbol": symbol.upper(),
        "side": side,
        "type": order_type,
        "time_in_force": time_in_force,
        "order_class": order_class,
    }
    if qty is not None:
        payload["qty"] = _format_number(qty)
    if notional is not None:
        payload["notional"] = _format_number(notional)
    if limit_price is not None:
        payload["limit_price"] = _format_number(limit_price)
    if stop_price is not None:
        payload["stop_price"] = _format_number(stop_price)
    if extended_hours:
        payload["extended_hours"] = True
    if client_order_id:
        payload["client_order_id"] = client_order_id
    if take_profit is not None:
        payload["take_profit"] = {
            "limit_price": _format_number(take_profit["limit_price"]),
        }
    if stop_loss is not None:
        payload["stop_loss"] = {
            key: _format_number(value) for key, value in stop_loss.items() if value is not None
        }

    return _request_trading(
        config,
        "POST",
        "/v2/orders",
        json_payload=payload,
        action=f"submit {side} order for {symbol}",
    )


def close_position(
    config: AlpacaConfig,
    symbol: str,
    *,
    qty: float | None = None,
    percentage: float | None = None,
) -> dict[str, Any]:
    encoded_symbol = quote(symbol.upper(), safe="")
    payload: dict[str, Any] = {}
    if qty is not None:
        payload["qty"] = _format_number(qty)
    if percentage is not None:
        payload["percentage"] = _format_number(percentage)

    return _request_trading(
        config,
        "DELETE",
        f"/v2/positions/{encoded_symbol}",
        json_payload=payload or None,
        action=f"close position {symbol}",
    )


def wait_for_order_terminal(
    config: AlpacaConfig,
    order_id: str,
    *,
    timeout_seconds: int = 30,
    poll_interval: float = 1.0,
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    latest_order = get_order(config, order_id)

    while latest_order.get("status") not in TERMINAL_ORDER_STATUSES and time.time() < deadline:
        time.sleep(poll_interval)
        latest_order = get_order(config, order_id)

    return latest_order


def fetch_market_data(
    symbol: str,
    *,
    minute_period: str = "7d",
    five_min_period: str = "60d",
    daily_period: str = "1y",
    config: AlpacaConfig,
) -> dict[str, pd.DataFrame]:
    end = pd.Timestamp.now(tz="UTC")
    minute_start = period_to_start(end, minute_period)
    five_min_start = period_to_start(end, five_min_period)
    daily_start = period_to_start(end, daily_period)

    LOGGER.info(
        "Downloading Alpaca market data for %s with feed=%s", symbol, config.feed
    )
    return {
        "1m": fetch_stock_bars(symbol, "1m", minute_start, end, config=config),
        "5m": fetch_stock_bars(symbol, "5m", five_min_start, end, config=config),
        "1d": fetch_stock_bars(symbol, "1d", daily_start, end, config=config),
    }


def fetch_latest_bar(config: AlpacaConfig, symbol: str) -> dict[str, Any]:
    response = requests.get(
        f"{config.data_base_url}/v2/stocks/bars/latest",
        headers=config.headers,
        params={"symbols": symbol.upper(), "feed": config.feed},
        timeout=30,
    )
    _raise_for_status(response, f"fetch latest bar for {symbol}")
    payload = response.json()
    return payload["bars"][symbol.upper()]


def fetch_latest_trade(config: AlpacaConfig, symbol: str) -> dict[str, Any]:
    response = requests.get(
        f"{config.data_base_url}/v2/stocks/trades/latest",
        headers=config.headers,
        params={"symbols": symbol.upper(), "feed": config.feed},
        timeout=30,
    )
    _raise_for_status(response, f"fetch latest trade for {symbol}")
    payload = response.json()
    return payload["trades"][symbol.upper()]


def fetch_latest_quote(config: AlpacaConfig, symbol: str) -> dict[str, Any]:
    response = requests.get(
        f"{config.data_base_url}/v2/stocks/quotes/latest",
        headers=config.headers,
        params={"symbols": symbol.upper(), "feed": config.feed},
        timeout=30,
    )
    _raise_for_status(response, f"fetch latest quote for {symbol}")
    payload = response.json()
    return payload["quotes"][symbol.upper()]


def fetch_stock_snapshots(
    symbols: list[str],
    *,
    config: AlpacaConfig,
    chunk_size: int = 50,
) -> dict[str, dict[str, Any]]:
    snapshots: dict[str, dict[str, Any]] = {}
    normalized_symbols = [symbol.upper() for symbol in symbols if symbol]
    for start_index in range(0, len(normalized_symbols), chunk_size):
        chunk = normalized_symbols[start_index : start_index + chunk_size]
        if not chunk:
            continue
        response = requests.get(
            f"{config.data_base_url}/v2/stocks/snapshots",
            headers=config.headers,
            params={"symbols": ",".join(chunk), "feed": config.feed},
            timeout=30,
        )
        _raise_for_status(response, f"fetch stock snapshots for {','.join(chunk)}")
        payload = response.json()
        snapshot_payload = payload.get("snapshots") if isinstance(payload, dict) else None
        if isinstance(snapshot_payload, dict):
            snapshots.update(snapshot_payload)
        elif isinstance(payload, dict):
            snapshots.update({symbol.upper(): value for symbol, value in payload.items() if isinstance(value, dict)})
    return snapshots


def fetch_multi_stock_bars(
    symbols: list[str],
    interval: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    config: AlpacaConfig,
    chunk_size: int = 25,
) -> dict[str, pd.DataFrame]:
    timeframe = TIMEFRAME_MAP[interval]
    normalized_symbols = [symbol.upper() for symbol in symbols if symbol]
    frames: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in normalized_symbols}
    url = f"{config.data_base_url}/v2/stocks/bars"

    for start_index in range(0, len(normalized_symbols), chunk_size):
        chunk = normalized_symbols[start_index : start_index + chunk_size]
        params: dict[str, Any] = {
            "symbols": ",".join(chunk),
            "timeframe": timeframe,
            "start": to_api_timestamp(start),
            "end": to_api_timestamp(end),
            "limit": 10_000,
            "sort": "asc",
            "adjustment": "raw",
            "feed": config.feed,
        }
        page_token: str | None = None
        while True:
            if page_token:
                params["page_token"] = page_token
            elif "page_token" in params:
                del params["page_token"]
            response = requests.get(url, headers=config.headers, params=params, timeout=30)
            _raise_for_status(response, f"fetch {interval} bars for {','.join(chunk)}")
            payload = response.json()
            for symbol, bars in payload.get("bars", {}).items():
                frames.setdefault(symbol.upper(), []).extend(list(bars))
            page_token = payload.get("next_page_token")
            if not page_token:
                break

    normalized_frames: dict[str, pd.DataFrame] = {}
    for symbol in normalized_symbols:
        bars = frames.get(symbol, [])
        if not bars:
            normalized_frames[symbol] = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
            continue
        frame = pd.DataFrame(
            {
                "timestamp": [bar["t"] for bar in bars],
                "open": [bar["o"] for bar in bars],
                "high": [bar["h"] for bar in bars],
                "low": [bar["l"] for bar in bars],
                "close": [bar["c"] for bar in bars],
                "volume": [bar["v"] for bar in bars],
            }
        ).set_index("timestamp")
        frame.index = pd.to_datetime(frame.index, utc=True)
        normalized_frames[symbol] = normalize_ohlcv(frame, interval=interval)
    return normalized_frames


def fetch_stock_bars(
    symbol: str,
    interval: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    config: AlpacaConfig,
) -> pd.DataFrame:
    timeframe = TIMEFRAME_MAP[interval]
    url = f"{config.data_base_url}/v2/stocks/{symbol}/bars"
    params: dict[str, Any] = {
        "timeframe": timeframe,
        "start": to_api_timestamp(start),
        "end": to_api_timestamp(end),
        "limit": 10_000,
        "sort": "asc",
        "adjustment": "raw",
        "feed": config.feed,
    }
    bars: list[dict[str, Any]] = []
    page_token: str | None = None

    while True:
        if page_token:
            params["page_token"] = page_token
        elif "page_token" in params:
            del params["page_token"]

        response = requests.get(url, headers=config.headers, params=params, timeout=30)
        _raise_for_status(response, f"fetch {symbol} {interval} Alpaca bars")
        payload = response.json()
        bars.extend(payload.get("bars", []))
        page_token = payload.get("next_page_token")
        if not page_token:
            break

    if not bars:
        raise ValueError(
            f"No Alpaca bars returned for {symbol} interval={interval}. "
            f"Check symbol support, date range, and data feed entitlement."
        )

    frame = pd.DataFrame(
        {
            "timestamp": [bar["t"] for bar in bars],
            "open": [bar["o"] for bar in bars],
            "high": [bar["h"] for bar in bars],
            "low": [bar["l"] for bar in bars],
            "close": [bar["c"] for bar in bars],
            "volume": [bar["v"] for bar in bars],
        }
    ).set_index("timestamp")
    frame.index = pd.to_datetime(frame.index, utc=True)
    return normalize_ohlcv(frame, interval=interval)


def format_account_summary(account: dict[str, Any]) -> str:
    return (
        "Account status={status} cash=${cash} buying_power=${buying_power} "
        "portfolio_value=${portfolio_value} trading_blocked={trading_blocked} "
        "account_blocked={account_blocked} pattern_day_trader={pattern_day_trader}"
    ).format(
        status=account.get("status"),
        cash=account.get("cash"),
        buying_power=account.get("buying_power"),
        portfolio_value=account.get("portfolio_value"),
        trading_blocked=account.get("trading_blocked"),
        account_blocked=account.get("account_blocked"),
        pattern_day_trader=account.get("pattern_day_trader"),
    )


def format_order_summary(order: dict[str, Any]) -> str:
    return (
        "Order id={id} symbol={symbol} side={side} type={type} status={status} "
        "qty={qty} notional={notional} filled_qty={filled_qty} "
        "filled_avg_price={filled_avg_price} tif={time_in_force}"
    ).format(
        id=order.get("id"),
        symbol=order.get("symbol"),
        side=order.get("side"),
        type=order.get("type"),
        status=order.get("status"),
        qty=order.get("qty"),
        notional=order.get("notional"),
        filled_qty=order.get("filled_qty"),
        filled_avg_price=order.get("filled_avg_price"),
        time_in_force=order.get("time_in_force"),
    )


def format_position_summary(position: dict[str, Any]) -> str:
    return (
        "Position symbol={symbol} side={side} qty={qty} market_value={market_value} "
        "avg_entry_price={avg_entry_price} unrealized_pl={unrealized_pl}"
    ).format(
        symbol=position.get("symbol"),
        side=position.get("side"),
        qty=position.get("qty"),
        market_value=position.get("market_value"),
        avg_entry_price=position.get("avg_entry_price"),
        unrealized_pl=position.get("unrealized_pl"),
    )


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


def to_api_timestamp(ts: pd.Timestamp) -> str:
    return ts.tz_convert("UTC").isoformat().replace("+00:00", "Z")


def _request_trading(
    config: AlpacaConfig,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_payload: dict[str, Any] | None = None,
    action: str,
    expected_statuses: tuple[int, ...] = (200,),
) -> Any:
    headers = {**config.headers}
    if json_payload is not None:
        headers["Content-Type"] = "application/json"

    response = requests.request(
        method,
        f"{config.trading_base_url}{path}",
        headers=headers,
        params=params,
        json=json_payload,
        timeout=30,
    )
    if response.status_code not in expected_statuses:
        _raise_for_status(response, action)
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def _format_number(value: float) -> str:
    return format(value, ".9f").rstrip("0").rstrip(".")


def _raise_for_status(response: requests.Response, action: str) -> None:
    if response.ok:
        return

    body = response.text.strip()
    if response.status_code == 403:
        raise RuntimeError(
            f"Failed to {action}: HTTP 403. Alpaca requires APCA-API-KEY-ID and "
            f"APCA-API-SECRET-KEY headers, and paper credentials only work against "
            f"paper-api.alpaca.markets. Response: {body}"
        )
    raise RuntimeError(f"Failed to {action}: HTTP {response.status_code}. Response: {body}")
