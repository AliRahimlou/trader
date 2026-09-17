# Production release and continuing review — September 17, 2026

## Version 3.0 update

The owner's subsequent request to fix and publish the audited differences authorizes the implementation described in [the version 3 release specification](production-v3.md). Both entry methods are now independent, leaders use native five-minute input, and opportunity identity and expiry are explicit. The former fifteen-minute implementation and its replay remain a named historical baseline. The new policy requires owner review before new live entries; no approval is migrated automatically. AllSpark installation uses the existing entry-gated updater. The initial release scope below describes the earlier version 2 releases.

## Authorized release scope

After reviewing the research report, the owner requested production deployment of the reviewed protection fixes and decision recorder, personally changed the saved purchase target from $25 to **$5**, and requested continuing monitoring and primary-video research. The $5 setting was verified through the hosted API at 16:18 UTC. It is a purchase target, not a loss limit. The application minimum remains $1; no sizing code change is needed.

This authorization supersedes the earlier research-only deployment restriction in `docs/research/README.md` for this release. The historical [research report](research/REPORT.md) and [verification record](research/verification.json) describe the evidence before this deployment request. Their negative/inconclusive findings remain valid.

The release contains the existing Nasdaq/leader/VIX entry conditions, durable decision diagnostics, asynchronous rejection handling, and protection acceptance/reconciliation safeguards. A follow-up audit also fixes an internal exit inconsistency: the nearest opposing premarked target may come from four-hour areas or previous-day extremes, both of which the app already recognizes as valid levels. The videos do not specify these exits. Experimental parameter variants stay offline; the production image excludes the research package. No new loss limit, size increase, data subscription, or alternate strategy is activated. The initial releases used an Off-only updater. The owner's subsequent request authorizes v2.2.0 routine updates with saved Live permission preserved, an entry-admission hold and fresh broker-confirmed flat account. The one-time legacy upgrade constraint is documented in [AllSpark hosting](allspark-hosting.md). The deployment process never enables trading or approves changed policies.

## Primary recordings and fidelity

The two primary files are `ScreenRecording_09-14-2026 17-25-23_1.MP4` and `ScreenRecording_09-14-2026 18-11-24_1.MP4`. The later reference to `11-25-23_1` is interpreted as the available `17-25-23_1`. The supplementary first clip supplies no additional mandatory entry rules.

The supported sequence is premarked Nasdaq location, a sweep or break/retest, leader behavior, and opposite actual-VIX reaction. The [timestamped source table](research/source-rule-map.md) remains authoritative about evidence strength. Version 3 corrects the observed five-minute leader timeframe and supports repeated crossings in its documented area approximation. Exact reproduction is still not established: leader-zone construction, participation, persistence, session convention, and exits are not fully specified by the recordings. The current account and fractional size cannot execute the videos' short examples.

## Monitoring and research plan

| When | Evidence and action |
| --- | --- |
| Every application decision cycle on AllSpark | Record the analysis checkpoint, pre-existing levels, event type/time, each leader's reaction and age, actual-VIX evidence, freshness, permission, and exact blocker. Deduplicate unchanged polling; preserve decision history in the existing durable database. |
| Every 15 minutes during the weekday market window, when Codex is available | Read the authenticated hosted snapshot. Check revision, worker/account/analysis freshness, stock/VIX coverage and quota, $5 setting, decision trace, managed trade, positions/orders and protection status. Notify only for a new fill/completion, rejection, protection problem, stale service/data, size/configuration change, or required owner action. Do not treat normal waiting as failure. |
| After each market session | Read the bounded, password-protected `/api/decisions` history pages and review new decision checkpoints and completed outcomes by sweep versus break/retest. Separate repeated checks from distinct events. Reconcile fill quantities, protective acceptance/cancellation and final position; mark unavailable evidence explicitly. Record fees separately and do not call gross P&L net profit. |
| Next bounded offline research step | Evaluate the frozen version 3 choices on additional untouched sessions with genuine five-minute data and actual VIX. Retain the old baseline separately. Investigate manual-area construction/session alignment and participation using synchronized accepted/skipped examples and after-cost evidence. Do not change live rules from a few trades. |
| Before a future strategy or size change | Present timestamped source support, untouched after-cost results, execution evidence, and concrete proposed changes for owner review. Current nine-session zero-trade results establish no edge. |

The application and its durable trace recorder run on AllSpark independently of the laptop. Codex heartbeat reviews run in the desktop environment and require it to be available; they are not an always-on server alert service or protective-order mechanism. Host/network outages can interrupt both execution and observation. No paid service is added.

## Incident and release boundaries

The monitoring task is read-only with respect to account trading: it never submits/cancels orders, activates Live, changes size or strategy, or silently deploys revisions. It reports protection uncertainty, unresolved order identity, stale state with exposure, and share-count discrepancies promptly for owner action. Existing application recovery continues under the owner-enabled execution policy.

Keep unknown order intents and durable trade history. Never restart the retired local runner. For v2.2.0 code rollout/rollback, require fresh broker-confirmed zero positions/orders, no active managed trade, tested image, exclusive entry-admission lock, durable hold and unchanged saved permission. Existing exposure continues its protective lifecycle until flat; unknown recovery retains the entry hold. The legacy Off-only path applies only before this protocol has been commissioned. Do not restore an older ledger over new order history. Deployment and test success do not establish successful live fills or profitability.

## Release verification

The research commit `c3e41c3` passed 687 Python tests, 9 interface tests, UI build, 494-checkpoint default-analyzer parity, and byte-identical replay. An independent packaging rehearsal passed all 687 tests with exactly the container test-stage source files. The production release additionally requires GitHub's actual Linux/Python 3.12 container build, followed by AllSpark revision, authentication, $5 persistence, data freshness, and decision-recorder verification. The final deployment result is recorded in the task and GitHub release history.
