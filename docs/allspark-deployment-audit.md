# AllSpark deployment verification · September 16, 2026

The video app replaced the older hosted application at [media.mytap.net/pivot/](https://media.mytap.net/pivot/). The initial release was `3a8411e2d207d9a5c8f07fa93b07bdff0373f9e8` from the repository's `main` branch.

## Confirmed

- The same release passed **557 Python/deployment tests and 9 interface tests** locally, in AllSpark's Linux Docker build, and in [GitHub Actions](https://github.com/AliRahimlou/trader/actions/runs/35155910096).
- Authenticated public-browser verification displayed the rebuilt app, current account state, the AllSpark runtime label and the deployed revision. Anonymous homepage, snapshot and health requests returned HTTP 401.
- All eight equity histories were current. Actual VIX history contained 260 complete regular-session candles with no data errors. The saved request ledger remained at two app requests, 50 reserved, and 848 available within the local allowance.
- The purchase setting and audit history were migrated privately. The hosted service started with Live money Off and no positions, orders or active trade. No live order, cancellation or activation was used to test deployment.
- The local Mac backend and dashboard LaunchAgents were stopped and disabled. Local execution permission was cleared only after confirming a fresh flat account; a private database backup was retained. The older AllSpark container remains stopped with its original data volume preserved.
- The hosted container runs without root privileges, with a read-only application filesystem, persistent private runtime storage, bounded logs and automatic restart. Its host port is bound only to loopback; the existing authenticated Caddy route uses the private Docker network.
- Provider credentials and migrated SQLite files have owner-only permissions. No credentials, runtime databases, installed dependencies or private deployment files were included in the GitHub release.
- The user update timer is enabled, runs every five minutes, and persists without a login session. Its initial service check succeeded and reported the correct GitHub revision.

## Remote updates

The updater retrieves code directly from public GitHub over outbound HTTPS. A laptop connection to AllSpark is not part of its normal update path. Builds, checks, the live-Off/flat-account gate, revision validation, rollback and durable rejection of failed releases are implemented and covered by isolated tests. The footer reports pending, blocked, installed or failed update status without exposing private operator errors.

This audit file is a follow-up documentation commit used to exercise that same updater path after the initial installation. The installed app's footer and GitHub history identify the latest deployed revision.

## Operating requirements

AllSpark and its internet connection must remain available. The hosted Live money control is owner-operated. Future releases wait until it is Off and all positions/orders have finished. This deployment does not verify future live fills, discretionary strategy equivalence or profitability.

See [the hosting guide](allspark-hosting.md) for remote editing and deployment steps.
