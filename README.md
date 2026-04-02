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

Important:

- Paper only. The runner refuses non-paper Alpaca execution.
- Massive is optional for historical experimentation only. It is not the active live path.
- Secrets stay in `.env` and are never returned by the API or UI.

## Architecture Note

Control flow:

1. `paper_engine.py` owns the live paper loop, state transitions, risk checks, execution, reconciliation, and audit events.
2. `live_paper_runner.py` is the CLI entrypoint for direct runner operation.
3. `paper_supervisor.py` wraps the engine for start, stop, smoke test, flatten, and config commands with audit logging.
4. `operator_store.py` is the local SQLite store for snapshots, structured events, and operator command history.
5. `paper_api.py` exposes REST endpoints plus SSE for the dashboard and other local tooling.
6. `dashboard/` is a React control plane that consumes the backend only. It never decides trades itself.

Runtime behavior:

1. On startup the engine validates the Alpaca paper account and reconciles persisted state against broker account, open orders, and positions.
2. Every cycle it checks the Alpaca clock, refreshes `1m`, `5m`, and `1d` context, and rejects stale bars.
3. Shared signal code in `strategy_signals.py` evaluates the existing FVG strategies.
4. `live_risk.py` enforces size, daily-loss, cooldown, daily-trade-count, one-position-per-symbol, tradability, and buying-power limits.
5. `live_execution.py` handles paper order submission, bracket or in-process exits, lifecycle tracking, and flatten logic.
6. The engine persists JSON runner state, writes JSONL log events, and mirrors snapshots plus audit history into SQLite for the API/UI.

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
./scripts/run_full_stack.sh
```

Run the full stack in demo mode:

```bash
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
- `GET /api/strategy-status`
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

- Overview: runner status, account, PnL, positions, open orders, and freshness.
- Strategy: latest signal decisions, cooldowns, daily limits, and per-strategy status.
- Orders: broker orders plus operator command history.
- Positions: current positions and manual close control.
- Events: structured event log with local filtering and JSON export.
- Controls: start, stop, run-once, smoke test, pause/resume entries, flatten, cancel orders, symbol toggle, and strategy toggle.
- Settings: effective runtime config plus safe non-secret edits.
- Health: auth, connectivity, reconciliation, and raw diagnostics.

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
curl http://127.0.0.1:8000/api/events?limit=20
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
