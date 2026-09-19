# Version 4.0.0: independent live strategy engines

The previous release added a second analysis view but could not execute its trades. This release supplies a separate Alpaca crypto order engine, persistent strategy controls, and shared account coordination. Socrates remains its own QQQ engine. No discretionary trading rule or real order is invented by changing views.

## Controls and migration

| Action | Effect on new entries | Effect on existing positions |
| --- | --- | --- |
| Select Socrates, 4H Range Reversal or All in the view menu | Display only; saved permissions stay unchanged | Every strategy continues managing its own positions |
| Enable a strategy after reviewing its policy | Select that engine; it may enter when global Live is On and its checks pass | Other strategies keep running |
| Run both strategies | Review and enable both engines; each has its own purchase target | Both engines keep their own ownership and exit rules |
| Turn one strategy Off | Stop its new entries and cancel unfinished entry quantities | Keep its exits active; other enabled strategies continue |
| Turn global Live Off | Stop new entries across both engines and cancel unfinished entries | Keep protection and exits active across both |
| Change crypto amount/markets | Applies to future entries; invalidates pending unsent plans | Existing trades retain original stops, targets and ownership |

Existing Socrates permission and purchase amount migrate unchanged. The new crypto permission starts Off and requires its own policy review; an old QQQ approval cannot silently enable a new instrument. The app clearly shows global status and each family's saved permission. There is no observation-only crypto execution stub.

Socrates has one active QQQ position. Range Reversal can have one active position per selected market. With both BTC and ETH selected, $5 means up to $5 for each qualifying new purchase, rather than a combined $5 budget across all markets. The coordinator reserves shared account capital before each entry; other active allocations remain conservatively reserved until closed. This can leave additional cash unused when the broker already deducted that cash, but avoids overspending from delayed account snapshots.

## Crypto route and video interpretation

The live-capable route is **Alpaca spot BTC/USD**, with **ETH/USD optional**. A fresh read-only capability check confirmed this retail account's crypto eligibility and approximately $1 minimum sizes, allowing a $5 target. Both instruments are long-only. An upper-range short signal is reported as unsupported; it is never turned into a substitute buy or a sale of unowned coins. Bullpen/Hyperliquid is a different, short-capable route with a $10 opening minimum and separate authentication/position management; it is not silently substituted.

The full audio transcript and selected exact chart-frame review support this deterministic baseline:

1. Use the first fully completed four-hour daily range. The chosen app anchor is New York midnight plus 240 elapsed minutes, explicitly disclosed; chart display timezone did not prove the creator's original session anchor.
2. Require a native five-minute close outside, followed by the first later close strictly inside. A wick alone or equality with a boundary is insufficient.
3. Lower-range failed break gives a long. The first outside-close candle's low fixes the stop; the confirmation close plus twice that stop distance fixes the target.
4. The latest confirmation must still be within 90 seconds, with a current provider receipt and executable quote. A new receipt never extends that candle's deadline. New excursions have new identities; old or attempted events are not replayed.
5. Rebuild the range at the next New York day. Existing positions continue with frozen exits; the source does not prescribe midnight liquidation.

No VIX, Magnificent Seven, stock-market opening clock or stock end-of-day liquidation is added to crypto. Daily/weekly trend preference and discretionary closer stops remain separately described source suggestions without invented formulas. [Detailed video evidence and comparison](research/range-reversal-video-20260919.md).

Ethereum is an additional application of the same rules, not a demonstrated or verified creator result. No claim of a 72% win rate or profitable performance is made. The video's 40%-break-even statement at exact 2R/1R is mathematically incorrect before costs.

## Actual order lifecycle

- **Admission:** global and family permission must match the reviewed live account. Verify native signal identity, current quote, asset eligibility/minimum/increments, unreserved non-marginable cash, and ownership of all account exposure. Unknown positions/orders block additional entries. Shorting is never simulated by selling a spot holding.
- **Entry:** a capped immediate-or-cancel limit buy. Planned notional is within 1% below the saved amount, never above it; partial fills can be smaller. The price must remain within the signal stop/target and at most 0.1% above the confirmation reference.
- **Costs:** published tier-one taker fees of 0.25% per side, plus an execution allowance, must leave a positive projected target after costs. A visually valid but economically too-small target is skipped with a visible reason. Fees and market conditions can change realized results.
- **Ownership:** confirmed fills establish gross quantity; the broker position establishes bounded net quantity after buy fees. Saved net ownership cannot grow without additional confirmed fill evidence. Fees below the published bound and very small external changes can be indistinguishable initially; the engine does not claim an independent fee ledger. Larger unexplained holdings/reservation changes stop competing orders and raise an incident.
- **Protection:** a native GTC sell stop-limit is placed for the owned quantity. Its limit is 1% below the stop. This can remain unfilled after a gap; it is not a guaranteed stop price. The running worker checks targets and stop fallback approximately every ten seconds, subject to broker latency and limits.
- **Exit:** first obtain definitive cancellation or fill of existing sells, reconcile all cumulative fills and available quantity, then send the necessary market sale. An unknown cancellation never authorizes a competing sell. Late fees and recovered prepared sells trigger quantity revalidation before first submission.
- **Durability:** persist unique intents before broker POSTs; claim each mutation once; look up uncertain responses by client identifier. Restart does not blindly resubmit, replay old signals or adopt manually held positions.
- **Incidents:** unresolved entry cancellation, stop acceptance and exits have durable attention deadlines. Broker read failures do not reset those deadlines. Ownership uncertainty pauses new crypto entries while management continues to reconcile known exposure. Unresolved incidents remain visible.
- **Recovery:** “Recheck crypto incidents” uses broker reads only. It requires the same live account, no unfinished crypto lifecycle, a flat account, and matching terminal records for every submitted operation. Missing uncertain orders never count as rejection proof. Recovery preserves permission and the audit trail; recurring incidents reopen. Recent crypto outcomes remain visible after a trade finishes.

Alpaca limits each crypto order to $200,000. The purchase control enforces this provider limit. Appreciated positions can exit through independently reconciled sales planned below $190,000 each using fresh quotes; this buffer cannot guarantee acceptance through every price jump. Failed/partial attempts are bounded and unresolved exposure remains recorded. Ordinary small emergency exits do not acquire a new dependency on available quotes.

Alpaca supports market, limit and stop-limit crypto orders, without native crypto bracket/OCO or market-stop orders. See [Alpaca crypto trading](https://docs.alpaca.markets/us/docs/crypto-trading) and its [order reference](https://docs.alpaca.markets/us/v1.1/reference/postorder). Real fill effects are not established by offline tests.

## Shared execution and deployment

The existing Socrates engine is retained. Its new optional coordinator permits other exposure only when a durable strategy ledger proves matching account, symbol, net quantity and working protection. Manual/conflicting exposure remains blocked. A shared thread/process lock covers new entry checks, reservation and first submission; each engine's exits remain available independently.

The updater now includes every unfinished crypto intent, even when the broker temporarily looks flat. Permission verification includes master control, strategy selection, crypto settings, targets and authorization generations. Unknown ledgers or changed permissions prevent an unsafe replacement. No active position is discarded during deployment. A narrowly tested first-install bridge lets the existing v3.3 updater verify unchanged permission while new family controls remain untouched defaults under its exclusive installation lock. After installation, all checks use the composite permission fingerprint. Global Live Off remains available during installation; new family settings wait until the rollout finishes.

## Next market-session checks

On Monday, September 21, check the first regular-session stock quotes, completed five-minute leader candles, completed fifteen-minute Nasdaq candles, actual VIX timestamps and worker progress. Compare recorded entry blockers with the received data and rules; a day with no qualifying setup is not itself an execution failure. For any owner-enabled trade, inspect confirmed entry quantity, protection acceptance, exit orders and after-cost account changes. Crypto collects data throughout the weekend and uses its own daily range. No synthetic setup or forced real-money order is needed for these checks.

## Verification boundary

Offline tests exercise actual engine lifecycles against stateful fake venues, including shared cash/positions across the QQQ and crypto engines, both admission orders, independent exits, fees, partial fills, rejection, lost responses, cancellation races, prepared-intent recovery, global/family Off, source expiry and manual exposure. Separate browser-handler tests exercise actual control requests and reconciliation. Visual checks use isolated fixtures and no broker connection.

The release passed **1,657 Python tests** across the app, deployment and research suites. All **104 interface tests** and the production build also passed. These are network-denied simulated-broker checks, including the migration and fault-injection cases above; they are not evidence of real fills or profitability.

Live preflight uses authenticated GETs only: account eligibility, asset metadata, native bars and quote timestamps. A stale quote is reported as stale, even if the latest endpoint returned it successfully. No real-money commissioning order is sent as a test, and no paper-account fill is claimed without separate paper credentials. Exact production counts and deployed revision are recorded in the release verification artifact after the complete suite and rollout finish.
