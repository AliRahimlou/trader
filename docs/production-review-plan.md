# Production release and continuing review — September 17, 2026

## Authorized release scope

After reviewing the research report, the owner requested production deployment of the reviewed protection fixes and decision recorder, personally changed the saved purchase target from $25 to **$5**, and requested continuing monitoring and primary-video research. The $5 setting was verified through the hosted API at 16:18 UTC. It is a purchase target, not a loss limit. The application minimum remains $1; no sizing code change is needed.

This authorization supersedes the earlier research-only deployment restriction in `docs/research/README.md` for this release. The historical [research report](research/REPORT.md) and [verification record](research/verification.json) describe the evidence before this deployment request. Their negative/inconclusive findings remain valid.

The release contains the unchanged default entry rules, durable decision diagnostics, asynchronous rejection handling, and protection acceptance/reconciliation safeguards. Experimental parameter variants stay offline; the production image excludes the research package. No new loss limit, size increase, data subscription, or alternate strategy is activated. The updater installs only with Live Off and a fresh flat account. The owner activates Live in the hosted app after installation; the deployment process never enables trading.

## Primary recordings and fidelity

The two primary files are `ScreenRecording_09-14-2026 17-25-23_1.MP4` and `ScreenRecording_09-14-2026 18-11-24_1.MP4`. The later reference to `11-25-23_1` is interpreted as the available `17-25-23_1`. The supplementary first clip supplies no additional mandatory entry rules.

The supported sequence is premarked Nasdaq location, a sweep or break/retest, leader behavior, and opposite actual-VIX reaction. The [timestamped source table](research/source-rule-map.md) remains authoritative about evidence strength. Exact reproduction is not established: visible leader charts use five minutes while production uses fifteen; manual areas include crossings; leader-zone construction, participation, persistence, session convention, and exits are not fully specified. The current account and fractional size cannot execute the videos' short examples.

## Monitoring and research plan

| When | Evidence and action |
| --- | --- |
| Every application decision cycle on AllSpark | Record the analysis checkpoint, pre-existing levels, event type/time, each leader's reaction and age, actual-VIX evidence, freshness, permission, and exact blocker. Deduplicate unchanged polling; preserve decision history in the existing durable database. |
| Every 15 minutes during the weekday market window, when Codex is available | Read the authenticated hosted snapshot. Check revision, worker/account/analysis freshness, stock/VIX coverage and quota, $5 setting, decision trace, managed trade, positions/orders and protection status. Notify only for a new fill/completion, rejection, protection problem, stale service/data, size/configuration change, or required owner action. Do not treat normal waiting as failure. |
| After each market session | Review new decision checkpoints and completed outcomes by sweep versus break/retest. Separate repeated checks from distinct events. Reconcile fill quantities, protective acceptance/cancellation and final position; mark unavailable evidence explicitly. Record fees separately and do not call gross P&L net profit. |
| Next bounded offline research step | Preregister a five-minute versus fifteen-minute leader comparison with the same feed/session, zones, sample, costs and thresholds. Obtain required source/data coverage first. Then investigate manual-area construction/session alignment and participation/persistence using synchronized accepted/skipped examples. Do not change live rules from a few trades. |
| Before a future strategy or size change | Present timestamped source support, untouched after-cost results, execution evidence, and concrete proposed changes for owner review. Current nine-session zero-trade results establish no edge. |

The application and its durable trace recorder run on AllSpark independently of the laptop. Codex heartbeat reviews run in the desktop environment and require it to be available; they are not an always-on server alert service or protective-order mechanism. Host/network outages can interrupt both execution and observation. No paid service is added.

## Incident and release boundaries

The monitoring task is read-only with respect to account trading: it never submits/cancels orders, activates Live, changes size or strategy, or silently deploys revisions. It reports protection uncertainty, unresolved order identity, stale state with exposure, and share-count discrepancies promptly for owner action. Existing application recovery continues under the owner-enabled execution policy.

Keep unknown order intents and durable trade history. Never restart the retired local runner. For code rollout/rollback, require fresh broker-confirmed zero positions/orders, no active managed trade, Live Off, tested image and exclusive deployment lock. Do not restore an older ledger over new order history. Deployment and test success do not establish successful live fills or profitability.

## Release verification

The research commit `c3e41c3` passed 687 Python tests, 9 interface tests, UI build, 494-checkpoint default-analyzer parity, and byte-identical replay. An independent packaging rehearsal passed all 687 tests with exactly the container test-stage source files. The production release additionally requires GitHub's actual Linux/Python 3.12 container build, followed by AllSpark revision, authentication, $5 persistence, data freshness, and decision-recorder verification. The final deployment result is recorded in the task and GitHub release history.
