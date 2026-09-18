# Version 3.2.1: authentic native history for strategy validation

The running hourly strategies have current Alpaca stock data and actual spot-VIX fifteen-minute candles. The missing evidence for the separately defined faster-entry and VIX-exit experiments is native five-minute QQQ/VIX history. This release collects that evidence on AllSpark, where the authoritative free-request ledger resides.

After production inputs become current, a separate bounded worker attempts one QQQ five-minute history request and one actual `CBOE:VIX` five-minute history request per monthly experiment slot. The VIX request shares the existing monthly allowance and per-minute cap. It reuses current verified index metadata and consumes its claim before sending the request. Unknown outcomes, failures and restarts cannot create automatic retries. QQQ has its own durable claim and refuses pagination into a second request.

The collector keeps private immutable artifacts, original request/receipt/source timestamps, the verified exchange calendar and hashes. It verifies index identity, native resolution, OHLC values, ordering and completed-bar times. Incomplete history stays incomplete. Read-only export validates the saved files again and supplies only market evidence. The optional worker has no order interface and runs separately from account, analysis and execution loops.

The homepage's expanded data details distinguish saved validation history from a continuously updated five-minute trading feed. `/api/native-history` gives bounded status; `/api/native-history/export` supplies the authenticated research bundle. The existing hosted login protects both routes. These endpoints perform no collection or broker action.

`research/import_native_capture.py` validates both providers' evidence and converts the actual capture to the existing disconnected experiment format, preserving calendar exclusions, gaps and provenance. The predeclared native experiment compares faster location checks and matched-entry VIX exits with chronological periods and explicit costs. Its results remain separate from production rule selection.

## Scope and remaining limits

- Current live entry rules, exit rules, saved permission and purchase target retain their existing behavior.
- A one-time native history capture supplies historical evidence, not continuous five-minute freshness. Regular-session five-minute REST polling alone would consume roughly 78 calls per full session, above the current monthly allowance across a normal month; it must not be silently enabled under a zero-dollar budget.
- The source's four-hour/hourly method remains distinct from its five-minute scalp examples. Exact zone formulas, completed-candle timing and the five/one leader quorum remain documented app interpretations.
- A $5 fractional QQQ purchase can express long entries; it cannot express a whole-share QQQ short at current prices. No alternative instrument or larger purchase is substituted.
- Actual broker lifecycle commissioning and profitability remain unverified. Healthy incoming data and passing software tests do not establish either.

Validation includes concurrent capture claims, crash/restart behavior, shared quota limits, no-pagination enforcement, invalid source/frame/timestamps, source-watermark filtering, incomplete-history retention, immutable private files, read-only exports and conversion into the existing native research loader. Actual capture results are recorded separately after deployment.

The release passed 1,310 offline Python tests, 45 interface-model tests and the frontend production build before publication.
