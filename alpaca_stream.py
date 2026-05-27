from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from alpaca_api import AlpacaConfig

STREAMABLE_STOCK_FEEDS = {"iex", "sip", "delayed_sip"}


@dataclass
class AlpacaMarketDataStream:
    config: AlpacaConfig
    reconnect_seconds: float = 5.0
    connected: bool = False
    authenticated: bool = False
    last_message_at: str | None = None
    last_error: str | None = None
    subscribed_symbols: set[str] = field(default_factory=set)
    latest: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.feed = self.config.feed.lower()
        self.url = f"wss://stream.data.alpaca.markets/v2/{self.feed}"
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._message_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._ws: Any = None

    @property
    def supported(self) -> bool:
        return self.feed in STREAMABLE_STOCK_FEEDS

    def start(self, symbols: list[str] | set[str] | tuple[str, ...]) -> None:
        if not self.supported:
            with self._lock:
                self.last_error = f"Market stream is not supported for feed={self.feed}."
            return
        self.update_symbols(symbols)
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="alpaca-market-stream")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def update_symbols(self, symbols: list[str] | set[str] | tuple[str, ...]) -> None:
        normalized = {str(symbol).upper() for symbol in symbols if str(symbol).strip()}
        with self._lock:
            changed = normalized != self.subscribed_symbols
            self.subscribed_symbols = normalized
        if changed:
            self._send_subscription()

    def wait_for_message(self, timeout: float) -> bool:
        seen = self._message_event.wait(timeout)
        if seen:
            self._message_event.clear()
        return seen

    def latest_for_symbol(self, symbol: str) -> dict[str, dict[str, Any]]:
        with self._lock:
            return dict(self.latest.get(symbol.upper(), {}))

    def status(self) -> dict[str, Any]:
        with self._lock:
            latest_timestamps = {
                symbol: {
                    name: payload.get("t")
                    for name, payload in sorted(stream_payload.items())
                    if isinstance(payload, dict)
                }
                for symbol, stream_payload in sorted(self.latest.items())
            }
            return {
                "enabled": True,
                "supported": self.supported,
                "connected": self.connected,
                "authenticated": self.authenticated,
                "feed": self.feed,
                "url": self.url,
                "subscribed_symbols": sorted(self.subscribed_symbols),
                "last_message_at": self.last_message_at,
                "last_error": self.last_error,
                "latest_timestamps": latest_timestamps,
            }

    def _run(self) -> None:
        try:
            import websocket
        except Exception as exc:
            with self._lock:
                self.connected = False
                self.authenticated = False
                self.last_error = f"websocket-client is not installed: {exc}"
            return

        while not self._stop_event.is_set():
            self._ws = websocket.WebSocketApp(
                self.url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            try:
                self._ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                self._on_error(self._ws, exc)
            finally:
                with self._lock:
                    self.connected = False
                    self.authenticated = False
                self._ws = None
            if not self._stop_event.wait(self.reconnect_seconds):
                continue

    def _on_open(self, ws: Any) -> None:
        with self._lock:
            self.connected = True
            self.authenticated = False
            self.last_error = None
        ws.send(json.dumps({
            "action": "auth",
            "key": self.config.api_key_id,
            "secret": self.config.api_secret_key,
        }))

    def _on_message(self, ws: Any, raw_message: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self.last_message_at = now
        self._message_event.set()
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError:
            with self._lock:
                self.last_error = "Received a non-JSON market stream message."
            return
        messages = payload if isinstance(payload, list) else [payload]
        for message in messages:
            if not isinstance(message, dict):
                continue
            message_type = str(message.get("T") or "")
            if message_type == "success" and message.get("msg") == "authenticated":
                with self._lock:
                    self.authenticated = True
                    self.last_error = None
                self._send_subscription()
                continue
            if message_type == "error":
                with self._lock:
                    self.last_error = str(message.get("msg") or message)
                continue
            if message_type == "subscription":
                continue

            symbol = str(message.get("S") or "").upper()
            if not symbol:
                continue
            bucket = _stream_bucket(message_type)
            if not bucket:
                continue
            with self._lock:
                self.latest.setdefault(symbol, {})[bucket] = dict(message)

    def _on_error(self, ws: Any, error: Any) -> None:
        with self._lock:
            self.last_error = str(error)

    def _on_close(self, ws: Any, close_status_code: Any, close_msg: Any) -> None:
        with self._lock:
            self.connected = False
            self.authenticated = False
            if close_msg:
                self.last_error = str(close_msg)

    def _send_subscription(self) -> None:
        ws = self._ws
        with self._lock:
            authenticated = self.authenticated
            symbols = sorted(self.subscribed_symbols)
        if ws is None or not authenticated or not symbols:
            return
        try:
            ws.send(json.dumps({
                "action": "subscribe",
                "trades": symbols,
                "quotes": symbols,
                "bars": symbols,
            }))
        except Exception as exc:
            with self._lock:
                self.last_error = f"Failed to update market stream subscription: {exc}"


def _stream_bucket(message_type: str) -> str | None:
    return {
        "t": "trade",
        "q": "quote",
        "b": "bar",
        "u": "updated_bar",
    }.get(message_type)
