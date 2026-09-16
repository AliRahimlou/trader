# InsightSentry free VIX verification — September 16, 2026

## Result

The owner's new **FREE** account successfully returned actual `CBOE:VIX` metadata, a current quote, and 1,000 fifteen-minute OHLC candles. A second independent request sequence confirmed updates after a candle boundary. The production collector is now installed in the local app; the [completion audit](completion-audit.md) records its validation and remaining limits. The observations below describe the initial read-only verification.

The existing API key was saved in the git-ignored local `.env` as `INSIGHTSENTRY_API_KEY`, with owner-only file permissions. It is not included in the evidence files or application snapshots. No plan upgrade or purchase was made.

## Observed evidence

- Metadata identified `CBOE:VIX`, `INDEX`, CBOE Volatility Index, with `delay_seconds: 0`.
- First quote: **18.05**, source timestamp **19:36:31 UTC**, response timestamp **19:36:35.486 UTC**.
- Follow-up quote: **17.94**, source timestamp **19:45:31 UTC**, locally received by **19:45:38.463 UTC**: approximately **7.5 seconds old**.
- Both history responses explicitly returned `bar_type: 15m` and 1,000 observations, reaching back to August 19. All observations had ordered, unique, aligned timestamps and valid finite OHLC bounds.
- At 19:45:38 UTC, the latest completed regular-session candle ended at **19:45 UTC / 3:45 p.m. Eastern**. The newly forming 15:45–16:00 candle was excluded from completed history.
- The formerly forming 15:30–15:45 candle became completed with O 18.42, H 18.42, L 17.89, C 18.00. All **998 overlapping previously completed candles** remained unchanged between samples.
- Independent Alpaca `/v2/calendar` sessions from **September 2 through September 16** covered ten trading dates. The history check found **zero missing expected regular-session candles**. This range includes the Labor Day closure.
- The source includes observations outside the app's 09:30–16:00 Eastern analysis session even with `extended=false`; explicit local filtering is necessary. The observed `bar_end` used an exclusive next-boundary timestamp.

Eight InsightSentry data requests were issued across the two sequences (info, session, quotes, series), plus one read-only Alpaca calendar request. No broker order or Live money control was used.

## Cross-source comparison

The earlier CNBC sample and InsightSentry agreed across all four OHLC fields for **10 of 22** overlapping candles. All 22 closing values agreed. Twelve candles had differences, primarily in opening values; the largest was **0.46 VIX points** at 14:00 Eastern. Some highs/lows also differed by 0.01–0.05. The cause was not established. Do not describe the feeds as identical or silently splice their candles together.

## Reproducible diagnostics

`python -m pivot.insight_probe` makes exactly four fixed-host, read-only data requests and saves private timestamped evidence under `runtime/video-review-v2`. It stops on authentication, permission, redirect, rate-limit, malformed-data or size errors. The diagnostic has no broker connection or feed-enabling operation.

`pivot/insight_validation.py` checks identity, explicit delay metadata, source quote freshness, OHLC bounds, order, alignment, completed candles and gaps. Optional session-calendar input checks entirely missing sessions and session edges. Every output remains `execution_eligible: false` because this is a diagnostic.

Evidence:

- `runtime/video-review-v2/insightsentry-vix-verification-20260916.json`
- `runtime/video-review-v2/insightsentry-probe-20260916T194538470896Z.json`
- `runtime/video-review-v2/insightsentry-calendar-20260916.json`
- `runtime/video-review-v2/insightsentry-verified-summary-20260916.json`

Validation: **348 Python tests and 3 browser-model tests passed** using `npm test`; the automated test suite blocks live network calls.

## Ongoing use

The [free allowance](https://insightsentry.com/payment) is 1,000 requests/month and five/minute. A candle request after each regular-session 15-minute close is approximately 572 requests for 22 trading days, before metadata, quote checks, retries and opening refreshes. Continuous minute-by-minute quote plus history polling would exceed this allowance. The installed adapter uses durable caching, a persisted request budget, bounded retries and fresh quotes checked when an entry is actually being evaluated. It preserves timestamp/session checks rather than relabeling cached data as fresh.

The adapter uses `pivot/insight_cache.py` and `pivot/insight_data.py`; a 20:15:44 UTC production-path read confirmed 260 complete regular-session candles, zero missing candles and a final 16:00 Eastern candle. The backend was then restarted with the new policy version, which requires fresh owner review before new entries. No order or cancellation was used to validate this integration. Profitability and live order behavior remain unverified.
