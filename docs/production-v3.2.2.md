# Version 3.2.2: live execution audit and recovery fixes

This release addresses failure cases reproduced during the September 18 live-readiness audit. It does not change the two production strategies, increase the purchase target, or grant live permission.

## Reproduced defects

- **Unconfirmed exits lacked an incident deadline.** A market exit could remain partially filled, or a protective-stop cancellation could remain pending, without raising a persistent alert. The app now retains the start time and a 30-second confirmation deadline across restarts, records the incident, pauses new entries, and prominently requests review of the broker position and orders. Reconciliation continues; an uncertain cancellation or order never authorizes a competing exit. Completion records resolution and does not automatically re-enable entries. This deadline is an operational alert, not a promise that orders fill in 30 seconds.
- **An unsent entry recovered after restart did not repeat all broker checks.** Before its first submission, the app now verifies account eligibility, identity/mode and existing positions/orders again. Changed broker state prevents submission; the original permission and data expiry still apply. Already-attempted entries retain their identifier-based reconciliation path.
- **Settings saves could freeze the web API while waiting for storage or the entry lock.** Blocking save work now runs outside the API event loop. Health and other requests remain responsive while a save waits.
- **A failed Live-control response could leave an obsolete error visible after the server confirmed the requested state.** Fresh snapshots reconcile a pending request without resubmitting it. A rejection or an unconfirmed result remains visible; an old poll cannot override a newer control request.

The purchase area also reports a broker-provided short-selling restriction when present. A fractional dollar target does not create short-sale eligibility. No different instrument, increased target, or trade is substituted.

## Verification boundary

Regression checks cover durable exit incidents, restart and provider failures, partial exits, stop-cancel races, recovery without duplicate orders, recovered-entry broker changes, concurrent settings/health requests, and asynchronous Live-control reconciliation. **1,338 Python tests and 56 interface tests passed, and the production frontend build passed.** Test brokers and market fixtures cannot establish actual broker fills.

The live audit uses GET requests and the hosted status page only. Current data, active workers, account eligibility and a valid purchase calculation are necessary checks; they do not establish a future order's acceptance, an exact reproduction of discretionary video decisions, or profitability. Actual buy/protect/sell commissioning remains separate. Experimental faster entries and VIX exits are not part of this release.

The existing updater preserves the saved owner permission and target, defers replacement around active exposure, and verifies the installed version before releasing its deployment hold.
