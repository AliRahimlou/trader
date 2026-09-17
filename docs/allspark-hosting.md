# AllSpark hosting and remote updates

## Access

The hosted app is at **https://media.mytap.net/pivot/**. The existing password-protected Caddy route is retained. The public app and its account/API routes require the same login. The service connects outbound to Alpaca and InsightSentry; API keys stay in private configuration on AllSpark and are never committed to GitHub or built into the browser.

AllSpark runs the worker independently of the laptop, with automatic container restart. AllSpark itself and its internet connection must remain available. The laptop trading worker is retired during migration to prevent duplicate account management. The hosted installation starts with Live money Off; enable it yourself in the hosted interface after reviewing the rules.

## Make changes from any network

1. Edit the repository at https://github.com/AliRahimlou/trader from GitHub, another computer, or a coding workspace.
2. Commit the desired changes to `main`. A pull request is useful for reviewing larger changes first.
3. GitHub runs the checks. AllSpark independently fetches `main` through an outbound HTTPS connection and builds a tested release. No local Wi-Fi, Tailscale, inbound SSH access, or GitHub runner access to broker keys is required.
4. With the v2.2.0 updater and worker installed, routine updates preserve the saved Live money setting. An in-flight entry finishes its admission first. Open positions, orders, or an active trade defer replacement automatically; the running worker continues protection and exits.
5. When ready to switch, the updater holds new entries and reads the broker's current account, positions and orders directly. It replaces the app only after confirming no exposure or active trade. The replacement must verify its revision, account state, entry hold and unchanged owner permission before new entries resume. A failed replacement rolls back through the same checks.

The v2.2.0 user systemd timer checks thirty seconds after each completed run, with up to five seconds of jitter. Builds and tests take additional time. User lingering keeps this timer available after logout and reboot. Updates are serialized. An exclusive deployment lock prevents new entry submissions and Live activation during the switch. A durable hold keeps entries paused if the updater or host restarts; position management continues. Recovery verifies the running image and permission before removing that hold.

Version 2.2.1 also reconciles the timer when the worker already matches the selected GitHub revision. This handles a legacy updater that installed the new worker and updater but retained its old five-minute timer. A saved digest records a successful timer reload and restart; partial failures retry, while normal checks leave the timer untouched.

### One-time upgrade from the old updater

The installed v2.1.0 updater only refreshes itself after a completed rollout, and the v2.1.0 worker does not implement the entry-admission gate. Pushing v2.2.0 to GitHub alone cannot bootstrap that worker while Live is On. It requires a one-time host commissioning step with administrative access, or the legacy Off-and-flat rollout path. After both worker and updater are upgraded and verified, routine releases no longer require the owner to turn Live Off. Do not report a queued GitHub release as installed or remove the legacy gate without first replacing its missing entry-admission protection.

An existing policy change may still require the owner to review the new rules. The updater never grants that approval or changes the purchase target.

The bottom-right badge shows the installed app version and exact build ID (for example, `Installed v2.1.0 · abc1234`). A queued update appears separately and is explicitly marked as not installed. “Latest checked release” requires a recent update check that matches the running build; stale or unavailable checks remain unconfirmed. Refresh an older open page after an update to load its new interface.

## Files on AllSpark

- `~/pivot-trader/repository`: GitHub checkout used by the updater.
- `~/pivot-trader/releases/<commit>`: versioned source used for each image build.
- `~/pivot-trader/config/video.env`: private credentials; owner-readable only.
- `~/pivot-trader/data/pivot-v2/`: durable settings, order history, VIX request ledger and worker/deployment locks.
- `~/pivot-trader/config/`: installed updater and host configuration.
- `~/pivot-trader/data/pivot-v2/deployment-status.json`: update status shown in the hosted footer.
- `~/pivot-trader/data/pivot-v2/entry-hold.json`: durable entry pause during deployment or unresolved recovery; never delete it to bypass verification.

The earlier deployment and its Docker volume are retained for reference. They must remain stopped once the video app is active. The new and old apps must never manage the same account simultaneously.

## Operations

The Docker service publishes port 18011 only on AllSpark's loopback interface. Caddy reaches the app through the existing private Docker network and strips `/pivot` from upstream requests. The app validates the configured HTTPS origin for settings/live-control requests and rejects other origins. Hostname, prefix and deployed revision are explicit configuration.

The updater has no broker order client and never enables Live money. Candidate builds run the offline Python suite, interface model tests and frontend build before rollout. Account, order and VIX state are mounted separately from code releases so updates cannot reset their history or monthly data allowance.

The read-only `/api/deployment-readiness` endpoint supplies bounded, sanitized broker evidence for the updater. It must report the expected revision, entry-gate protocol, held lock, matching hold ID, fresh reads begun after lock acquisition, zero exposure, no active trade and the same saved-permission fingerprint. An unknown order state, failed broker read or unverified recovery leaves replacement or entry resumption blocked. The homepage distinguishes an update pause from a strategy setup wait.

This deployment supplies remote access and remote code updates. It does not establish profitable strategy performance or verified real-money fills. The app's existing data checks, exact purchase target rules, whole-share short restrictions and owner-controlled permission still apply.
