# Intraday evaluation and daily review

## Objective and scope

The owner wants day trading and scalping, with small purchases that allow repeated evaluation. Slower multi-day strategy candidates are outside this research plan. Evaluate net returns after costs and the size of losses, not trade frequency or win rate alone. No return target or exponential growth is assumed.

Existing Socrates and 4H Range Reversal are separate baselines. Saved purchase sizes, markets and live permissions remain the owner's choices. A daily report cannot submit an order, enlarge a purchase, change a signal, add a market or alter a stop.

## What a daily report means

- New York calendar days are the reporting boundary for both families. A report after midnight describes the previous day's retained evidence; crypto itself runs continuously.
- Completed outcomes are assigned to their completion day, including positions opened on earlier days. Broker fill timestamps determine entry-day counts. Missing timestamps remain unknown.
- Gross outcomes, observed account-wide fees and verified net outcomes are different fields. Unlinked daily fees must not be arbitrarily allocated to one strategy. Alpaca says crypto fee activities may arrive after the trading day; completed-day reports can receive evidence revisions. [Alpaca crypto fees](https://docs.alpaca.markets/us/docs/crypto-fees)
- Captured equity movement is distinct from realized trading profit. Deposits and withdrawals require separate treatment; missing or date-only transfer timing may make an interval adjustment uncertain.
- Repeated polling is not another independent trade. Retained decision counts explain recorded checks, not full-day data coverage or opportunity independence.
- A no-trade day is an observation to investigate. It is not a profitable day, a software failure by itself, or permission to loosen the entry rules.
- Entry and exit reasons are recorded facts. A target or stop explains the execution trigger; it does not prove a causal explanation for the market's movement.

## Daily research procedure

1. Read the deployed version, reporting freshness and account/data/worker status. Compare the current report with its previous saved revision.
2. Reconcile broker observations with app-owned orders, fills and outcomes. Flag unknown submissions, incomplete protection, missing fees and unexplained cash flows separately.
3. For each family, inspect the most important entry blockers, completed trades and data interruptions. Preserve both winners and losers; do not select only favorable examples.
4. Separate bugs from strategy outcomes. Reproduce an implementation defect with a focused test. A losing trade that followed the defined rules is not automatically a defect.
5. Record a hypothesis, source, expected mechanism, required data, costs and evaluation periods before testing a rule change. Keep failed hypotheses in the register.
6. Report whether new evidence supports a change, contradicts it, or is insufficient. Do not silently change live signals based on daily hindsight.

## Declared candidate work

The initial research queue remains narrow:

| Candidate | Comparison | Required evidence |
| --- | --- | --- |
| Existing Socrates methods | Prior-day sweep and higher-timeframe retest, evaluated separately | Native source-identified QQQ, leader and actual VIX data; subsequent executable prices within entry deadlines |
| Faster closed-five-minute entries | Existing F1 implementation versus its frozen baseline | Native five-minute QQQ/VIX history, unchanged confirmation requirements, declared arrival delays and costs |
| VIX-based exits | Existing X1 matched exits versus baseline exits on the same entries | Actual VIX data available at each decision; existing stock protection remains modeled |
| 4H Range Reversal | Frozen midnight-New-York range and declared first-outside-candle stop | Venue-specific five-minute bars, executable quotes, fee quantities, partial/no fills and overnight outcomes |

An explicit same-day exit/time limit for crypto is a separate intraday research candidate. The current position manager can hold a protected position beyond midnight while awaiting its exit. Reporting dates must not be mistaken for an enforced liquidation rule. Any time-limit change requires its own precise cutoff, cost model and execution tests; this reporting release does not change exits.

## Evaluation standards

Use chronological development and later evaluation periods, with a record of every tried variation. Already-inspected data cannot be relabeled untouched. Include commissions/fees, spread, latency, partial orders, missed fills and conservative ambiguous-price treatment. Group correlated outcomes appropriately; repeated signals across related coins do not create independent evidence.

Assess net return, average winner and loser, drawdown, exposure, turnover, uncertainty and sensitivity to worse costs. Compare against suitable cash and market-exposure benchmarks. Check whether a single day supplies most gains. The previous project's sample minimums are screening criteria, not a universal proof threshold.

These procedures follow the reasoning in [Backtesting Protocol](https://people.duke.edu/~charvey/Research/Published_Papers/SSRN-id3275654.pdf) and [The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf). They do not guarantee a profitable candidate.

## Operations

AllSpark generates and persists factual reports independently of the desktop assistant. The separate assistant review is a scheduled research follow-up and requires its host and application to be available. It may investigate primary sources, preserve a bounded offline experiment and report findings. It must not reinterpret a scheduled review as permission to place/cancel orders, increase size, buy data or deploy untested signal changes.
