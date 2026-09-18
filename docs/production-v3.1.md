# Pivot 3.1 — leader context and current entry evidence

September 18, 2026. This describes the release content. Installation is confirmed separately by the hosted app's version and revision badge.

## Changes visible in the app

- A one-hour/four-hour Nasdaq overview describes confirmed QQQ swing structure, including mixed conditions and evidence age. This is context, not a forecast or an additional trade veto.
- Leader readings distinguish up, down, neutral, missing, conflicting, expired and stale observations. Reaction age and the active participation rule are visible.
- The owner's selected rule requires at least five agreeing leaders and allows at most one opposing. Current observations must share the same completed candle timestamp; their qualifying reactions may have begun on different candles.
- Both entry methods remain independent. The purchase target and durable trade history are preserved.

## What the latest source review supports

The [main recording's public match](https://www.youtube.com/watch?v=DR6OFHaqUSw) shows Nvidia and Apple rejecting supply, followed by small green candles, during the bearish-confirmation discussion around 01:09–01:20. Direction describes the reaction and price location, rather than only the newest candle's color. The app already permits some green-candle continuation; these filmed examples do not establish a specific defect in its exact continuation formula.

Apple and Nvidia have five-minute chart headers; Microsoft has a fifteen-minute header, particularly clear around 02:24. The display does not establish a universal timeframe for all seven companies. The app retains its documented native five-minute leader interpretation.

Around 01:45–01:55, the current VIX sequence is falling while the creator discusses wanting upward confirmation of a prospective short. The later rising VIX example appears after a pan into older history. It is not evidence that the current setup had completed all conditions.

The separate [five-minute Nasdaq/VIX scalp](https://www.youtube.com/watch?v=egiUVtmGlZs) shows a local short after a Nasdaq rally into an upper reference area, and a later buy notification consistent with closing the displayed short. That example does not establish the account mode or net return, and no leader count is visible. It supports keeping broad direction descriptive rather than making it a universal trade-direction veto.

Neither sampled chart review nor the eleven reviewed complete public transcripts establishes an exact five-agree/one-opposing threshold, equal leader weights, a fixed reaction lifetime, or an exact colored-area formula. Five/one remains the owner's selected app interpretation. This review did not cover every video on the channel. General candlestick and indicator references do not add new mandatory entry conditions.

## Reliability changes

Entry admission now expires saved opportunities at the earliest applicable reaction, candle-publication, data or quote deadline. Refreshing a snapshot cannot extend old evidence. Valid expired or consumed candidates cannot hide another current candidate. Transactional reservation and claim checks prevent stale workers from retiring or submitting a changed order intent; only proven never-attempted reservations may be retried. Uncertain or attempted orders remain subject to reconciliation.

Data collection fetches the frames actually required by the two methods, with exchange-calendar publication checks. Saved diagnostics record the participation rule, observation times, expiry and market context. See the [implementation audit](research/leader-context-implementation-20260918.md) for detailed changes and limitations.

## Release verification and activation

The application passed 1,095 network-denied Python tests, 40 browser-model tests, a production frontend build and synthetic desktop/mobile review. GitHub and AllSpark also build the tested Linux image. Simulated-broker tests do not establish actual fills or profitability.

The execution-policy identifier changes to `nasdaq-qqq-execution-v5-five-leader-majority`. The updater preserves saved permission but does not approve replacement rules. An approval saved for the previous policy therefore produces **Review updated live rules**, and new entries remain disabled until the owner reviews the new policy in the app. Deployment does not change the saved purchase amount or submit a commissioning trade.

The paired historical comparison added no fully qualifying observations under five/one and removed four; repeated observations are not trades. It was previously inspected development data, not unseen validation, and does not establish an improvement in returns. Small fractional purchase targets still cannot open QQQ short positions.
