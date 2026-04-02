# trader

Simple FVG backtest starter with:

- `fvgBreak.py`: opening-range break plus fair value gap.
- `fvgPullback.py`: daily sweep, fair value gap, and pullback entry.
- `run_backtests.py`: downloads 1-minute, 5-minute, and daily OHLCV for one symbol and runs the strategies.
- `alpaca_api.py`: Alpaca account check plus stock bar downloads for the same runner.
- `massive_api.py`: Massive market-data adapter for historical/intraday bars.
- `alpaca_trade.py`: guarded Alpaca paper-trading CLI for account checks, orders, positions, and a smoke test.
- `live_paper_runner.py`: paper-only live loop that polls Alpaca bars, runs one strategy, places entries, and manages exits.

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

If you want Alpaca:

```bash
cp .env.example .env
```

Fill in your paper credentials in `.env`.

## Run

```bash
python run_backtests.py --symbol SPY --strategy both
```

Run against Alpaca stock data instead of `yfinance`:

```bash
python run_backtests.py --source alpaca --symbol AAPL --strategy both --check-account
```

Run against Massive market data:

```bash
python run_backtests.py --source massive --symbol SPY --strategy both
```

Paper-trading CLI examples:

```bash
python alpaca_trade.py account
python alpaca_trade.py positions
python alpaca_trade.py orders --status all --limit 20
python alpaca_trade.py submit --symbol SPY --side buy --notional 10 --wait-seconds 30
python alpaca_trade.py close --symbol SPY --wait-seconds 30
python alpaca_trade.py smoke-test --notional 10
```

Run the live paper strategy loop:

```bash
python live_paper_runner.py --symbol SPY --strategy break
```

One-cycle dry run:

```bash
python live_paper_runner.py --symbol SPY --strategy break --once --dry-run --verbose
```

Example with stricter FVG detection, commissions, and slippage:

```bash
python run_backtests.py \
  --symbol ES=F \
  --value-per-point 50 \
  --commission-per-unit 2.25 \
  --slippage-bps 1 \
  --min-gap-atr 0.2 \
  --verbose
```

## Notes

- Intraday data is normalized to `America/New_York`.
- `is_fair_value_gap()` lives in `backtest_utils.py` and is intentionally simple to keep it easy to tune.
- Alpaca account checks hit `https://paper-api.alpaca.markets/v2/account`, while Alpaca stock bars come from `https://data.alpaca.markets`.
- Massive market data uses `https://api.massive.com` minute/day aggregate endpoints.
- Massive access depends on your plan. On the current local key, some aggregate requests work, but real-time endpoints and bursty request patterns can still return entitlement or rate-limit errors.
- If you call `/v2/account` without `APCA-API-KEY-ID` and `APCA-API-SECRET-KEY` headers, Alpaca will reject the request.
- Paper accounts should generally use the `iex` stock data feed unless you have broader market data entitlements.
- This repo accepts `MASSIVE_API_KEY` or `MASSIVE_API` in `.env`.
- `alpaca_trade.py` refuses to submit orders unless `APCA_API_BASE_URL` points at the paper endpoint.
- `live_paper_runner.py` is also paper-only and uses a local JSON state file to avoid duplicate entries across restarts.
- The live runner currently manages stops and targets in-process. If the runner is down, Alpaca is not holding broker-side protective orders for you.
- This is still a starter backtest. Real intraday research needs cleaner session handling, holiday calendars, data validation, and instrument-specific contract logic.

Working curl example:

```bash
curl -X GET \
  -H "APCA-API-KEY-ID: $APCA_API_KEY_ID" \
  -H "APCA-API-SECRET-KEY: $APCA_API_SECRET_KEY" \
  https://paper-api.alpaca.markets/v2/account
```
