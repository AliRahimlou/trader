# Version 4.2.1: trade readiness and accurate wait records

Daily reports could present closed-market signal observations as entry blockers and omit execution failures that occurred before a trade existed. This release separates market-hours observations and associates new execution records with the broker adapter's hashed account reference. Older records without proven ownership remain explicitly unassigned.

## Changes

- Closed and unknown market-session observations have their own report counts and reasons. They no longer create misleading actionable strategy-blocker summaries.
- Pre-entry account, data and execution failures appear in the matching account's review without requiring an existing trade. Explicit identity and retained trade ownership must agree; account switches do not mix evidence.
- The expanded daily review shows observation coverage, account-matched execution checks and missing historical account associations.
- A fresh broker clock that explicitly says the stock session is closed produces a market-session wait before checking new-entry VIX availability. Position management, protection, Live Off and deployment holds retain priority.

Signal rules, saved purchase amounts, selected markets and permissions are unchanged. Socrates' hourly events and leader/VIX confirmations remain distinct from the crypto range-reversal rules. No position is opened merely to prove that the app can trade.

## Validation and boundaries

New offline lifecycle tests feed both genuine analyzers into the execution engines on one stateful fake account, in either admission order. They exercise small fractional stock and Bitcoin purchases, fee-adjusted protection, independent exits and prevention of repeated entries after permission changes. Readiness tests cover fresh/stale clocks, account identity changes, legacy evidence, pre-entry failures and continued management outside market hours.

All **1,867 Python tests** across app, deployment and research passed. All **144 browser-model tests** and the dashboard production build passed. An independent diff review also reproduced a stale signal-clock classification error; capture-time freshness checks and regression cases fix it before release.

These tests verify executable code paths, not actual broker fills or profitable performance. A live read-only capability check can establish account and asset permissions but cannot commission real order acceptance. The videos also leave numerical conventions unresolved; the [Socrates specifications](research/current-method-specifications.md) and [executable crypto baseline](production-v4.0.0.md) disclose those interpretations. The [intraday evaluation plan](research/intraday-evaluation-plan.md) keeps any new strategy experiments separate.
