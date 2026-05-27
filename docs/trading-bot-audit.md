# Trading Bot Audit

This app is a local paper-trading control plane. The runner is the source of truth. The dashboard sends commands and shows state, but the dashboard does not decide trades.

## What Happens When You Click Start Bot

1. The API receives `start_runner`.
2. `EngineSupervisor` starts a background `PaperTradingEngine` thread.
3. The engine loads persisted state from `runtime/state`.
4. In live paper mode it refuses non-paper Alpaca accounts.
5. It fetches the Alpaca account, open positions, and orders.
6. It reconciles any saved active trades against the broker.
7. It refreshes the scanner and builds the active watchlist.
8. It marks startup as ready.
9. It loops every `poll_seconds`, default 5 seconds.

If startup auth, reconciliation, or data freshness fails, startup fails or the runner halts. That is intentional.

## Is The Data Live

In live paper mode, market data comes from Alpaca:

- latest account, positions, orders, and market clock from Alpaca trading endpoints
- stock snapshots for broad scanner ranking
- `1m`, `5m`, and `1d` bars for watched symbols
- latest trade and quote when needed

The default feed is `iex` for paper accounts unless `LIVE_PAPER_ALPACA_FEED` or `APCA_API_FEED` changes it. `iex` is live exchange data from IEX, not full SIP consolidated market data. The runner rejects stale `1m` bars when the market is open using `LIVE_PAPER_MAX_BAR_AGE_SECONDS`, default 130 seconds.

In demo mode, data is simulated. Demo mode is for UI and workflow testing only.

## How It Picks Stocks

The bot has two layers:

1. Scanner chooses what to watch.
2. Strategy engine chooses whether to enter.

Scanner flow:

1. Build the universe from Alpaca active US equities or configured symbols.
2. Filter out untradable assets, disallowed stocks/ETFs, leveraged ETFs, bad prices, low volume, and wide spreads.
3. Score snapshots for liquidity, spread, range, gap, momentum, and freshness.
4. Pull daily bars for the best scan set.
5. Add context: trend, daily bias, ATR, relative volume, compression, level proximity, noise, and extension.
6. Shortlist the strongest names plus pinned symbols and open positions.
7. Pull `1m`, `5m`, and `1d` context for the shortlist.
8. Run the configured strategies.
9. Score any signals for setup quality, expectancy, execution quality, and calibration from closed paper trades.
10. Apply the ATLAS review: regime fit, relative strength, strategy edge, and CRO-style risk flags.
11. Build the active watchlist.

The bot does not know the most profitable stock in advance. It ranks candidates using live liquidity, volatility, trend, setup quality, strategy evidence, and risk controls. That is a selection model, not a profit guarantee.

## Current Strategies

The active strategy code looks for fair value gap setups:

- `break`: opening-range break plus FVG confirmation
- `pullback`: daily sweep plus pullback FVG confirmation

Signals produce:

- direction: long or short
- entry reference price
- stop
- target
- quantity
- reason and metadata

The runner chooses the best allowed signal across the active watchlist. If multiple symbols pass, it ranks by signal selection score, scanner score, watchlist rank, and risk result.

## How Much It Invests

There are two separate ideas:

- `risk_per_trade`: intended dollar loss if the stop is hit
- `max_position_notional`: maximum dollars allowed in one position

Initial strategy quantity is:

```text
quantity = floor(risk_per_trade / abs(entry - stop))
```

Then live risk caps it by:

- max shares
- max dollars per position
- max concurrent positions
- max total capital deployed
- max capital per symbol
- daily loss limit
- daily trade count
- cooldown
- one-position-per-symbol
- buying power
- correlation with open positions
- ATLAS CRO block

The new launchpad treats the user-entered amount as `max_position_notional`, meaning "do not put more than this many paper dollars into one bot trade." It also sets a smaller internal risk budget from that amount.

## What Happens After Entry

Default exit mode is `bracket`.

In bracket mode, the entry order includes broker-side target and stop orders. If the app stops after entry, those broker-side bracket orders should still exist at Alpaca paper, but the local app will not scan, reconcile, update UI state, or flatten at end-of-day until it runs again.

In `in_process` mode, the bot itself watches bars and submits exits. That mode requires the app to keep running while a position is open.

The bot also has an end-of-day flatten cutoff, default `15:55` ET.

## Do You Need To Keep It Running 24/7

No. It only trades regular market sessions. But if you want the bot to find entries, manage in-process exits, reconcile state, and flatten near the cutoff, the backend must be running during the trading window.

If your machine sleeps or the backend stops:

- no new scans happen
- no new entries happen
- the dashboard stops updating
- in-process exits cannot happen
- bracket exits already submitted to Alpaca remain broker-side
- end-of-day flatten will not happen locally

For real unattended operation, this should run on an always-on host, not a laptop that sleeps.

## Main Risks And Gaps

- Profitability is not proven by the UI. It must be measured through backtests, paper logs, and live paper outcomes.
- The scanner only deep-scans a capped set each refresh. Default broad scan cap is 120 symbols.
- `iex` feed is not full-market consolidated SIP data.
- Strategy sizing is risk-first, not exact dollar-notional investing.
- Current strategies are narrow FVG strategies. They will miss many trend-following and mean-reversion opportunities.
- Manual trading and bot trading are intentionally separated to avoid unmanaged positions.
- Running locally means uptime depends on the local machine.

## Current Safer Operating Model

Use the Home launchpad:

1. Choose `Bot picks` if you want the scanner to select from the market board.
2. Choose `Pick stock` if you want to focus one symbol.
3. Enter max dollars per bot trade.
4. Click Start bot.
5. Watch Scanner and Activity for reasons, signals, skips, orders, and fills.

The bot will not buy immediately unless a live setup triggers and all risk checks pass.
