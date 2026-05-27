from alpaca_api import AlpacaConfig
from alpaca_stream import AlpacaMarketDataStream, _stream_bucket


def _config(feed="iex"):
    return AlpacaConfig(
        api_key_id="key",
        api_secret_key="secret",
        trading_base_url="https://paper-api.alpaca.markets",
        data_base_url="https://data.alpaca.markets",
        feed=feed,
    )


def test_stream_bucket_maps_stock_messages():
    assert _stream_bucket("t") == "trade"
    assert _stream_bucket("q") == "quote"
    assert _stream_bucket("b") == "bar"
    assert _stream_bucket("u") == "updated_bar"
    assert _stream_bucket("subscription") is None


def test_market_stream_marks_unsupported_feed_without_connecting():
    stream = AlpacaMarketDataStream(_config(feed="boats"))

    stream.start(["SPY"])

    status = stream.status()
    assert status["supported"] is False
    assert status["connected"] is False
    assert "not supported" in status["last_error"]


def test_market_stream_symbol_updates_are_normalized():
    stream = AlpacaMarketDataStream(_config())

    stream.update_symbols(["spy", "AAPL", "spy"])

    assert stream.status()["subscribed_symbols"] == ["AAPL", "SPY"]
