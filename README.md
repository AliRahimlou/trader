# trader

Local paper-trading platform for stocks and ETFs using Alpaca for both market data and execution. The trading runner is the source of truth. The web dashboard is the local control plane and observability layer.

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cd dashboard && npm install && cd ..
```

Fill in Alpaca paper credentials in `.env`.

Start the full app from the repo root with:

```bash
npm start
```

Important:

- Paper only. The runner refuses non-paper Alpaca execution.
- Massive is optional for historical experimentation only. It is not the active live path.
- Secrets stay in `.env` and are never returned by the API or UI.

## Architecture Note

Control flow:

1. `paper_engine.py` owns the live paper loop, scanner refresh cadence, watchlist consumption, risk checks, execution, reconciliation, and audit events.
2. `live_paper_runner.py` is the CLI entrypoint for direct runner operation.
3. `paper_supervisor.py` wraps the engine for start, stop, smoke test, flatten, and config commands with audit logging.
4. `operator_store.py` is the local SQLite store for snapshots, structured events, and operator command history.
5. `paper_api.py` exposes REST endpoints plus SSE for the dashboard and other local tooling.
6. `dashboard/` is a React control plane that consumes the backend only. It never decides trades itself.
7. `scanner_engine.py`, `universe_manager.py`, `ranking_engine.py`, and `watchlist_engine.py` provide the multi-symbol universe scan, opportunity scoring, and active watchlist persistence.

Runtime behavior:

1. On startup the engine validates the Alpaca paper account and reconciles persisted state against broker account, open orders, and positions.
2. The scanner builds a universe from Alpaca assets by default, scores candidates with explicit ranking weights, and maintains a watchlist with add/remove reasons.
3. Every cycle the engine checks the Alpaca clock, refreshes `1m`, `5m`, and `1d` context for the active watchlist plus open positions, and rejects stale bars.
4. Shared signal code in `strategy_signals.py` evaluates the existing FVG strategies on the shortlisted symbols.
5. `live_risk.py` enforces size, daily-loss, cooldown, daily-trade-count, concurrent-position, deployed-capital, per-symbol-capital, correlation, tradability, and buying-power limits.
6. `live_execution.py` handles paper order submission, bracket or in-process exits, lifecycle tracking, and flatten logic.
7. The engine persists JSON runner state, writes JSONL log events, and mirrors snapshots plus audit history into SQLite for the API/UI.

## Scanner And Watchlist Config

- `LIVE_PAPER_UNIVERSE_MODE`: `fixed` or `alpaca_assets`
- `LIVE_PAPER_UNIVERSE_SYMBOLS`: comma-separated base universe for fixed mode
- `LIVE_PAPER_UNIVERSE_MAX_SYMBOLS`: cap for broad scans in dynamic mode
- `LIVE_PAPER_UNIVERSE_REFRESH_SECONDS`: scanner refresh cadence
- `LIVE_PAPER_WATCHLIST_SIZE`: target active watchlist size
- `LIVE_PAPER_WATCHLIST_HOLD_BUFFER`: keep-near-threshold names warm to reduce churn
- `LIVE_PAPER_PINNED_SYMBOLS`: force symbols into the watchlist
- `LIVE_PAPER_UNIVERSE_ALLOW_STOCKS`, `LIVE_PAPER_UNIVERSE_ALLOW_ETFS`, `LIVE_PAPER_EXCLUDE_LEVERAGED_ETFS`: structural filters
- `LIVE_PAPER_SCANNER_MIN_PRICE`, `LIVE_PAPER_SCANNER_MAX_PRICE`, `LIVE_PAPER_SCANNER_MIN_AVG_DAILY_VOLUME`, `LIVE_PAPER_SCANNER_MAX_SPREAD_BPS`: scanner quality filters
- `LIVE_PAPER_MAX_CONCURRENT_POSITIONS`, `LIVE_PAPER_MAX_CAPITAL_DEPLOYED`, `LIVE_PAPER_MAX_CAPITAL_PER_SYMBOL`, `LIVE_PAPER_CORRELATION_THRESHOLD`: portfolio-level controls
- `LIVE_PAPER_SCANNER_WEIGHT_*`: explicit ranking weights for liquidity, volatility, momentum, gap, trend, setup, spread, and freshness

## Backend Commands

Run the backend API on `127.0.0.1:8000`:

```bash
./scripts/run_backend.sh
```

Run backend demo mode without Alpaca:

```bash
./scripts/run_backend.sh --demo
```

Run the CLI runner directly:

```bash
. .venv/bin/activate
python live_paper_runner.py --symbol SPY --strategy both --once --dry-run --verbose
python live_paper_runner.py --symbol SPY --strategy both --universe-mode fixed --universe-symbols SPY,QQQ,IWM,XLK --watchlist-size 3 --watchlist-hold-buffer 1 --once --dry-run --verbose
python live_paper_runner.py --symbol SPY --strategy both --universe-mode alpaca_assets --universe-max-symbols 120 --watchlist-size 10 --watchlist-hold-buffer 4 --dry-run --verbose
python live_paper_runner.py --symbol SPY --strategy both --smoke-test --paper-confirm
python live_paper_runner.py --symbol SPY --strategy both --paper-confirm
```

## Frontend Commands

Run the dashboard dev server on `127.0.0.1:5173`:

```bash
./scripts/run_frontend.sh
```

Build the dashboard bundle served by the backend:

```bash
cd dashboard
npm run build
```

Verify the local dashboard against the running backend:

```bash
cd dashboard
npm run verify
```

## Full Local Stack

Run backend plus frontend together:

```bash
npm start
./scripts/run_full_stack.sh
```

Run the full stack in demo mode:

```bash
npm run start:demo
./scripts/run_demo_stack.sh
```

## API Surface

REST routes:

- `GET /api/status`
- `GET /api/heartbeat`
- `GET /api/account`
- `GET /api/positions`
- `GET /api/orders`
- `GET /api/signals`
- `GET /api/scanner/status`
- `GET /api/scanner/ranked`
- `GET /api/strategy-status`
- `GET /api/watchlist`
- `GET /api/config`
- `GET /api/health`
- `GET /api/diagnostics`
- `GET /api/overview`
- `GET /api/events`
- `GET /api/commands`
- `POST /api/controls/{command_type}`

Realtime:

- `GET /api/events/stream`

The SSE stream emits runner, signal, order, fill, warning, command-audit, and heartbeat events.

## Dashboard Views

- Home: portfolio value, cash, buying power, current positions, quick actions, and recent activity.
- Scanner: ranked candidates, live watchlist status, score breakdowns, exclusions, pin/disable controls, and direct links into the trade ticket.
- Trade: chart, scanner-driven symbol picker, buy/sell preview, and clear paper-trading actions.
- Positions: entry, current price, unrealized PnL, time in trade, stop/target, and exit action.
- Activity: fills, rejections, recent bot decisions, and an advanced history section.
- Bot: simple automation status, pause/resume/start/stop controls, and signal decisions.
- Settings: plain-English risk settings with advanced technical controls hidden behind expandable details.

Manual trade note:

- Manual trades are blocked while automation is running or dry-run mode is enabled.
- If you open a manual paper position on a symbol the bot is tracking, close it before restarting the bot so reconciliation stays clean.

## Safety Rules

- Dangerous controls require confirmation and are audit logged.
- Duplicate UI submissions are blocked while a command is in flight.
- `--paper-confirm` is still required for direct CLI execution that can submit paper orders.
- Backend `start_runner` waits for broker auth and startup reconciliation to complete before reporting success.
- The dashboard shows runner startup state separately from steady-state running/stopped status.
- Runtime overrides can be reset from the Controls page while the runner is stopped.
- Stale market data, unmanaged broker state, reconciliation errors, or execution uncertainty halt trading instead of continuing.

## Validation Commands

Sanity-check account and data:

```bash
. .venv/bin/activate
python alpaca_trade.py account
python run_backtests.py --source alpaca --symbol SPY --strategy both --check-account --max-rows 1
```

Backend and API validation:

```bash
. .venv/bin/activate
python backend_server.py --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/api/status
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/scanner/status
curl http://127.0.0.1:8000/api/watchlist
curl http://127.0.0.1:8000/api/events?limit=20
```

Scanner command validation:

```bash
curl -X POST http://127.0.0.1:8000/api/controls/refresh_scanner \
  -H "Content-Type: application/json" \
  -d '{"actor":"local-operator","confirm":false,"payload":{}}'

curl -X POST http://127.0.0.1:8000/api/controls/pin_symbol \
  -H "Content-Type: application/json" \
  -d '{"actor":"local-operator","confirm":false,"payload":{"symbol":"XLK","pinned":true}}'

curl -X POST http://127.0.0.1:8000/api/controls/set_symbol_enabled \
  -H "Content-Type: application/json" \
  -d '{"actor":"local-operator","confirm":false,"payload":{"symbol":"QQQ","enabled":false}}'
```

Runner validation:

```bash
. .venv/bin/activate
python live_paper_runner.py --symbol SPY --strategy both --once --dry-run --verbose
python live_paper_runner.py --symbol SPY --strategy both --smoke-test --paper-confirm --entry-timeout-seconds 45
```

Working `curl` example:

```bash
curl -X GET \
  -H "APCA-API-KEY-ID: $APCA_API_KEY_ID" \
  -H "APCA-API-SECRET-KEY: $APCA_API_SECRET_KEY" \
  https://paper-api.alpaca.markets/v2/account
```
