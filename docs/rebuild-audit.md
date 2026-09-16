# Initial read-only rebuild audit (historical record)

**Superseded for live controls and execution by [the September 16 execution update](live-execution.md).** The record below describes the first rebuild before the owner requested a working live-money switch. Its statements that no execution path exists are historical, not the current implementation.

# Application audit and rebuild status

## Outcome

The active local application now consists of the `pivot` package and its small web interface. Original code and pre-existing dirty changes were preserved in `runtime/legacy-app-before-video-rebuild/`; the original tracked diff is also retained in `runtime/video-review-v2/pre-rebuild-working-changes.patch`. Neither is loaded by the new runtime.

**This is a tested read-only rebuild with provisional Nasdaq setup analysis, not a completed or approved live trading system.** Data and execution gaps are named below. The old trader was paused by the owner, then stopped only after read-only broker requests confirmed zero positions and zero open orders. No orders, cancellations, transfers or broker-mode changes were performed during the rebuild.

## Findings and disposition

| Severity | Area | Finding | Disposition |
| --- | --- | --- | --- |
| Critical | Strategy fidelity | Daily body levels and 1-minute entries were represented as related to 4h/1h videos. | Old detectors excluded; primary analyzer uses 4h levels and 1h events. Source distinctions are visible. |
| Critical | Source interpretation | First clip was prematurely treated as an independent required system or a mandatory additional stage. | Final owner direction makes V2/V3 primary. V1 tape, 30m, 5m, VWAP requirements excluded. |
| Critical | Data | Actual VIX is not entitled under current Massive credentials; actual NDX sample was delayed. | Direct provider failure is displayed; no ETF substitution. Live unavailable. |
| High | Breadth | Old context counted both Google share classes and used daily direction votes. | Exactly seven companies. Price reactions at precomputed zones replace old votes; numeric thresholds are labelled interpretations. |
| High | Live controls | Old pause command waited almost two minutes behind shared engine work. | Old runner retired; account reads and data reads have separate workers. New API has no live-enable or order mutation endpoints. |
| High | Runtime | Legacy strategies, perpetuals and prediction-market bots could start from environment/watchdog settings. | No legacy imports. LaunchAgents stripped of old strategy/live flags. Legacy market-open, server-view and auth-watch helpers disabled. |
| High | Lookahead / incomplete data | New levels and incomplete candles could imply hindsight if treated carelessly. | Closed candles only, confirmed prior pivots, pre-event zone timestamps, complete resample buckets, real prior-session calendar date. |
| High | Sizing | Old minimum-equity rule and fixed caps conflicted with chosen dollar amounts. | Independent Decimal-based target planner; no inherited 70% minimum or fixed share ceiling. Fractional planning is not claimed as a commissioned broker exit system. |
| High | Cross-site controls | Broad legacy API surface mixed trading controls and public routes. | Localhost-only server, trusted Host allowlist, explicit allowed Origin/custom intent for settings, no generic control route. Hosted deployment is not supported by this local-only rebuild. |
| Medium | UI truthfulness | Possible entries, stale observations, actual positions and approvals could be confused. | Explicit read-only/live-off state; timestamped broker observations; all-green analysis means review only; no profit promise. |
| Medium | Data pagination | Initial replacement limit stopped after ten pages even when the provider legitimately paginated more. | Bounded 64-page handling with repeated-token/truncation rejection; tested. |
| Medium | Persistence | UI settings needed a durable and independently verified save. | Dedicated SQLite transaction plus audit event; target/account/exposure validation on server; no inherited operator database. |

## Mechanical choices requiring validation

See [the source rulebook](video-strategy-audit.md). The recordings do not define numerical zone clustering, fixed candle anchors, strict majority/no-conflict rules, retest expiry, stop/target rules, allocation or the execution instrument. Current code is explicit about those choices. A green synthetic fixture proves the stated code path; it does not prove equivalence to a discretionary trader.

The stock observation feed is IEX, not consolidated US volume. QQQ is shown as a Nasdaq ETF proxy. Those limitations have not been hidden or presented as source requirements. The primary clips do not establish a need for the first clip's tape data.

## Checks

- Python tests deny every HTTP request and use temporary storage.
- Pure source/sequence tests cover precomputed levels, multi-candle retests, old/future/duplicate bars, missing or contradictory leaders, actual versus proxy/delayed VIX, source scope, missing previous sessions and an end-to-end all-confirmations research fixture.
- Sizing tests cover $25, $50 and $1,000,000; insufficient buying power; nonfinite/invalid amounts; and a whole-share short that cannot fit the target.
- API tests cover durable settings, exposure/stale-account rejection, Origin/Host validation, removed legacy routes and permanently uncommissioned live execution.
- Import checks prohibit legacy engine imports and broker submit/close/cancel calls in the new package.
- UI model tests cover target validation, freshness and safe display of provider text. Production build and browser inspection complete the interface checks.

## Not completed or established

- A paid/entitled real-time VIX connection; no subscription was purchased.
- Broker order entry and exit execution in the replacement. The previous turn's legacy fake-broker tests do not certify this new application.
- Historical strategy replay, paper-broker execution, profitability, slippage performance, disconnect/restart protection for real positions, or unattended trading.
- Public hosting migration. This update runs on this Mac; the Allspark deployment was not changed.

These are material blockers to live trading, not cosmetic cleanup. The app exposes them and cannot silently enable the old strategy while they remain.

## Final verification record

- **32 Python tests passed**; **3 UI model tests passed**.
- Vite production build passed after dependency updates; application bundle is about 8 KB before compression (excluding styles and HTML).
- `npm audit fix` updated Vite to 7.3.6 and supporting packages. The resulting npm audit reported **0 known vulnerabilities**. This covers the frontend dependency tree, not a penetration test.
- Browser verification on the separate, broker-free fixture saved a $50 target, preserved it after refresh, and rejected a $1,000,000 target against a $100 test balance. No setting-save or live-control actions were performed in the actual account page.
- The real local page showed the current Alpaca balance, live trading Off, no positions/orders, the new Nasdaq stage, and the VIX entitlement failure.
- Local service check: `legacy_loaded=false`, `live_enabled=false`; active code is `video-audit-v2`.
- `git diff --check` passed. No commit, push, paid subscription or hosted deployment was performed.
