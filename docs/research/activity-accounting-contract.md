# Read-only daily account accounting

`pivot.activity_ledger.ActivityLedger(path)` keeps a separate private SQLite ledger.
It never creates, changes, or cancels an order and never changes account settings.

## Public methods

- `refresh(feeds, account_ref, now)` accepts an aware datetime. It verifies the USD
  account before and after bounded activity GET pagination, then atomically stores
  normalized evidence. Returns today's `summary` plus `changed_days`: effective
  dates affected by new or corrected rows, including dates outside the fetch window.
  Callers should rebuild retained daily reports for those dates. Unchanged repeats
  do not produce changed days. Provider/account/pagination failures return a fixed
  safe error with stale/unavailable status and no committed partial pages.
- `summary(account_ref, day, now)` accepts a `YYYY-MM-DD` New York date. Returns
  hashed `account_scope`, coverage/freshness, FILL counts and executed notionals,
  provisional observed fees, deposits/withdrawals, and captured account movement.
  Monetary values are decimal strings or null. Unavailable or malformed evidence
  is never converted to a zero. Categories with actual evidence outside current
  coverage remain visible as partial history; missing categories stay null.
- `record_equity(account_ref, account, observed_at)` requires a matching verified
  USD account. Retains first/last captured equity per New York date, including
  out-of-order observations, for at most 365 calendar dates per account.

Invalid caller inputs and local storage failures can raise; reporting must remain
outside the execution path. A concurrent refresh returns existing status without
another broker call. Default limits: 100 rows/page, 10 pages, and a 45-second
elapsed collection guard (an already-started request still uses the feed timeout).
The service refresh interval is separate. Each successful refresh adds two account
GETs for identity verification. Evidence is stale after 15 minutes without success.

## Accounting meaning

FILL counts are broker executions, including partial fills, not distinct orders or
completed round trips. Executed notional is incremental `qty × price`, never
`cum_qty × price`. Only USD-valued evidence is accepted; non-USD crypto pairs and
options without a proven contract multiplier are unvalued. Known compact non-USD
crypto aliases are rejected, as are explicit non-USD currencies. This collector
targets the app's USD equity and USD crypto instruments; it is not a general FX or
derivatives accounting engine.

`FEE` cost is negative `net_amount`. A documented USD-quoted `CFEE` asset debit is
valued once as `-qty × price`, with `net_amount=0`. Rebates reduce costs. These are
observed FEE/CFEE costs, not every possible tax, corporate-action charge, spread,
or slippage estimate. Fees can post after the trade; the API has no finality marker.
`fees.final` is always false. No rows means zero observed costs and `pending`, not
verified zero fees. Costs are dated by broker activity evidence and are never
arbitrarily assigned across trades or strategies. Same-ID corrections replace
prior values; explicit cross-ID corrections are flagged unverified. Silent broker
deletions cannot be inferred from this historical evidence ledger.

The creation-date query overlaps today and the preceding seven New York dates on
every refresh; it accounts for DST. Newly created fees with older effective dates
are retained and surfaced through `changed_days`. Corrections never returned by
that bounded query require a separate explicit historical reconciliation.

`account_change` compares its displayed first/last equity and timestamps. It is
always marked `partial_day:true`; it does not establish opening/closing equity.
Cash-flow adjustment subtracts deposits/withdrawals only when exact transaction
times place them in `(start, end]` and fresh collection covers the interval. NTA
`date` is an effective date even if serialized as a timestamp, so date-only or
otherwise unclassified transfers on interval boundary dates make adjustment null.
Equity already includes charged fees; fees are not subtracted again. The result
includes unrealized movement and unrelated account activity. It is not realized
profit, a strategy return, a complete daily return, or proof of profitability.

Opening inventory and exact linked final costs are not established. Therefore
`realized_net_pnl_usd=null`, `net_status='unverified'`, and `per_trade_net=[]`.
Do not derive win rate or net strategy profitability from these aggregates.

## Source contract

Verified against Alpaca's public documentation on 2026-09-19:

- [Account activities and field definitions](https://docs.alpaca.markets/us/docs/account-activities)
  define incremental fills, fee types, and signed deposit/withdrawal activity.
- [Activity query and pagination](https://docs.alpaca.markets/us/reference/getaccountactivities-2)
  specify creation-date filtering and last-activity-ID pagination.
- [Crypto fee accounting](https://docs.alpaca.markets/us/docs/crypto-fees)
  provides the asset-denominated CFEE example and delayed fee-posting warning.

Stored rows allowlist accounting fields, hash identities, and omit descriptions,
account IDs, and keys. Database mode is `0600`; a symlink destination is refused.
The returned scope is SHA-256 of the supplied account reference so report builders
can reject accidentally mixed accounts without displaying that reference.
