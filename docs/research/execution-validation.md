# Execution validation — September 17, 2026

## Finding and evidence level

**Improved execution software, with offline evidence. Broker paper and live lifecycle validation remain outstanding.** No broker orders, cancellations, live settings, credentials, host processes or deployments were changed for this work. The audit tests use an in-memory broker and temporary databases; `pivot/tests/conftest.py` prohibits real requests.

Baseline: `facc507ae2761a76916031994bcf9f956f9369fe`. Local release work is isolated from the watched production branch. The reported September 17 observation of $25 permission On and zero orders/fills at 15:16 UTC is a dated operational observation, not execution proof. Use this research run's timestamped read-only operational baseline for current account/data facts; this execution audit made no authenticated account calls.

Two software gaps were reproduced:

1. A JSON order response or subsequent broker lookup reporting `rejected` did not pause new entries, whereas an HTTP rejection did. A rejected stop could therefore cause liquidation followed by another entry under unchanged permission. The new six-case regression matrix failed against the baseline. The local fix now pauses entry permission, records the rejection once across normal repeated reads/restarts, retains actual fill quantities, and continues management. An exit rejection still requires owner action rather than blind replacement.
2. A stop stuck awaiting acceptance, suspended or finished for the day could be displayed as generic broker protection indefinitely. The local release now distinguishes working protection from unconfirmed/unavailable protection, with a persistent acceptance deadline and cancellation-safe recovery.

These changes do not demonstrate strategy profitability, broker acceptance, a maximum loss, or a successful live purchase/protect/sell cycle.

## Current official documentation

Sources retrieved **2026-09-17**, official Alpaca documentation only:

- [Fractional Trading](https://docs.alpaca.markets/us/docs/fractional-trading): its supported-types section permits fractional market, limit, stop and stop-limit orders with DAY time-in-force. Use either notional or quantity, not both; both accept up to nine decimal places. Assets must be fractionable. Fractional short selling is unsupported. The same page retains a contradictory market-only/normal-hours sentence near its end. The explicit supported-types section is consistent with the current order matrix, but account-level commissioning remains necessary.
- [Placing Orders](https://docs.alpaca.markets/us/docs/orders-at-alpaca): its fractional matrix supports DAY, not GTC, stops. Regular-session DAY orders end with the session. `new` means routed; `accepted`/`pending_new` do not establish execution eligibility. `suspended` is ineligible; `done_for_day` cannot execute again that day. Pending cancellation is not terminal confirmation. Stop prices at or above $1 allow two decimal places, and a stop does not guarantee its execution price.
- [Versioned Placing Orders](https://docs.alpaca.markets/us/v1.1/docs/orders-at-alpaca): this version explicitly describes converting buy stops to stop-limit orders, while the current unversioned page omits that conversion text. Do not assume short-cover stop behavior from the older text alone. The proposed $25 deployable model excludes unavailable fractional shorts regardless.
- [Paper Trading](https://docs.alpaca.markets/us/docs/paper-trading): paper fills are simulated. Paper results omit several real execution effects, including latency slippage, queue position and regulatory fees. A passing paper lifecycle would establish integration evidence, not live fill quality or profitability.

## Exact intended $25 long workflow

This is a description of the reviewed code path and its required commissioning evidence, not a record of submitted orders. QQQ at $675 in the audit test is a synthetic price.

| Stage | Required action/evidence | Offline result |
| --- | --- | --- |
| Admission | Account identity matches reviewed permission; active account, fresh broker clock, regular session, sufficient buying power, no existing exposure/orders, tradable and fractionable QQQ, current quote/data/VIX and complete setup. | Existing admission suite passes. |
| Purchase | One durable intent; `buy`, `market`, `notional: "25.00"`, `day`, extended hours false; never send both notional and quantity. | Pass. |
| Entry reconciliation | Find the exact client order ID after uncertainty. Confirm terminal entry state and cumulative filled quantity. Cancel an unfinished remainder before managing its actual fill. | Pass, including timeout/restart and partial entry. |
| Protection | Submit one `sell` stop for the confirmed remaining quantity, using the saved stop price and DAY duration. A $25 fill at a synthetic $675 is represented by `0.037037037` shares in the precision test; the fill quantity determines protection and exit size. | Pass in memory; real broker acceptance NOT RUN. |
| Managed target/close | The app evaluates its target and broker session close. A target is not a broker-held take-profit order. | Offline target and session-close paths pass. |
| Cancel protection | Request cancellation, then wait for a terminal lookup. A DELETE response alone cannot justify a sale. | Pass, including delayed cancellation and stop fills during cancellation. |
| Exit | Re-read position and all owned fill quantities. Sell only the actual remaining shares, never the original dollar amount. | Pass, including partial stop fills and lost exit responses. |
| Final reconciliation | Confirm no position, no working owned order, and entry/exit fill quantities reconcile. Manual resolution remains explicitly unverified for economics. | Pass offline. |

The app does not submit native bracket/OCO exits for this fractional workflow. The broker stop, once working, is broker-held; the target, session-close decision, reconciliation and recovery depend on AllSpark and its connection. The scheduled Codex monitor is an additional observer and is not the execution worker.

## Proposed local protection policy

**The 30-second deadline is a proposed engineering safety policy for release review. It is not an approved live loss limit or an assertion about normal Alpaca acceptance speed. It has not been deployed.**

`WORKING_PROTECTION`, `UNAVAILABLE_PROTECTION`, and `PROTECTION_CONFIRM_SECONDS` in `pivot/execution.py` make the classification explicit:

| Observed condition | Local release response |
| --- | --- |
| `new`, `partially_filled` | Treat the known remaining order as working, subject to quantity reconciliation. Continue monitoring. |
| `accepted`, `pending_new`, `accepted_for_bidding`, `stopped`, or an unrecognized nonterminal status | Display that executable protection is unconfirmed. Allow at most 30 seconds from the persisted stop intent before pausing entries and beginning recovery. Existing target/session-close rules can request an earlier exit. |
| `suspended`, `done_for_day`, `pending_cancel`, `pending_replace`, `calculated` while still in the open-position stage | Immediately pause entries and start cancel-confirm/reconcile-then-exit recovery. Never count these as working protection. |
| `rejected`, `expired`, `canceled`, or another terminal stop with shares remaining | Pause entries and close reconciled remaining shares through the existing exit path. |
| Stop lookup missing or failing beyond its deadline | Pause entries, persist recovery intent, require Alpaca review, and send no competing sale. Resolve the original client ID before cancellation or exit. |
| Cancellation missing, delayed or uncertain | Remain paused and explicitly request owner review. Continue read reconciliation; do not submit a competing exit until terminal cancellation/fill confirmation. |
| Replacement or foreign order | Existing manual-attention behavior takes priority. Do not compete with an owner's order. |
| Outside regular hours with exposure | Report exposure and possible DAY expiry. Do not claim after-hours protection or send an ineligible market exit. Resume safe reconciliation at the next eligible session. |

The deadline is written before submitting the stop. A recovered older intent without this field receives one deadline on first observation, saved to disk. Restarting does not renew it. A late broker response can safely move recovery forward, but a previously paused permission remains Off. Missing market quotes do not defer protection-timeout recovery. Early account, clock or position read failures also pause an already observed unconfirmed stop after its deadline using local evidence alone; cancellation and sale still require normal broker/account/quantity validation. An unrelated read failure does not itself reclassify a last-known working stop or change a manual-attention trade into an automated exit.

The deadline is enforced at the next successful worker observation, not by an independent broker timer. The execution loop waits five seconds after a cycle, and provider calls can delay that cycle. A host outage can exceed the deadline without any app action. Protection also remains unconfirmed while an entry is partially filled and its unfinished remainder is awaiting cancellation. These intervals cannot be represented as guaranteed bounded loss.

## Reproducible verification

From the isolated research checkout:

```sh
/Users/alirahimlou/myapps/trader/.venv/bin/python -m pytest -o addopts='' -q \
  pivot/tests/test_execution.py \
  pivot/tests/test_execution_research_audit.py \
  pivot/tests/test_insight_execution.py
```

Result on September 17: **128 passed**, including **36 new research-audit cases**. The complete project suite is reported in the overall research report. The temporary fake account's `mode="live"` is an in-memory admission fixture; it does not contact Alpaca.

New cases cover nine-decimal quantity accounting; unsupported $25 shorts; initial and later broker rejections for entry/stop/exit; partial stop fills followed by rejection; accepted versus unseen stop timeouts; restart recovery; delayed cancel with partial fill; DAY expiry and next-session close; lost exit responses; nonworking and pending protection statuses; deadline migration; missing lookup/network responses; missing quotes; a stop filling after uncertain cancellation; and recovered unsubmitted stops that must not be placed outside the session or during an exit.

## Commissioning checklist: actual status

| Evidence | Offline simulation | Alpaca paper | Actual live |
| --- | --- | --- | --- |
| $25 buy → exact fill quantity → accepted fractional stop → target exit → flat/no orders | PASS | NOT RUN | NOT DEMONSTRATED |
| Broker rejection and protective rejection recovery | PASS | NOT RUN | NOT DEMONSTRATED |
| Partial fill and cancellation race cannot create an unintended short | PASS | NOT RUN | NOT DEMONSTRATED |
| Unknown submissions survive restart without duplicate POST | PASS | NOT RUN | NOT DEMONSTRATED |
| Suspended/expired/unconfirmed protection and deadline persistence | PASS | NOT RUN | NOT DEMONSTRATED |
| Early session close and overnight residual exposure handling | Simulated close/reopen paths pass | NOT RUN | NOT DEMONSTRATED |
| Real protective acceptance latency and exposure duration | Not measurable | NOT RUN | NOT DEMONSTRATED |
| Real slippage, activity fees and after-cost realized P&L | Not measurable | Simulation would remain insufficient | NO SAMPLE |

“PASS” means only the named offline scenarios passed. In-memory fills do not model venue liquidity, broker outage distributions, real partial-fill rates, stale broker replicas or exchange halts.

## Remaining promotion gates and owner response

1. Keep the research code separate from the watched deployment branch until reviewed and explicitly authorized. The safety policy and broader risk limits need review before release; the $25 purchase target is not a defined loss budget.
2. Build/review an isolated paper commissioning harness with a hard paper-host allowlist and separate storage/credentials. The current executor deliberately refuses paper accounts in both its enable and entry paths. Do not bypass that by relabeling a paper account as live. No broker paper orders were sent by this audit; separate paper credentials and a commissioned paper harness were unavailable.
3. In that harness, record sanitized order state transitions, actual returned type/time-in-force/stop price/quantity, terminal cancellation, fills, position residuals and worker timestamps. Demonstrate at least 20 complete paper cycles, including at least five controlled recovery cases covering restart/uncertainty/partial cancellation; require zero unexplained residuals or duplicate entries. These are proposed commissioning gates, not statistical evidence of an edge.
4. Observe the frozen candidate in shadow mode for independent eligible opportunities and all gate rejections; keep research order paths disconnected. Historical strategy results and shadow observations do not count as paper execution events.
5. A later live trial requires explicit authorization, reviewed loss limits and unchanged small size. Use the first authorized cycle to verify actual broker-held protection, exit quantity, no remaining orders and activity-derived costs. One successful cycle is commissioning evidence, not profitability evidence. No live trial is performed here.
6. If protection is unconfirmed, cancellation is stuck, an exit is rejected, the worker is stale with exposure, or share counts differ, pause new entries and inspect the exact QQQ position and working orders in Alpaca. Do not retry an unknown submission or issue a second sale blindly. Restore reconciliation first; retain any working broker protection while deciding the safe resolution. Do not restart/deploy over an unresolved active trade.

Order-state rejection events are deduplicated through saved last-seen state across ordinary polling/restarts. Event insertion and trade-state persistence remain separate database transactions, so a process crash between those operations can duplicate a diagnostic event; it still cannot cause the same order intent to be submitted twice. A broader durable event/outbox redesign is outside this focused release.
