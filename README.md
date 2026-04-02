# trader

Paper-trading stack for stocks and ETFs using Alpaca for both market data and execution.

Core modules:

- `run_backtests.py`: historical runner for the FVG strategies.
- `strategy_signals.py`: shared signal-generation logic used by both backtests and the live runner.
- `live_paper_runner.py`: restart-safe paper-only live loop.
- `live_execution.py`: order submission, broker reconciliation, bracket handling, and flatten logic.
- `live_risk.py`: trade limits, buying-power checks, cooldowns, and duplicate-position guards.
- `live_state.py`: local persisted runner state.
- `live_logging.py`: machine-readable JSONL event logs plus concise console output.
- `alpaca_trade.py`: low-level paper-trading CLI for manual inspection and smoke tests.

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in Alpaca paper credentials in `.env`.

Important:

- `live_paper_runner.py` is paper-only. It refuses to run against a non-paper Alpaca endpoint.
- Real order submission requires `--paper-confirm`. This flag is intentionally CLI-only.
- Massive is optional for historical experimentation. It is not the active live market-data path for the runner.

## End To End Paper Mode

Sanity-check the account and data feed:

```bash
python alpaca_trade.py account
python run_backtests.py --source alpaca --symbol SPY --strategy both --check-account
```

Run one dry cycle without submitting orders:

```bash
python live_paper_runner.py --symbol SPY --strategy both --once --dry-run --verbose
```

Run a real paper smoke test through the live runner:

```bash
python live_paper_runner.py --symbol SPY --strategy both --smoke-test --paper-confirm
```

Start the continuous live paper loop:

```bash
python live_paper_runner.py --symbol SPY --strategy both --paper-confirm
```

Useful variants:

```bash
python live_paper_runner.py --symbol SPY --strategy break --once --dry-run --reset-state --verbose
python live_paper_runner.py --symbol SPY --strategy break --paper-confirm --exit-mode bracket
python live_paper_runner.py --symbol SPY --strategy break --paper-confirm --exit-mode in_process
python live_paper_runner.py --symbol SPY --strategy break --once --paper-confirm --exit-mode in_process --flatten-at 00:00
```

State and logs land in:

- `runtime/state/*.json`
- `runtime/logs/*.jsonl`

## Architecture Note

Control flow:

1. `live_paper_runner.py` loads config, checks the Alpaca paper account, and loads persisted local state.
2. On startup it reconciles local state against Alpaca positions and orders before it does anything else.
3. Each cycle fetches the Alpaca clock plus fresh `1m`, `5m`, and `1d` bars.
4. Bar freshness is validated in ET. If bars are stale, the runner halts instead of continuing to trade.
5. Shared logic in `strategy_signals.py` builds the latest FVG candidate setups.
6. `live_risk.py` applies risk filters: max size, max daily loss, max trades per day, one-position-per-symbol, cooldown, shortability, and buying power.
7. `live_execution.py` submits the entry order only after all filters pass, then tracks broker state until the trade is open, flattened, or rejected.
8. Every cycle persists local state and appends structured events to the JSONL log.

Execution modes:

- `bracket`: safer default. Alpaca holds stop-loss and take-profit orders at the broker.
- `in_process`: runner-managed exits. If the runner hits stale data or an unexpected exception while a position is open, it attempts a fail-safe flatten before stopping.

## Operational Notes

- Timezone handling is `America/New_York`.
- Tradable universe is stocks and ETFs only.
- Duplicate entries are blocked with both `last_processed_bar` and per-signal persistence.
- Unmanaged broker state is treated as an error. If the runner sees open orders or positions that are not represented in local state, it stops.
- `strategy_signals.py` is the single signal source for backtests and live trading. There is no separate live-only strategy implementation.
- `backtest_utils.py:is_fair_value_gap()` is intentionally simple and can be tightened with `LIVE_PAPER_MIN_GAP_PCT`, `LIVE_PAPER_MIN_GAP_ATR`, and `LIVE_PAPER_REQUIRE_DISPLACEMENT`.

Working `curl` example:

```bash
curl -X GET \
  -H "APCA-API-KEY-ID: $APCA_API_KEY_ID" \
  -H "APCA-API-SECRET-KEY: $APCA_API_SECRET_KEY" \
  https://paper-api.alpaca.markets/v2/account
```
