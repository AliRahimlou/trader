# Durable execution checks

The v2.1.1 release adds a separate record of the execution worker's checks. A saved signal trace explains why the analyzer accepts or rejects a setup. An execution check explains where the worker subsequently waits, encounters invalid data or a provider error, manages an existing trade, or reaches an order operation.

## Read the evidence

- `GET /api/snapshot` includes `execution_check` and `execution_diagnostic_error`, alongside the existing signal trace. A restored record is historical. Only a successful save from the current process within thirty seconds can be marked current.
- `GET /api/execution-checks?limit=50` returns password-protected history. The maximum page size is 100. Pass `next_before_id` as `before_id` to read the next page.
- The homepage's **Decision logging** indicator requires fresh, saved signal and execution records. A missing or stalled recorder is visible even if trading analysis continues. “Recording” describes the recorder, not trade eligibility or profitability.

Each record identifies its observation time, app version/build, execution stage and outcome, signal context, and trade/operation context when available. Exception categories are retained without exception text, provider responses, credentials or raw account identifiers. Stable repeated checks share one row with first/last timestamps and an observation count. The store retains up to 24,000 distinct states. Poll counts are not independent opportunities.

Since v4.2.1, records also retain the broker adapter's hashed `account_ref` and a fresh broker clock's `regular_session_open` boolean when known. A pre-entry observation needs either a fresh, error-free account snapshot or a direct broker account read; an existing trade retains its recorded owner. Account changes cannot attribute earlier waits to the newly connected account. Missing or conflicting identity remains unassigned. Legacy records require a retained trade to prove ownership; earlier pre-entry records cannot be retroactively assigned.

Daily reviews separate closed-session and unknown-session signal observations from actionable entry blockers. Account-scoped pre-entry execution failures remain visible even before a trade exists. A fresh, explicitly closed broker clock produces a regular-session wait before new-entry VIX checks. This does not bypass Live permission, deployment holds, existing position management or broker checks before entry.

Logging failure is exposed separately and does not change the trade logic, live permission or saved amount. Unexpected execution errors report uncertainty; they do not assert that no order was attempted. The existing durable order-intent ledger and broker reconciliation remain authoritative for order handling.

## Limits

History starts when its recorder is installed; it cannot reconstruct earlier transient execution messages. These records preserve check outcomes, not every exchange tick or every intermediate broker event. Unknown outcomes still require reconciliation. Signal traces and execution checks are independently timed observations and do not grant permission for an order.

Software tests cover mocked failures and persistence. They do not establish successful real-money fills, profitable performance or exact reproduction of the videos. Current signal interpretations remain subject to the [primary-source findings](primary-source-followup-20260917.md).
