# AllSpark hosting and remote updates

## Access

The hosted app is at **https://media.mytap.net/pivot/**. The existing password-protected Caddy route is retained. The public app and its account/API routes require the same login. The service connects outbound to Alpaca and InsightSentry; API keys stay in private configuration on AllSpark and are never committed to GitHub or built into the browser.

AllSpark runs the worker independently of the laptop, with automatic container restart. AllSpark itself and its internet connection must remain available. The laptop trading worker is retired during migration to prevent duplicate account management. The hosted installation starts with Live money Off; enable it yourself in the hosted interface after reviewing the rules.

## Make changes from any network

1. Edit the repository at https://github.com/AliRahimlou/trader from GitHub, another computer, or a coding workspace.
2. Commit the desired changes to `main`. A pull request is useful for reviewing larger changes first.
3. GitHub runs the checks. AllSpark independently fetches `main` through an outbound HTTPS connection and builds a tested release. No local Wi-Fi, Tailscale, inbound SSH access, or GitHub runner access to broker keys is required.
4. If Live money is On, or any position/order remains, the update waits. Turn Live money Off from the hosted app and wait for the current position to finish. Off does not abandon an existing managed position.
5. The updater deploys only after a fresh account snapshot confirms it is safe to switch. It checks the running Git revision and health after the change. An unhealthy release rolls back to the previous image. Live money stays Off until you enable it again.

AllSpark checks periodically through a user systemd timer. User lingering keeps this timer available after logout and reboot. An exclusive deployment lock prevents the Live money control from enabling execution during the final switch. Updates are serialized.

## Files on AllSpark

- `~/pivot-trader/repository`: GitHub checkout used by the updater.
- `~/pivot-trader/releases/<commit>`: versioned source used for each image build.
- `~/pivot-trader/config/video.env`: private credentials; owner-readable only.
- `~/pivot-trader/data/pivot-v2/`: durable settings, order history, VIX request ledger and worker/deployment locks.
- `~/pivot-trader/config/`: installed updater and host configuration.
- `~/pivot-trader/data/pivot-v2/deployment-status.json`: update status shown in the hosted footer.

The earlier deployment and its Docker volume are retained for reference. They must remain stopped once the video app is active. The new and old apps must never manage the same account simultaneously.

## Operations

The Docker service publishes port 18011 only on AllSpark's loopback interface. Caddy reaches the app through the existing private Docker network and strips `/pivot` from upstream requests. The app validates the configured HTTPS origin for settings/live-control requests and rejects other origins. Hostname, prefix and deployed revision are explicit configuration.

The updater has no broker order client and never enables Live money. Candidate builds run the offline Python suite, interface model tests and frontend build before rollout. Account, order and VIX state are mounted separately from code releases so updates cannot reset their history or monthly data allowance.

This deployment supplies remote access and remote code updates. It does not establish profitable strategy performance or verified real-money fills. The app's existing data checks, exact purchase target rules, whole-share short restrictions and owner-controlled permission still apply.
