# Version 4.1.0: crypto coverage and entry diagnostics

The crypto page showed the current wait condition but did not explain the day's lack of orders. It also hid unselected markets and only supported BTC/ETH. This release adds a bounded five-market watchlist and durable per-market decision records.

## What the audit found

The September 19 native-bar replay contained five BTC reversal observations and four XRP observations, all shorts. The existing Alpaca spot route cannot open shorts. No BTC long setup was reproduced. ETH had 11 missing five-minute candles, SOL had two, and LINK had one in the measured sample. Incomplete histories fail the existing coverage rule; they are not filled with invented prices. Reconstructed candles do not establish what was executable at the historical decision time.

The [market research audit](research/crypto-market-audit-20260919.md) records the measured eight-market comparison, source times, spreads, size metadata and official references. It does not establish profitability.

## Product behavior

- Watch BTC/USD, ETH/USD, SOL/USD, LINK/USD and XRP/USD independently, including unselected trading markets. Show completed/missing candle counts, opening-range gaps, reconstructed buy/short setup totals and whether each market is selected for trading.
- Preserve all saved permissions, purchase targets and selected markets during the upgrade. Newly watched markets are not automatically selected. The owner can select supported markets through the existing settings review.
- Record actual worker checks separately from chart history. The daily panel distinguishes fresh long/short setups checked, broker entry submissions attempted, entries with confirmed fills and saved reasons for waiting. Logging starts with this release; earlier chart history is not backfilled as live decisions.
- Save only changed decisions or new completed candles, retain a bounded history, recover it across restarts, and show logging failures without interrupting position protection.
- Use each market's own entry message. Keep crypto panels inside the crypto/all views. The account total remains shared across both strategy families.

## Execution and data isolation

The expanded catalog uses exact pair/position aliases throughout data, order validation and portfolio ownership. Asset minimums and increments remain provider-derived. A failed extra-market constructor no longer disables Bitcoin or prevents later markets from starting.

The audit reproduced approximately 238 Trading API GETs per minute when five protected crypto positions and Socrates ran at their original cadences. Alpaca publishes a [200-request-per-minute account limit](https://alpaca.markets/support/usage-limit-api-calls). This release admits at most two active crypto lifecycles and bounds fresh-entry broker preflights to one per crypto tick, rotating fairly among selected markets. Unknown/unfinished entries consume capacity. Existing positions always retain their management loop, including any saved positions above the admission cap. Signal deadlines remain unchanged.

The two-position normal-management scenario measured approximately 148 broker reads per minute with an active Socrates position, including the shared account poll. These are measured normal workloads, not a guarantee about every recovery burst or other clients using the same account. The five independent watch workers add approximately 20 requests per minute to the separate market-data endpoint. No paid data service is added.

## Verification boundary

The release passed **1,705 Python tests**, **121 interface tests**, and the production UI build. Desktop and 390-pixel mobile preview checks verified all five rows, missing-candle explanations, distinct setup/order/fill counts, view separation and unchanged BTC-only selection. The isolated preview recorded zero broker submissions, mutation requests or worker starts.

Tests exercise actual analyzers, source identity, gaps, aliases, permission preservation, fair admission, daily decision retention, $5 buy/protection/exit lifecycles, partial fills, rejection and recovery against fake brokers. Browser checks use a provider-disabled service with all mutations rejected and no execution workers. Live checks read account eligibility, today's broker order history and current data only; no test order or new market activation is sent.

Market-data gaps remain an external limitation. Enabling a market cannot make missing candles valid, support shorts on this spot route, or establish positive after-cost returns.
