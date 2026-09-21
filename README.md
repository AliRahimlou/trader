# Pivot · video strategy app

Socrates execution follows the **17:25 and 18:11 recordings**, with the 12:45 recording supplementary. A separate **4H Range Reversal** live-capable Alpaca crypto engine follows the September 19 recording. The view menu changes display only; persistent strategy controls select which engines may trade under the global Live switch.

**Current state: the app connects to Alpaca and InsightSentry Free actual VIX, with protected AllSpark hosting and remote GitHub updates.** The VIX integration keeps completed candles in a durable cache and checks a fresh actual-index quote before an otherwise valid entry. Its free allowance is guarded across restarts. Live execution requires owner review through the top-right Live money switch. Order handling has passed offline simulated-broker tests; paper-account/live fills and profitability have not been established.

See [execution behavior and remaining limits](docs/live-execution.md).

Both engines share a durable maximum of **two new entry attempts per New York calendar session**. Rejected, uncertain and already-claimed attempts consume the allowance; completing a trade or restarting does not refund it. Protection and exits remain available after the allowance is exhausted. The dashboard reports the shared allowance separately from fills. See [version 4.3.0 repair notes](docs/production-v4.3.0.md).

## Hosted app

Open [Pivot on AllSpark](https://media.mytap.net/pivot/) using the existing login. AllSpark runs independently of the laptop. The [hosting guide](docs/allspark-hosting.md) explains remote updates: commit to GitHub `main`, and the installed updater builds and tests the release, briefly holds new entries, and verifies that there are no open positions, orders or active trade before switching versions. Routine updates preserve the saved Live money setting; a changed execution policy requires owner review in the app. Update status appears in the app footer. Credentials and runtime databases stay on the server.

See the [deployment verification](docs/allspark-deployment-audit.md) for the installed runtime, migration and test evidence.

## Socrates flow

Premark Nasdaq areas (previous-day extremes and repeated four-hour levels) → observe a break/retest or sweep → confirm the technology leaders at supply/demand → confirm the actual VIX at its own area → evaluate the documented trade plan and current broker checks.

These are related source variants in one workflow. The app does not add opening-range/FVG, ranking scores, daily-bias heuristics, volatility ETF proxies, 5-minute tape conditions, or VWAP requirements.

## 4H Range Reversal flow

Build the completed first four-hour crypto range → observe a five-minute close outside → wait for a later close back inside → validate the signal, current bid/ask, shared cash and trading costs → execute an Alpaca spot long with its own stop and target. Bitcoin is the default; Ethereum is optional. Upper-range short setups cannot execute on this spot account. The global Live switch and each strategy's saved permission control entries; switching the view never changes either permission.

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
- [Detailed 4H Range Reversal source review and precise observation rules](docs/research/range-reversal-video-20260919.md)
- [Precisely sourced current methods and research alternatives](docs/research/current-method-specifications.md)
- [Isolated broker-paper commissioning](docs/research/paper-commissioning.md)

## Run locally

Use `.venv/bin/python -m pip install -r requirements.txt` and `npm --prefix dashboard install` if dependencies are not installed. Copy `.env.example` to a private `.env` and supply existing provider credentials.

`npm start` runs the development API on 127.0.0.1:8011 and the page on 127.0.0.1:5173. The laptop worker is disabled during the AllSpark migration. Do not enable two workers for the same account. Automated tests use fake brokers and block real requests.

`npm test` runs network-denied tests. `npm run build` verifies the frontend production build.

## Layout

- `pivot/strategy.py`: primary Nasdaq setup analysis; no order submission.
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
- `pivot/web/`: small standalone interface, no old dashboard imports.
- `pivot/tests/`: isolated tests; all HTTP requests denied.
- `deploy/`: tested Docker image, private-network hosting, serialized GitHub updater and rollout tests.

Legacy source and all pre-existing edits were archived under `runtime/legacy-app-before-video-rebuild/`, excluded from Git and deployment. Existing account history remains intact in the original runtime databases. The active app never imports that archive. Legacy environment flags cannot turn on orders. No credentials are sent to the browser.
