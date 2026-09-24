# Pivot · video strategy app

Socrates execution follows the **17:25 and 18:11 recordings**, with the 12:45 recording supplementary. A separate **4H Range Reversal** live-capable Alpaca crypto engine follows the September 19 recording. The view menu changes display only; persistent strategy controls select which engines may trade under the global Live switch.

**Current state (version 4.5.0): the app connects to Alpaca and InsightSentry Free actual VIX, with protected AllSpark hosting and remote GitHub updates, and has never sent an order.** The September 22 frame-by-frame re-read of the 18:11 recording (NQ futures 4h chart with extended hours, a handful of hand-drawn lines at swing points touched more than once over five to six weeks, kept on the 1h, break-then-retest including a fade at a just-reclaimed line, leaders read against period levels, a VIX with no lines where “look left” means a prior consolidation base) showed that the app's mechanical levels, continuation-only retest, 180-minute event life, 5-of-7 quorum and skipped shorts did not match what he does. Version 4.5.0 aligns them behind a new Socrates policy version (`nasdaq-qqq-execution-v7-video-aligned`): an afternoon four-hour bucket, swing-pivot areas with ±0.1% bands, two non-adjacent interactions and a cap of the 16 nearest, a retest that is any return to the level with the direction taken from the leaders and VIX, an event life to the end of the next session, leader period levels (previous day/week/Monday extremes, session/week/month opens, session VWAP) with a 4-of-7 majority and a 60-minute reaction window, VIX consolidation bases beside swing clusters, a PSQ inverse-ETF proxy for shorts (intraday only, stop and target translated by percentage), and a “Look left” chart panel backed by `/api/chart`. The 4.4.0 rules it builds on (minimum reward-to-risk with the event level excluded, VIX persistence, per-strategy allowances, strategy-scoped pause, 30-minute cutoff, crypto net-cost gate) are unchanged. Still not aligned: Alpaca has no futures, regular session only, no supply/demand boxes, no 5-minute VIX, no weekly/monthly VWAP. **After rollout both strategies show “review required”; the owner must accept the new policies in the app before new entries resume.** Purchase amounts and markets are unchanged. The VIX integration keeps completed candles in a durable cache and checks a fresh actual-index quote before an otherwise valid entry; its free allowance is guarded across restarts. Order handling has passed offline simulated-broker tests and 60-day replays; paper-account/live fills and profitability have not been established.

**Version 4.6.0:** Socrates rules decided by a year of replay and an expert panel. Profit is taken at today's open or the next key level; new entries only 10:00-12:00 ET; longs only; the entry is skipped when the stop is more than 1.5% away. The owner re-accepts the rules in the app. See the [4.6.0 release note](docs/production-v4.6.0.md).

**Version 4.5.2:** a live trade card at the top of the page shows the open Socrates position every few seconds. It shows profit or loss in $, % and R, the stop and target with the distance to each, the protective stop's status at Alpaca, the session-close countdown and a price line since the purchase. With no open trade, it shows the last trade's result and why it sold. No rule changes. See the [4.5.2 release note](docs/production-v4.5.2.md).

**Version 4.5.1:** crypto is paused (code kept; `PIVOT_CRYPTO_PAUSED=0` on the host re-enables it) and the app focuses on Socrates, with a plain-language readiness checklist, an [order-path audit](docs/order-path-audit-4.5.1.md) and a cleaner interface. See the [4.5.1 release note](docs/production-v4.5.1.md).

See the [4.5.0 release note](docs/production-v4.5.0.md), the [4.4.0 release note](docs/production-v4.4.0.md) and [execution behavior and remaining limits](docs/live-execution.md).

Each engine has its own durable maximum of **two new entry attempts per New York calendar session** (shared across both engines before 4.4.0). Rejected, uncertain and already-claimed attempts consume the allowance; completing a trade or restarting does not refund it. Protection and exits remain available after the allowance is exhausted. The dashboard reports each engine's allowance separately from fills. See [version 4.3.0 repair notes](docs/production-v4.3.0.md) and the [4.3.2 execution-safety fixes](docs/production-v4.3.2.md) (lost broker requests resolve after 60 seconds instead of stranding a position; skipped shorts, submission budget, immediate-limit buffer and container logs).

## Hosted app

Open [Pivot on AllSpark](https://media.mytap.net/pivot/) using the existing login. AllSpark runs independently of the laptop. The [hosting guide](docs/allspark-hosting.md) explains remote updates: commit to GitHub `main`, and the installed updater builds and tests the release, briefly holds new entries, and verifies that there are no open positions, orders or active trade before switching versions. Routine updates preserve the saved Live money setting; a changed execution policy requires owner review in the app. Update status appears in the app footer. Credentials and runtime databases stay on the server.

See the [deployment verification](docs/allspark-deployment-audit.md) for the installed runtime, migration and test evidence.

## Socrates flow

Premark Nasdaq areas (previous-day extremes and four-hour swing-pivot levels touched at least twice, two buckets per session) → observe an hourly break and a later return to the level, or a sweep; an event lives to the end of the next session → take the direction from the technology leaders at their interaction zones and period levels (four of seven agree, at most one opposing, within 60 minutes; a conflicting leader is neutral; mixed means no trade) → confirm the actual VIX reacting the opposite way at a swing cluster or consolidation base, with persistence → evaluate the documented trade plan (target excludes the event's own level and must meet the minimum reward-to-risk) and current broker checks. A short buys PSQ, the inverse ETF, intraday only. No new entries in the final 30 minutes; positions close five minutes before the close. The “Look left” panel shows the candles, areas and events the worker used.

These are related source variants in one workflow. The app does not add opening-range/FVG, ranking scores, daily-bias heuristics, volatility ETF proxies, 5-minute tape conditions, or a VWAP entry requirement (the session VWAP is one of the leader period levels, not a gate).

## 4H Range Reversal flow

Build the completed first four-hour crypto range (gap-tolerant since 4.4.0, with a minimum bar count) → observe a five-minute close outside → wait for a later close back inside → validate the signal, current bid/ask, shared cash and the net expectancy after 0.25%-per-side fees (the skip reason shows the numbers) → execute an Alpaca spot long with its own stop and target. Bitcoin is the default; Ethereum is optional. Upper-range short setups cannot execute on this spot account. The global Live switch and each strategy's saved permission control entries; switching the view never changes either permission.

## References

- [Source rulebook and audit](docs/video-strategy-audit.md)
- [Timestamped transcripts](docs/video-transcripts/README.md)
- [Implementation and verification audit](docs/rebuild-audit.md)
- [Data collection audit and free-source findings](docs/data-feed-audit.md)
- [Latest completion audit and outstanding dependencies](docs/completion-audit.md)
- [Creator channel review and exact-rule limits](docs/research/socrates-channel-review-20260918.md)
- [Version 3.1 release notes and latest visual findings](docs/production-v3.1.md)
- [3.1.0 leader/context implementation and validation](docs/research/leader-context-implementation-20260918.md)
- [Version 3.2 fixes, monitoring and remaining validation](docs/production-v3.2.md)
- [Version 3.2.1 native data collection for strategy validation](docs/production-v3.2.1.md)
- [Version 3.2.2 live execution audit and recovery fixes](docs/production-v3.2.2.md)
- [Version 3.3.0 strategy views and Bitcoin observations](docs/production-v3.3.0.md)
- [Version 4.0.0 live strategy controls, shared capital and crypto execution](docs/production-v4.0.0.md)
- [Version 4.3.0 shared entry allowance and execution verification](docs/production-v4.3.0.md)
- [Version 4.3.2 lost broker requests, skipped shorts and submission budget](docs/production-v4.3.2.md)
- [Version 4.4.0 minimum reward-to-risk, VIX-scaled gate, neutral leaders, per-engine allowances](docs/production-v4.4.0.md)
- [Version 4.6.0 evidence-decided Socrates rules](docs/production-v4.6.0.md)
- [Version 4.5.2 live trade view](docs/production-v4.5.2.md)
- [Version 4.5.0 video-aligned levels, retests, leaders and VIX, PSQ short proxy, “Look left” chart](docs/production-v4.5.0.md)
- [Detailed 4H Range Reversal source review and precise observation rules](docs/research/range-reversal-video-20260919.md)
- [Precisely sourced current methods and research alternatives](docs/research/current-method-specifications.md)
- [Isolated broker-paper commissioning](docs/research/paper-commissioning.md)

## Run locally

Use `.venv/bin/python -m pip install -r requirements.txt` and `npm --prefix dashboard install` if dependencies are not installed. Copy `.env.example` to a private `.env` and supply existing provider credentials.

`npm start` runs the development API on 127.0.0.1:8011 and the page on 127.0.0.1:5173. The laptop worker is disabled during the AllSpark migration. Do not enable two workers for the same account. Automated tests use fake brokers and block real requests.

`npm test` runs network-denied tests. `npm run build` verifies the frontend production build.

## Layout

- `pivot/strategy.py`: primary Nasdaq setup analysis; no order submission.
- `pivot/chart_data.py`: read-only `/api/chart` payload (completed 4h/1h candles, areas with interaction counts, previous-day levels, live events, leader period levels, VIX bases) for the “Look left” panel.
- `pivot/rulebook.py`: rules with source timestamps and unresolved definitions.
- `pivot/feeds.py`: read-only Alpaca and direct-index adapters.
- `pivot/index_data.py`, `pivot/data_health.py`: actual VIX provenance and per-input freshness checks.
- `pivot/insight_cache.py`, `pivot/insight_data.py`: InsightSentry collection, durable free-request allowance, exchange-calendar coverage and on-demand entry quotes.
- `pivot/service.py`: independent account/data/execution polling, snapshots and settings validation.
- `pivot/range_reversal.py`, `pivot/range_watch.py`: independent BTC/ETH native-five-minute signals and evidence history.
- `pivot/crypto_execution.py`, `pivot/crypto_broker.py`, `pivot/crypto_store.py`: durable Alpaca spot crypto entry, protection, exit, permission and incident handling.
- `pivot/portfolio.py`: shared account admission, retained allocations and verified position/order ownership across engines.
- `pivot/execution.py`, `pivot/broker.py`: durable order lifecycle and a narrow Alpaca adapter.
- `pivot/policy.py`: the versioned rules reviewed before live permission is saved.
- `pivot/store.py`: separate SQLite settings, permission, order-intent and audit storage.
- `pivot/observations.py`, `pivot/worker_health.py`: private reproducible input history and independent worker-progress checks.
- `research/`: disconnected strategy comparisons, exact observation replay and an explicitly isolated paper-broker workflow.
- `pivot/performance.py`: evidence-based completed-trade gross results; fees remain separate.
- `pivot/web/`: small standalone interface, no old dashboard imports; `chart.mjs` renders the “Look left” candles and lines from `/api/chart`.
- `pivot/tests/`: isolated tests; all HTTP requests denied.
- `deploy/`: tested Docker image, private-network hosting, serialized GitHub updater and rollout tests.

Legacy source and all pre-existing edits were archived under `runtime/legacy-app-before-video-rebuild/`, excluded from Git and deployment. Existing account history remains intact in the original runtime databases. The active app never imports that archive. Legacy environment flags cannot turn on orders. No credentials are sent to the browser.
