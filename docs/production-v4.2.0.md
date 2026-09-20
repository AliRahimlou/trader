# Version 4.2.0: daily trading evidence and broker costs

The app previously showed current entry blockers and gross stock results, but did not provide a shared daily review or collect actual broker fee activities. This release adds a read-only accounting worker and saved daily reports for both strategy families.

## Product behavior

- A compact **Today's trading review** panel sits below the strategy controls. The account total stays prominent. Expand the panel to see the selected strategy's recorded entries, closures, open lifecycles, blockers, trade reasons and evidence still needed.
- Today, previous-day and date controls read saved reports. Strategy views filter the report's family sections without changing execution. Account-wide costs remain explicitly account-wide.
- Gross results, provisional recorded fees, verified net outcomes and captured account movement have distinct labels. Unknown values do not become zero. A day without trades does not become a profitable day.
- The report displays its evidence cutoff, generation time and integer review revision. Reporting failures and account identity changes remain visible alongside any retained historical evidence.

## Accounting and evidence

The background worker queries Alpaca account activities approximately every five minutes. It verifies the account before and after a bounded, overlapping eight-date creation-time query, deduplicates incremental fills by activity identity, replaces corrected records, and converts supported fee activities once. It does not send or cancel orders.

Fees can arrive after a trade day. New or corrected activities rebuild affected reports, including older effective dates within the retained review horizon. Dates beyond verified collection coverage retain observed amounts with incomplete-history labels. Date-only cash movements do not acquire invented timestamps. Unsupported denominations, malformed values and pagination failures remain unverified.

Each day also retains the first and latest captured account equity. Where transfer timing and coverage are sufficient, the panel can show movement over that recorded interval after deposits/withdrawals. It includes unrealized changes, is usually a partial-day interval, and is not labeled realized or strategy profit.

Daily reports use account-scoped private storage, retain 365 days and up to eight evidence revisions per day, and preserve source/policy/build references. Repeated refresh timestamps alone do not create an evidence revision. The Socrates execution ledger now retains the original exit trigger through final reconciliation; this changes logging only.

## Important interpretation limits

Alpaca does not provide a fee-finality marker in the documented activity interface. Unlinked account-wide fees cannot be reliably assigned to an individual strategy merely because a trade completed on the same date. The current collector therefore supplies observed costs and leaves per-trade net results unverified where complete attributed evidence is absent. The UI does not present pending outcomes as confirmed wins or losses.

The review's next-investigation suggestions are deterministic summaries of retained evidence, not an automated trading-rule optimizer or a causal explanation of market behavior. The [intraday research plan](research/intraday-evaluation-plan.md) keeps proposed changes separate from current live rules. The [accounting contract](research/activity-accounting-contract.md) describes the data boundary.

## Execution isolation

Saved Live permission, selected markets, sizing, entry rules and exit timing remain unchanged. Reporting runs outside HTTP handlers and execution loops. Its health does not trigger a container restart that could interrupt position management. Report reads never make broker requests. Additional normal reporting traffic is small, with a bounded maximum refresh burst; this is not a global broker rate limiter.

The existing crypto manager can retain protected positions across midnight. This reporting release does not add a same-day forced exit. That is a separately defined intraday execution change, with its own cutoff and cancellation/recovery tests.

## Validation

The focused tests cover activity pagination/deduplication, late fees and corrections, denominated costs, account switches, cash-flow timing, New York/DST boundaries, partial outcomes, private report persistence, revision history and slow-provider isolation. UI checks cover unknown values, escaping, date-request races, strategy filtering, stale evidence and narrow screens.

All 1,803 Python tests and 142 browser-model tests passed, and the dashboard production build completed. An actual read-only Alpaca activity request passed identity and pagination checks. No live test order was used. Desktop and 390-pixel preview checks verified date navigation, family filtering and absence of horizontal overflow. Release verification records the installed revision separately.
